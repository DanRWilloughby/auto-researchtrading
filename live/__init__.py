"""
Live trading loop.

Standalone from paper/trader.py — does NOT modify or import from it.
Runs on the same schedule and same strategy file, but executes real
orders on Coinbase via the exchange abstraction layer, with the risk
manager wrapping every signal.

Components imported (read-only):
  - engine.prepare: type definitions (BarData, Signal, PortfolioState)
  - exchanges.CoinbaseClient: live execution
  - risk.RiskManager: safety layer
  - Strategy file: same one paper trader uses

Files written:
  - live/state/<strategy>_live_state.json: persistent state across ticks
  - live/logs/<strategy>-cron.log: execution log (redirected from cron)
  - live/logs/trades_YYYY-MM-DD.jsonl: daily trade log
"""
