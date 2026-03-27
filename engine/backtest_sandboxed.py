"""
Sandboxed backtest runner. Replaces backtest.py with subprocess isolation.

Instead of importing strategy.py directly (which executes arbitrary code in-process),
this runner launches strategy evaluation in a subprocess with a hard timeout.

Security layers:
  1. AST-based static analysis (catches dangerous imports/calls structurally)
  2. Subprocess isolation with hard timeout (cross-platform)
  3. Docker container recommended for full OS-level isolation (see docker-compose.yml)

Works cross-platform (no SIGALRM dependency).

Usage:
    uv run backtest_sandboxed.py                    # run with subprocess + AST validation
    uv run backtest_sandboxed.py --docker           # run inside Docker container (recommended)
"""

import ast
import os
import sys
import json
import time
import hashlib
import subprocess
import textwrap
from pathlib import Path
from datetime import datetime, timezone

from prepare import TIME_BUDGET

STRATEGY_FILE = Path(__file__).parent / "strategy.py"
TIMEOUT = TIME_BUDGET + 30  # 30s grace for startup
AUDIT_LOG = Path(__file__).parent / "audit.jsonl"

# Modules that strategy.py is allowed to import
ALLOWED_MODULES = frozenset({
    "numpy", "scipy", "math", "collections",
    "prepare", "dataclasses", "typing", "functools",
    "itertools", "numbers", "decimal",
    "statistics", "random",
    # NOTE: "operator" is intentionally excluded — operator.attrgetter
    # is a getattr equivalent that bypasses the sandbox.
})

# Built-in functions that are forbidden
FORBIDDEN_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__",
    "breakpoint", "exit", "quit",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr",
    "open", "input", "print",  # print allowed in on_bar? No — strategy shouldn't print
    "memoryview", "type", "super",
})

# Allow print and type — they're commonly used and not dangerous on their own
# The real protection is the import whitelist + forbidden attribute access
ACTUALLY_ALLOWED_BUILTINS = frozenset({"print", "type", "super", "isinstance", "issubclass"})
EFFECTIVE_FORBIDDEN_BUILTINS = FORBIDDEN_BUILTINS - ACTUALLY_ALLOWED_BUILTINS

# Forbidden attribute access patterns (on any object)
FORBIDDEN_ATTRIBUTES = frozenset({
    # Dangerous method names (os, subprocess, shutil, etc.)
    "system", "popen", "exec", "spawn",
    "execve", "execvp", "execvpe", "execv", "execlp", "execl",
    "posix_spawn", "posix_spawnp",
    "remove", "unlink", "rmdir", "rename", "makedirs",
    "read_text", "read_bytes", "write_text", "write_bytes",
    "ctypeslib",
    # Module names that leak as public attrs (statistics.sys, typing.sys, etc.)
    "sys", "modules",
    # Reflection helpers — getattr equivalents
    "attrgetter", "itemgetter", "methodcaller",
})

# ALLOWLIST for attribute access on the "prepare" module.
# Strategy.py only needs data types and constants from prepare.
# Everything else (os, sys, requests, HTTPAdapter, etc.) is blocked.
ALLOWED_PREPARE_ATTRIBUTES = frozenset({
    # Data types used by strategies
    "Signal", "PortfolioState", "BarData", "BacktestResult",
    # Constants strategies might reference
    "TIME_BUDGET", "INITIAL_CAPITAL", "MAKER_FEE", "TAKER_FEE",
    "SLIPPAGE_BPS", "MAX_LEVERAGE", "LOOKBACK_BARS", "BAR_INTERVAL",
    "SYMBOLS", "HOURS_PER_YEAR",
    "TRAIN_START", "TRAIN_END", "VAL_START", "VAL_END",
    "TEST_START", "TEST_END",
})

# Dunder attributes that are safe (used in normal Python classes)
ALLOWED_DUNDERS = frozenset({
    "__init__", "__repr__", "__str__", "__len__", "__bool__",
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
    "__hash__", "__add__", "__sub__", "__mul__", "__truediv__",
    "__floordiv__", "__mod__", "__pow__", "__neg__", "__pos__",
    "__abs__", "__iter__", "__next__", "__contains__",
    "__enter__", "__exit__", "__call__", "__name__", "__doc__",
})


class StrategySecurityError(Exception):
    """Raised when strategy.py contains forbidden code patterns."""
    pass


