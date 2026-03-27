"""
Pairs/Statistical Arbitrage — Experimental

Hypothesis: Correlated crypto pairs mean-revert when their spread deviates
from the rolling mean. Market-neutral by design: always long one leg, short the other.

Using EMA-based z-score for faster reaction to spread regime changes.
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Pair definitions ---
PAIRS = [
    ("BTC", "ETH"),
    ("SOL", "AVAX"),
    ("LINK", "XRP"),
    ("DOGE", "SUI"),
    ("BTC", "SOL"),
    ("ETH", "AVAX"),
    ("SOL", "LINK"),
    ("XRP", "DOGE"),
    ("AVAX", "LINK"),
    ("ETH", "SOL"),
    ("ETH", "XRP"),
    ("BTC", "DOGE"),
]

# --- Parameters ---
LOOKBACK = 72
ZSCORE_ENTRY = 3.9
ZSCORE_EXIT = 0.65
ZSCORE_STOP = 5.5
POSITION_SIZE_PCT = 1.38
MIN_HISTORY = 80
EMA_ALPHA = 2.0 / (LOOKBACK + 1)


class Strategy:
    def __init__(self):
        self.pair_state = {}
        self.exit_bar = {}
        self.bar_count = 0
        self.ema_mean = {}
        self.ema_var = {}

    def _calc_spread(self, closes_a, closes_b):
        return np.log(closes_a / closes_b)

    def _calc_zscore_ema(self, spread, pair_key):
        if len(spread) < LOOKBACK:
            return 0.0
        current = spread[-1]
        if pair_key not in self.ema_mean:
            window = spread[-LOOKBACK:]
            self.ema_mean[pair_key] = np.mean(window)
            self.ema_var[pair_key] = np.var(window)
        else:
            self.ema_mean[pair_key] = EMA_ALPHA * current + (1 - EMA_ALPHA) * self.ema_mean[pair_key]
            diff = current - self.ema_mean[pair_key]
            self.ema_var[pair_key] = EMA_ALPHA * (diff ** 2) + (1 - EMA_ALPHA) * self.ema_var[pair_key]
        std = np.sqrt(self.ema_var[pair_key])
        if std < 1e-10:
            return 0.0
        return (current - self.ema_mean[pair_key]) / std

    def on_bar(self, bar_data, portfolio):
        signals = []
        self.bar_count += 1
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        for leg_a, leg_b in PAIRS:
            pair_key = f"{leg_a}/{leg_b}"

            if leg_a not in bar_data or leg_b not in bar_data:
                continue

            hist_a = bar_data[leg_a].history
            hist_b = bar_data[leg_b].history

            if len(hist_a) < MIN_HISTORY or len(hist_b) < MIN_HISTORY:
                continue

            min_len = min(len(hist_a), len(hist_b))
            closes_a = hist_a["close"].values[-min_len:]
            closes_b = hist_b["close"].values[-min_len:]

            spread = self._calc_spread(closes_a, closes_b)
            z = self._calc_zscore_ema(spread, pair_key)

            in_trade = pair_key in self.pair_state
            size = equity * POSITION_SIZE_PCT

            if not in_trade:
                if z > ZSCORE_ENTRY:
                    signals.append(Signal(symbol=leg_a, target_position=-size))
                    signals.append(Signal(symbol=leg_b, target_position=size))
                    self.pair_state[pair_key] = {"side": -1, "entry_z": z}
                elif z < -ZSCORE_ENTRY:
                    signals.append(Signal(symbol=leg_a, target_position=size))
                    signals.append(Signal(symbol=leg_b, target_position=-size))
                    self.pair_state[pair_key] = {"side": 1, "entry_z": z}
            else:
                state = self.pair_state[pair_key]
                should_exit = False

                if state["side"] == -1 and z < ZSCORE_EXIT:
                    should_exit = True
                elif state["side"] == 1 and z > -ZSCORE_EXIT:
                    should_exit = True

                if abs(z) > ZSCORE_STOP:
                    should_exit = True

                if should_exit:
                    signals.append(Signal(symbol=leg_a, target_position=0.0))
                    signals.append(Signal(symbol=leg_b, target_position=0.0))
                    del self.pair_state[pair_key]
                    self.exit_bar[pair_key] = self.bar_count

        return signals
