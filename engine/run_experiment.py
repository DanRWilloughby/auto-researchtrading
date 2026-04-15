"""
run_experiment.py — enforced-logging wrapper around engine.prepare.run_backtest.

USE THIS from any script that runs backtests outside the engine/backtest.py CLI.
Guarantees every run is logged to <strategy_dir>/results.tsv so we don't lose
experiment data in shell scrollback.

Per project rule (strategies/.../CLAUDE.md): every backtest result must be
logged. The CLI (engine/backtest.py) logs automatically. Direct run_backtest()
calls do NOT — use this wrapper instead.

Example:
    from engine.run_experiment import run_experiment

    result = run_experiment(
        strategy=my_strategy_instance,
        data=loaded_data_dict,
        label="filter-E5-only-1323-UTC",
        notes="Hard time cut: trade only 13-23 UTC. n=944d",
        strategy_path="/path/to/strategy.py",   # for results.tsv location
        split="custom-944d",
        interval="30m",
        taker_fee=0.0006,
        slippage_bps=1.0,
        execute_delay=1,
    )
"""
import os
import sys

# Ensure prepare / backtest modules are importable when called from scripts
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_ENGINE_DIR)
for p in (_PROJECT_ROOT, _ENGINE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from prepare import run_backtest, compute_score
from backtest import log_result


def run_experiment(*, strategy, data, label, notes, strategy_path,
                   split="custom", interval="30m",
                   slippage_bps=None, taker_fee=None,
                   execute_delay=0, short_borrow_rate=0.0,
                   eod_flatten=False, sync_vm=True):
    """
    Run a backtest and ALWAYS log the result to results.tsv.

    Required:
        strategy:      instantiated Strategy (or strategy-like wrapper)
        data:          dict[symbol -> dataframe], as returned by prepare.load_data
        label:         unique experiment label (appears in results.tsv 'exp' column)
        notes:         free-text context for the run
        strategy_path: absolute or project-relative path to the strategy.py file.
                       results.tsv is written to the same directory.

    Optional:
        split:         string tag for 'split' column (default 'custom')
        interval:      bar interval, must be in engine.prepare.INTERVAL_CONFIG
        slippage_bps, taker_fee, execute_delay, short_borrow_rate, eod_flatten:
                       passed through to run_backtest
        sync_vm:       if True (default), log_result also rsyncs the updated
                       results.tsv to the VM for dashboard consumption.

    Returns:
        BacktestResult from run_backtest.

    Raises:
        ValueError if label or notes is empty — we require context on every run.
    """
    if not label or not str(label).strip():
        raise ValueError("run_experiment requires a non-empty 'label'")
    if not notes or not str(notes).strip():
        raise ValueError("run_experiment requires a non-empty 'notes' string "
                         "(what are you testing? why?)")

    # Resolve strategy_path to absolute for log_result
    sp = strategy_path if os.path.isabs(strategy_path) else os.path.join(_PROJECT_ROOT, strategy_path)

    # Run the backtest
    result = run_backtest(strategy, data, interval=interval,
                          slippage_bps=slippage_bps, taker_fee=taker_fee,
                          execute_delay=execute_delay,
                          short_borrow_rate=short_borrow_rate,
                          eod_flatten=eod_flatten)

    # Score = Sharpe (engine convention)
    score = compute_score(result)

    symbols_used = sorted(data.keys())

    # Temporarily disable VM sync if caller asked for it
    if not sync_vm:
        import backtest as _bt
        original_sync = _bt.sync_to_vm
        _bt.sync_to_vm = lambda *a, **kw: None
        try:
            log_result(sp, label, split, score, result, symbols_used, notes, _PROJECT_ROOT)
        finally:
            _bt.sync_to_vm = original_sync
    else:
        log_result(sp, label, split, score, result, symbols_used, notes, _PROJECT_ROOT)

    return result
