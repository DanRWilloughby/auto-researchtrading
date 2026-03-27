"""
Funding Rate Mean-Reversion — Baseline

Hypothesis: Extreme funding rates predict short-term reversals. When longs
are paying shorts heavily (high positive funding), the market is overleveraged
long and due for a pullback. Vice versa for extreme negative funding.

This is different from the existing strategy's FUNDING_BOOST (which was a
position size modifier and got disabled). Here funding IS the primary signal.

Approach:
- Track rolling average funding rate per coin
- Compute z-score of current funding vs rolling history
- Enter contrarian: short when funding extremely positive, long when extremely negative
- Exit when funding normalizes
- Size positions by funding extremity (more extreme = higher conviction)

Key parameters to explore:
- FUNDING_LOOKBACK (how much history for z-score)
- FUNDING_ZSCORE_ENTRY / EXIT thresholds
- Whether to add momentum confirmation (avoid fighting strong trends)
- Position sizing: fixed vs proportional to z-score
- Multi-coin: do all coins at once or only most extreme?
- Holding period: time-based exit vs funding-normalization exit
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Configuration ---
ACTIVE_SYMBOLS = ["SOL", "SUI", "DOGE", "AVAX", "LINK"]

# --- Parameters ---
FUNDING_LOOKBACK = 96       # bars for rolling funding stats
FUNDING_ZSCORE_ENTRY = 3.0  # enter when |funding z| > this
FUNDING_ZSCORE_EXIT = 0.3   # exit when |funding z| < this
MOMENTUM_FILTER = True      # require momentum to not oppose entry
MOMENTUM_WINDOW = 12        # bars for momentum check
MOMENTUM_THRESHOLD = 0.012  # max opposing momentum to allow entry
FUNDING_DECEL_FILTER = True  # only enter when funding is decelerating (starting to mean-revert)
FUNDING_DECEL_WINDOW = 6    # bars to check funding change direction
REQUIRE_CROSS_COIN = False  # require cross-coin funding agreement
POSITION_SIZE_PCT = 0.25    # base position size as % of equity
SCALE_BY_ZSCORE = False     # scale position by funding extremity
MAX_POSITIONS = 2           # max simultaneous positions (pick most extreme)
COOLDOWN_BARS = 6           # bars after exit before re-entry
ATR_LOOKBACK = 24           # ATR for trailing stop
ATR_STOP_MULT = 4.0         # trailing stop distance in ATRs
MAX_HOLD_BARS = 0           # 0 = disabled; force exit after N bars
TAKE_PROFIT_PCT = 0.07      # take profit at 7% return
STOP_LOSS_PCT = 0.0         # 0 = disabled; stop loss
MIN_HISTORY = 100  # must be >= FUNDING_LOOKBACK


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.entry_bar = {}
        self.exit_bar = {}
        self.bar_count = 0

    def _funding_zscore(self, funding_rates, lookback):
        """Z-score of latest funding rate vs rolling history."""
        if len(funding_rates) < lookback:
            return 0.0
        window = funding_rates[-lookback:]
        mean = np.mean(window)
        std = np.std(window)
        if std < 1e-12:
            return 0.0
        return (funding_rates[-1] - mean) / std

    def _calc_atr(self, history, lookback):
        """Average True Range."""
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1):-1]
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - closes), np.abs(lows - closes)))
        return np.mean(tr)

    def _momentum(self, closes, window):
        """Simple return over window."""
        if len(closes) < window + 1:
            return 0.0
        return (closes[-1] - closes[-window]) / closes[-window]

    def on_bar(self, bar_data, portfolio):
        signals = []
        self.bar_count += 1
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        # Score all symbols by funding z-score
        candidates = []
        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < MIN_HISTORY:
                continue

            funding = bd.history["funding_rate"].values
            fz = self._funding_zscore(funding, FUNDING_LOOKBACK)
            closes = bd.history["close"].values
            mom = self._momentum(closes, MOMENTUM_WINDOW)

            # Funding deceleration: compare recent avg vs older avg
            funding_decel = 0.0
            if len(funding) >= FUNDING_DECEL_WINDOW * 2:
                recent_avg = np.mean(funding[-FUNDING_DECEL_WINDOW:])
                older_avg = np.mean(funding[-FUNDING_DECEL_WINDOW*2:-FUNDING_DECEL_WINDOW])
                funding_decel = recent_avg - older_avg

            candidates.append({
                "symbol": symbol,
                "fz": fz,
                "mom": mom,
                "bd": bd,
                "closes": closes,
                "funding_decel": funding_decel,
            })

        # Compute average funding z-score for cross-coin agreement
        if candidates:
            avg_fz = np.mean([c["fz"] for c in candidates])
        else:
            avg_fz = 0.0

        # Sort by absolute funding z-score (most extreme first)
        candidates.sort(key=lambda c: abs(c["fz"]), reverse=True)

        # Track which symbols get signals (avoid duplicate signals)
        signaled = set()
        active_count = sum(1 for s in ACTIVE_SYMBOLS if portfolio.positions.get(s, 0) != 0)

        for c in candidates:
            symbol = c["symbol"]
            fz = c["fz"]
            mom = c["mom"]
            bd = c["bd"]
            closes = c["closes"]
            mid = bd.close

            current_pos = portfolio.positions.get(symbol, 0.0)
            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            # --- Manage existing position ---
            if current_pos != 0:
                # Take profit
                entry_px = self.entry_prices.get(symbol, mid)
                if TAKE_PROFIT_PCT > 0 and entry_px > 0:
                    if current_pos > 0:
                        pnl_pct = (mid - entry_px) / entry_px
                    else:
                        pnl_pct = (entry_px - mid) / entry_px
                    if pnl_pct >= TAKE_PROFIT_PCT:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        signaled.add(symbol)
                        self._clean_exit(symbol)
                        continue

                    # Fixed stop loss
                    if STOP_LOSS_PCT > 0 and pnl_pct <= -STOP_LOSS_PCT:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        signaled.add(symbol)
                        self._clean_exit(symbol)
                        continue

                # Time-based exit
                bars_held = self.bar_count - self.entry_bar.get(symbol, self.bar_count)
                if MAX_HOLD_BARS > 0 and bars_held >= MAX_HOLD_BARS:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    signaled.add(symbol)
                    self._clean_exit(symbol)
                    continue

                # Trailing stop via ATR
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                if atr is not None:
                    if symbol not in self.peak_prices:
                        self.peak_prices[symbol] = mid

                    if current_pos > 0:
                        self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                        stop = self.peak_prices[symbol] - ATR_STOP_MULT * atr
                        if mid < stop:
                            signals.append(Signal(symbol=symbol, target_position=0.0))
                            signaled.add(symbol)
                            self._clean_exit(symbol)
                            continue
                    else:
                        self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                        stop = self.peak_prices[symbol] + ATR_STOP_MULT * atr
                        if mid > stop:
                            signals.append(Signal(symbol=symbol, target_position=0.0))
                            signaled.add(symbol)
                            self._clean_exit(symbol)
                            continue

                # Funding normalization exit: exit when funding crosses threshold
                if current_pos > 0 and fz > -FUNDING_ZSCORE_EXIT:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    signaled.add(symbol)
                    self._clean_exit(symbol)
                elif current_pos < 0 and fz < FUNDING_ZSCORE_EXIT:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    signaled.add(symbol)
                    self._clean_exit(symbol)
                continue

            # --- New entries ---
            if symbol in signaled or in_cooldown:
                continue
            if active_count >= MAX_POSITIONS:
                continue

            if abs(fz) > FUNDING_ZSCORE_ENTRY:
                # Momentum filter: don't fight strong trends
                if MOMENTUM_FILTER:
                    if fz > 0 and mom > MOMENTUM_THRESHOLD:
                        continue  # funding says short but price surging
                    if fz < 0 and mom < -MOMENTUM_THRESHOLD:
                        continue  # funding says long but price crashing

                # Cross-coin agreement: only enter if market-wide funding agrees
                if REQUIRE_CROSS_COIN:
                    if fz > 0 and avg_fz < 0:
                        continue  # this coin has high funding but market avg is negative
                    if fz < 0 and avg_fz > 0:
                        continue  # this coin has low funding but market avg is positive

                # Funding deceleration filter: only enter when funding starts normalizing
                if FUNDING_DECEL_FILTER:
                    if fz > 0 and c["funding_decel"] > 0:
                        continue  # funding still accelerating upward
                    if fz < 0 and c["funding_decel"] < 0:
                        continue  # funding still accelerating downward

                # Size: base or scaled by z-score
                size = equity * POSITION_SIZE_PCT
                if SCALE_BY_ZSCORE:
                    scale = min(abs(fz) / FUNDING_ZSCORE_ENTRY, 1.5)
                    size *= scale

                if fz > FUNDING_ZSCORE_ENTRY:
                    # Funding very positive -> market overleveraged long -> go short
                    signals.append(Signal(symbol=symbol, target_position=-size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1
                elif fz < -FUNDING_ZSCORE_ENTRY:
                    # Funding very negative -> market overleveraged short -> go long
                    signals.append(Signal(symbol=symbol, target_position=size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1

                signaled.add(symbol)

        return signals

    def _clean_exit(self, symbol):
        self.entry_prices.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.entry_bar.pop(symbol, None)
        self.exit_bar[symbol] = self.bar_count
