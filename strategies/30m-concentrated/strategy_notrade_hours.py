"""
Skips trading during 02:00-08:00 UTC (low-volume chop hours).
Closes any open positions at 02:00 and doesn't re-enter until 08:00.
"""
import importlib.util
from pathlib import Path
from datetime import datetime, timezone

_base_path = Path(__file__).parent / "strategy.py"
_spec = importlib.util.spec_from_file_location("base_strategy", str(_base_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Import Signal from the same place the base strategy does
from prepare import Signal

NO_TRADE_START = 2   # UTC hour
NO_TRADE_END = 8     # UTC hour


class Strategy(_mod.Strategy):
    def on_bar(self, bar_data, portfolio):
        # Check current UTC hour from the latest bar timestamp
        current_hour = None
        for sym, bd in bar_data.items():
            ts_sec = bd.timestamp / 1000 if bd.timestamp > 1e12 else bd.timestamp
            current_hour = datetime.fromtimestamp(ts_sec, tz=timezone.utc).hour
            break

        if current_hour is not None and NO_TRADE_START <= current_hour < NO_TRADE_END:
            # During no-trade hours: close any open positions, don't enter new ones
            signals = []
            for symbol in ["BTC", "ETH", "SOL"]:
                if portfolio.positions.get(symbol, 0) != 0:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
            return signals

        # Outside no-trade hours: run normal strategy
        return super().on_bar(bar_data, portfolio)