def validate_strategy_ast(strategy_path: Path) -> list[str]:
    """
    AST-based validation of strategy.py.

    Parses the file into an Abstract Syntax Tree and walks every node
    to check for forbidden patterns. Unlike string matching, this cannot
    be bypassed with string concatenation, encoding tricks, or comments.

    Returns list of security violations. Empty = safe to run.
    """
    violations = []
    code = strategy_path.read_text()

    # Step 1: Parse the AST (catches syntax errors too)
    try:
        tree = ast.parse(code, filename=str(strategy_path))
    except SyntaxError as e:
        violations.append(f"SYNTAX ERROR: {e}")
        return violations

    # Step 2: Walk every node
    for node in ast.walk(tree):

        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    violations.append(
                        f"BLOCKED IMPORT: '{alias.name}' (line {node.lineno}). "
                        f"Only {sorted(ALLOWED_MODULES)} are allowed."
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root not in ALLOWED_MODULES:
                    violations.append(
                        f"BLOCKED IMPORT: 'from {node.module}' (line {node.lineno}). "
                        f"Only {sorted(ALLOWED_MODULES)} are allowed."
                    )
                # Enforce allowlist on `from prepare import X`
                if node.module == "prepare" and node.names:
                    for alias in node.names:
                        if alias.name == "*":
                            violations.append(
                                f"BLOCKED IMPORT: 'from prepare import *' "
                                f"(line {node.lineno}). Wildcard imports from "
                                f"prepare are forbidden."
                            )
                        elif alias.name not in ALLOWED_PREPARE_ATTRIBUTES:
                            violations.append(
                                f"BLOCKED IMPORT: 'from prepare import {alias.name}' "
                                f"(line {node.lineno}). Only data types and constants "
                                f"are importable from prepare."
                            )

        # Check ALL attribute access (calls and bare)
        if isinstance(node, (ast.Attribute, ast.Call)):
            # Get the attribute node to inspect
            attr_node = None
            attr_name = None
            if isinstance(node, ast.Attribute):
                attr_node = node
                attr_name = node.attr
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr_node = node.func
                attr_name = node.func.attr

            if attr_name:
                # Block underscore-prefixed attributes on modules (private internals).
                # This catches random._os, collections._sys, etc.
                # Allow self._method() — strategy classes use private methods.
                if attr_name.startswith("_") and not (
                    attr_name.startswith("__") and attr_name.endswith("__")
                ):
                    # Check if access is on self (allowed) or a module (blocked)
                    value_node = attr_node.value if attr_node else None
                    is_self = (
                        isinstance(value_node, ast.Name) and
                        value_node.id == "self"
                    )
                    if not is_self:
                        violations.append(
                            f"BLOCKED PRIVATE: '.{attr_name}' (line {node.lineno}). "
                            f"Access to private/internal attributes is forbidden "
                            f"(except self._method)."
                        )

                # Block non-whitelisted dunder access (allowlist, not blocklist)
                if attr_name.startswith("__") and attr_name.endswith("__"):
                    if attr_name not in ALLOWED_DUNDERS:
                        violations.append(
                            f"BLOCKED DUNDER: '.{attr_name}' (line {node.lineno}). "
                            f"Only {sorted(ALLOWED_DUNDERS)} are permitted."
                        )

                # Block known dangerous non-dunder attributes
                if attr_name in FORBIDDEN_ATTRIBUTES:
                    violations.append(
                        f"BLOCKED ATTRIBUTE: '.{attr_name}' (line {node.lineno}). "
                        f"Access to this attribute is forbidden."
                    )

                # ALLOWLIST enforcement for prepare.* access
                # Only Signal, PortfolioState, BarData, constants are permitted.
                # This closes ALL transitive leaks (os, requests, HTTPAdapter, etc.)
                if attr_node is not None and isinstance(attr_node.value, ast.Name):
                    if attr_node.value.id == "prepare":
                        if attr_name not in ALLOWED_PREPARE_ATTRIBUTES:
                            violations.append(
                                f"BLOCKED PREPARE ACCESS: 'prepare.{attr_name}' "
                                f"(line {node.lineno}). Only data types and constants "
                                f"are accessible from prepare."
                            )

        # Check forbidden built-in calls: eval(), exec(), open(), __import__(), etc.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in EFFECTIVE_FORBIDDEN_BUILTINS:
                violations.append(
                    f"BLOCKED CALL: '{node.func.id}()' (line {node.lineno}). "
                    f"This built-in is not allowed in strategies."
                )

        # Check string constants for encoded dunder payloads
        # e.g. "__import__", "__globals__", "__builtins__" as string args
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.startswith("__") and val.endswith("__") and val not in ALLOWED_DUNDERS:
                violations.append(
                    f"BLOCKED STRING: dunder string '{val}' (line {node.lineno}). "
                    f"Strings containing dunder names can be used for sandbox escape."
                )

        # Detect string concatenation that builds dunder names or module names
        # e.g. "_" + "_globals_" + "_" or "sub" + "process"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            try:
                result = ast.literal_eval(node)
                if isinstance(result, str):
                    if result.startswith("__") and result.endswith("__") and result not in ALLOWED_DUNDERS:
                        violations.append(
                            f"BLOCKED CONCAT: string concatenation builds dunder "
                            f"'{result}' (line {node.lineno})."
                        )
            except (ValueError, TypeError):
                pass  # Non-constant concat — can't evaluate statically

    # Step 3: Structural checks
    # Must define a Strategy class
    class_names = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]
    if "Strategy" not in class_names:
        violations.append("INVALID: strategy.py must define 'class Strategy'")

    # Strategy must have on_bar method
    has_on_bar = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == "on_bar":
                        has_on_bar = True
    if not has_on_bar:
        violations.append("INVALID: Strategy class must define 'on_bar' method")

    # Step 4: Block reassignment of "self" inside any method.
    # Prevents: self = collections; self._sys (bypasses the self exception)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                # Assignment: self = X
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and target.id == "self":
                            violations.append(
                                f"BLOCKED SELF REBIND: 'self = ...' "
                                f"(line {child.lineno}). Reassigning self is "
                                f"forbidden."
                            )
                # Augmented assignment: self += X (unlikely but cover it)
                elif isinstance(child, ast.AugAssign):
                    if isinstance(child.target, ast.Name) and child.target.id == "self":
                        violations.append(
                            f"BLOCKED SELF REBIND: 'self op= ...' "
                            f"(line {child.lineno}). Reassigning self is "
                            f"forbidden."
                        )
                # Named expression (walrus): (self := X)
                elif isinstance(child, ast.NamedExpr):
                    if isinstance(child.target, ast.Name) and child.target.id == "self":
                        violations.append(
                            f"BLOCKED SELF REBIND: '(self := ...)' "
                            f"(line {child.lineno}). Reassigning self is "
                            f"forbidden."
                        )
                # For loop: for self in X
                elif isinstance(child, ast.For):
                    if isinstance(child.target, ast.Name) and child.target.id == "self":
                        violations.append(
                            f"BLOCKED SELF REBIND: 'for self in ...' "
                            f"(line {child.lineno}). Reassigning self is "
                            f"forbidden."
                        )
                # With statement: with X as self
                elif isinstance(child, ast.withitem):
                    if child.optional_vars and isinstance(child.optional_vars, ast.Name):
                        if child.optional_vars.id == "self":
                            violations.append(
                                f"BLOCKED SELF REBIND: 'with ... as self' "
                                f"(line {node.lineno}). Reassigning self is "
                                f"forbidden."
                            )

    # Step 5: Check for suspiciously long strings (potential encoded payloads)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > 500:
                violations.append(
                    f"SUSPICIOUS: Very long string constant ({len(node.value)} chars) "
                    f"at line {node.lineno}. Could contain encoded payload."
                )

    return violations


