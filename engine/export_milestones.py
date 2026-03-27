#!/usr/bin/env python3
"""Export equity curves at key autoresearch milestones.

Checks out each milestone commit's strategy.py, validates it via AST,
runs the backtest, saves the equity CSV, then restores the original.

Security: Uses secure temp files and AST validation before execution.
"""
import csv
import subprocess
import sys
import tempfile
import os
import importlib
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

MILESTONES = [
    ("d779d69", "baseline",  "Exp 0: Baseline momentum (score 2.7)"),
    ("edabd44", "exp15",     "Exp 15: 4/5 ensemble + cooldown (score 8.4)"),
    ("31600ce", "exp46",     "Exp 46: Remove strength scaling (score 13.5)"),
    ("61d4b77", "exp72",     "Exp 72: RSI period 8 (score 19.7)"),
    ("7245f22", "exp102",    "Exp 102: Final strategy (score 20.6)"),
]

STRATEGY_FILE = Path(__file__).parent / "strategy.py"


def run_cmd(cmd):
    """Run a shell command, return CompletedProcess."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
    return r


def export_equity_for_commit(commit, label, desc):
    """Checkout commit's strategy, validate, run backtest, save equity CSV."""
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"  Commit: {commit}")
    print(f"{'='*60}")

    # Extract strategy.py from the commit to a secure temp file
    result = run_cmd(f"git show {commit}:strategy.py")
    if result.returncode != 0:
        print(f"  Failed to extract strategy from commit {commit}")
        return False

    strategy_code = result.stdout

    # Write to secure temp file (not world-writable /tmp)
    fd, tmppath = tempfile.mkstemp(suffix=".py", prefix="strategy_milestone_")
    try:
        os.write(fd, strategy_code.encode())
        os.close(fd)

        # Validate via AST before execution
        from backtest_sandboxed import validate_strategy_ast
        violations = validate_strategy_ast(Path(tmppath))
        if violations:
            print(f"  BLOCKED — security violations in {commit}:")
            for v in violations:
                print(f"    {v}")
            return False

        # Safe to copy and execute
        import shutil
        shutil.copy(tmppath, str(STRATEGY_FILE))

    finally:
        # Always clean up temp file
        try:
            os.unlink(tmppath)
        except OSError:
            pass

    try:
        # Force fresh import — clear bytecache and module cache
        pycache = Path("__pycache__")
        if pycache.exists():
            for f in pycache.glob("strategy*.pyc"):
                f.unlink()

        # Use spec-based import for clean reload
        if "strategy" in sys.modules:
            del sys.modules["strategy"]

        spec = importlib.util.spec_from_file_location("strategy", str(STRATEGY_FILE))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        Strategy = mod.Strategy

        from prepare import load_data, run_backtest

        strategy = Strategy()
        data = load_data("val")
        result = run_backtest(strategy, data)

        outfile = f"equity_curve_{label}.csv"
        start = datetime(2024, 7, 1)
        timestamps = [start + timedelta(hours=i) for i in range(len(result.equity_curve))]

        with open(outfile, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "equity"])
            for ts, eq in zip(timestamps, result.equity_curve):
                w.writerow([ts.strftime("%Y-%m-%d %H:%M"), f"{eq:.2f}"])

        print(f"  Exported {len(result.equity_curve)} points -> {outfile}")
        print(f"    Start: ${result.equity_curve[0]:,.2f}")
        print(f"    End:   ${result.equity_curve[-1]:,.2f}")
        print(f"    Return: {result.total_return_pct:.1f}%")
        print(f"    Sharpe: {result.sharpe:.2f}")
        return True

    except Exception as e:
        print(f"  Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    # Save current strategy.py with secure copy
    backup_fd, backup_path = tempfile.mkstemp(suffix=".py", prefix="strategy_backup_")
    os.close(backup_fd)
    import shutil
    shutil.copy(str(STRATEGY_FILE), backup_path)

    try:
        for commit, label, desc in MILESTONES:
            export_equity_for_commit(commit, label, desc)
    finally:
        # Restore original strategy.py
        shutil.copy(backup_path, str(STRATEGY_FILE))
        os.unlink(backup_path)
        print("\nRestored original strategy.py")

    print("\nAll milestone equity curves exported!")


if __name__ == "__main__":
    main()