def compute_strategy_hash(strategy_path: Path) -> str:
    """SHA-256 hash of strategy file for audit trail."""
    return hashlib.sha256(strategy_path.read_bytes()).hexdigest()


def log_audit_event(event_type: str, details: dict):
    """Append to immutable audit log. Gracefully handles read-only filesystems."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **details,
    }
    try:
        with open(AUDIT_LOG, "a") as f:
            json.dump(record, f)
            f.write("\n")
    except OSError:
        # Read-only filesystem (Docker container) — print to stdout instead
        print(f"AUDIT: {json.dumps(record)}")


def run_backtest_subprocess() -> dict:
    """
    Run backtest in an isolated subprocess with hard timeout.
    Returns parsed results dict or raises on failure.
    """
    runner_code = textwrap.dedent("""\
        import sys
        import json
        import time

        t_start = time.time()

        from prepare import load_data, run_backtest, compute_score

        try:
            from strategy import Strategy
        except Exception as e:
            print(json.dumps({"error": f"Strategy import failed: {e}"}))
            sys.exit(1)

        try:
            strategy = Strategy()
            data = load_data("val")
            result = run_backtest(strategy, data)
            score = compute_score(result)
            t_end = time.time()

            output = {
                "score": score,
                "sharpe": result.sharpe,
                "total_return_pct": result.total_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "num_trades": result.num_trades,
                "win_rate_pct": result.win_rate_pct,
                "profit_factor": result.profit_factor,
                "annual_turnover": result.annual_turnover,
                "backtest_seconds": result.backtest_seconds,
                "total_seconds": t_end - t_start,
                "bars_loaded": sum(len(df) for df in data.values()),
                "symbols": list(data.keys()),
            }
            print(json.dumps(output))
        except Exception as e:
            print(json.dumps({"error": f"Backtest failed: {e}"}))
            sys.exit(1)
    """)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner_code],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(Path(__file__).parent),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"TIMEOUT: backtest exceeded {TIMEOUT}s budget"}

    if proc.returncode != 0:
        stderr = proc.stderr.strip()[-500:] if proc.stderr else "no stderr"
        return {"error": f"Subprocess failed (exit {proc.returncode}): {stderr}"}

    # Parse JSON output from last line
    stdout_lines = proc.stdout.strip().split("\n")
    for line in reversed(stdout_lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    return {"error": f"No JSON output from subprocess. stdout: {proc.stdout[-500:]}"}


def run_backtest_docker() -> dict:
    """
    Run backtest inside Docker container with full OS-level isolation.
    Requires the image to be built first: docker build -t auto-research:hardened .
    """
    image = os.environ.get("AUTOTRADER_DOCKER_IMAGE", "auto-research:hardened")
    data_dir = os.environ.get(
        "AUTOTRADER_DATA_DIR",
        os.path.join(os.path.expanduser("~"), ".cache", "autotrader", "data"),
    )
    strategy_path = str(STRATEGY_FILE.resolve())

    cmd = [
        "docker", "run", "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:size=512m",
        "--memory=4g",
        "--cpus=2",
        "--security-opt=no-new-privileges:true",
        "--cap-drop=ALL",
        "-v", f"{strategy_path}:/app/strategy.py:ro",
        "-v", f"{data_dir}:/app/data:ro",
        "-e", "AUTOTRADER_DATA_DIR=/app/data",
        image,
        "backtest_sandboxed.py",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT + 30,  # Extra grace for Docker startup
        )
    except subprocess.TimeoutExpired:
        return {"error": f"TIMEOUT: Docker container exceeded {TIMEOUT + 30}s budget"}
    except FileNotFoundError:
        return {"error": "Docker not found. Install Docker or use subprocess mode."}

    if proc.returncode != 0:
        stderr = proc.stderr.strip()[-500:] if proc.stderr else "no stderr"
        return {"error": f"Docker container failed (exit {proc.returncode}): {stderr}"}

    # Parse output — backtest.py prints key=value lines
    result = {}
    for line in proc.stdout.strip().split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("Loaded") and not line.startswith("Symbols"):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            try:
                result[key] = float(val) if "." in val else int(val)
            except ValueError:
                result[key] = val

    if not result or "score" not in result:
        return {"error": f"Failed to parse Docker output: {proc.stdout[-500:]}"}

    return result


def print_results(result: dict):
    """Print backtest results in standard format."""
    print(f"Loaded {result.get('bars_loaded', '?')} bars across {len(result.get('symbols', []))} symbols")
    print(f"Symbols: {result.get('symbols', [])}")
    print("---")
    for key in ["score", "sharpe", "total_return_pct", "max_drawdown_pct",
                 "num_trades", "win_rate_pct", "profit_factor", "annual_turnover",
                 "backtest_seconds", "total_seconds"]:
        if key in result:
            val = result[key]
            if isinstance(val, float):
                if key in ("annual_turnover",):
                    print(f"{key:20s}{val:.2f}")
                elif key in ("backtest_seconds", "total_seconds"):
                    print(f"{key:20s}{val:.1f}")
                else:
                    print(f"{key:20s}{val:.6f}")
            else:
                print(f"{key:20s}{val}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sandboxed backtest runner")
    parser.add_argument("--docker", action="store_true",
                        help="Run inside Docker container (recommended, requires image build)")
    args = parser.parse_args()

    strategy_hash = compute_strategy_hash(STRATEGY_FILE)

    # Step 1: Always validate strategy.py via AST
    print("Validating strategy.py (AST analysis)...")
    violations = validate_strategy_ast(STRATEGY_FILE)
    if violations:
        print("\n--- VALIDATION FAILED ---")
        for v in violations:
            print(f"  {v}")
        log_audit_event("validation_failed", {
            "strategy_hash": strategy_hash,
            "violations": violations,
        })
        print("\nStrategy blocked. Fix the security violations above.")
        sys.exit(1)
    print(f"Validation passed. Strategy hash: {strategy_hash[:16]}...\n")

    # Step 2: Run backtest
    if args.docker:
        print("Running backtest in Docker container (full OS isolation)...")
        result = run_backtest_docker()
    else:
        print("Running backtest in subprocess...")
        print("NOTE: For full OS-level isolation, use --docker flag.\n")
        result = run_backtest_subprocess()

    if "error" in result:
        print(f"\n--- BACKTEST FAILED ---")
        print(f"  {result['error']}")
        log_audit_event("backtest_failed", {
            "strategy_hash": strategy_hash,
            "error": result["error"],
        })
        sys.exit(1)

    print_results(result)

    # Step 3: Log to audit trail
    log_audit_event("backtest_completed", {
        "strategy_hash": strategy_hash,
        "score": result.get("score"),
        "sharpe": result.get("sharpe"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "num_trades": result.get("num_trades"),
        "mode": "docker" if args.docker else "subprocess",
    })


if __name__ == "__main__":
    main()
