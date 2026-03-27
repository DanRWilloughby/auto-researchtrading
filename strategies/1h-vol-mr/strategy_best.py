"""
Volatility Mean-Reversion — Baseline

Hypothesis: Crypto volatility is strongly mean-reverting. After vol spikes,
it compresses; after compression, it explodes. Instead of trading price
direction, trade the volatility regime itself.

Approach:
- Measure realized vol vs its rolling average (vol-of-vol z-score)
- After a vol spike (high z): sell vol by taking mean-reversion positions
  (fade the move, expect range contraction)
- After vol compression (low z): buy vol by taking breakout positions
  (expect expansion, ride the trend)
- Use Bollinger Band width percentile as primary vol measure
- ATR ratio as confirmation

Key parameters to explore:
- VOL_LOOKBACK for measuring realized vol
- HIGH_VOL_THRESHOLD / LOW_VOL_THRESHOLD
- Position sizing: larger in high-conviction vol regimes
- Which signal to use in each regime (mean-revert vs trend-follow)
- Number of coins to trade per regime
- Whether BTC vol regime applies globally or per-coin
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Configuration ---
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI", "DOGE", "AVAX", "LINK"]

# --- Parameters ---
VOL_LOOKBACK = 48           # bars for realized vol measurement
VOL_LONG_LOOKBACK = 168     # bars for vol mean (7 days at 1h)
BB_PERIOD = 20              # Bollinger Band period
HIGH_VOL_PCTILE = 80        # above this = vol spike regime
LOW_VOL_PCTILE = 20         # below this = vol compression regime
RSI_PERIOD = 8              # RSI for mean-reversion entries in high-vol
RSI_OVERBOUGHT = 70         # sell in high-vol when RSI overbought
RSI_OVERSOLD = 30           # buy in high-vol when RSI oversold
EMA_FAST = 7                # EMA for breakout direction in low-vol
EMA_SLOW = 26
POSITION_SIZE_PCT = 0.06
POSITION_SIZE_HIGHVOL = 0.04  # smaller size for MR (counter-trend)
POSITION_SIZE_LOWVOL = 0.08   # larger size for breakout (trend-following)
ATR_LOOKBACK = 24
ATR_STOP_MULT = 3.5         # stops in high-vol
ATR_STOP_MULT_LOW = 5.0     # wider stops in low-vol (let breakout run)
COOLDOWN_BARS = 2
MAX_POSITIONS = 5
MIN_HISTORY = 180
MAX_TRADE_LOSS_PCT = 0.015   # close trade if losing > 1.5% of equity
DD_BRAKE_PCT = 0.025         # halve position size if equity down > 2.5% from peak
DD_BRAKE_FACTOR = 0.5
MAX_HOLD_BARS_LOWVOL = 36   # max bars to hold low-vol breakout before time-exit
MAX_HOLD_BARS_HIGHVOL = 12  # max bars to hold high-vol MR before time-exit


def ema(values, span):
    alpha = 2.0 / (span + 1)
    result = np.empty_like(values, dtype=float)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def calc_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    rs = avg_gain / max(avg_loss, 1e-10)
    return 100 - 100 / (1 + rs)


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.entry_regime = {}  # symbol -> "high_vol" or "low_vol"
        self.exit_bar = {}
        self.entry_bar = {}      # symbol -> bar number of entry
        self.bar_count = 0
        self.peak_equity = 0.0

    def _bb_width_percentile(self, closes, period, lookback):
        """Current BB width percentile over lookback — vectorized."""
        if len(closes) < max(period * 2, lookback):
            return 50.0
        # Match original: windows end at closes[period-1] through closes[-2]
        # (original loop: for i in range(period, len(closes)): closes[i-period:i])
        c = closes[:-1]  # exclude last element to match original windowing
        windows = np.lib.stride_tricks.sliding_window_view(c, period)
        smas = np.mean(windows, axis=1)
        stds = np.std(windows, axis=1)
        safe_smas = np.where(smas > 0, smas, 1.0)
        widths = (2 * stds) / safe_smas
        widths = np.where(smas > 0, widths, 0.0)
        if len(widths) < 2:
            return 50.0
        current = widths[-1]
        return 100.0 * np.sum(widths <= current) / len(widths)

    def _realized_vol(self, closes, lookback):
        """Annualized realized volatility."""
        if len(closes) < lookback + 1:
            return 0.01
        log_rets = np.diff(np.log(closes[-lookback:]))
        return max(np.std(log_rets), 1e-8)

    def _vol_zscore(self, closes, short_lb, long_lb):
        """Z-score of short-term vol vs long-term vol."""
        if len(closes) < long_lb + 1:
            return 0.0
        short_vol = self._realized_vol(closes, short_lb)
        # Rolling vol of the long lookback
        vols = []
        for i in range(short_lb, min(len(closes), long_lb)):
            v = self._realized_vol(closes[:-(len(closes) - i - short_lb)], short_lb)
            vols.append(v)
        if len(vols) < 10:
            return 0.0
        mean_vol = np.mean(vols)
        std_vol = np.std(vols)
        if std_vol < 1e-12:
            return 0.0
        return (short_vol - mean_vol) / std_vol

    def _calc_atr(self, history, lookback):
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1):-1]
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - closes), np.abs(lows - closes)))
        return np.mean(tr)

    def on_bar(self, bar_data, portfolio):
        signals = []
        self.bar_count += 1
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        # Track peak equity for DD brake
        self.peak_equity = max(self.peak_equity, equity)
        dd_from_peak = 1.0 - equity / self.peak_equity if self.peak_equity > 0 else 0.0
        # Graduated DD brake: linear reduction from 1.0 to DD_BRAKE_FACTOR
        if dd_from_peak > DD_BRAKE_PCT:
            # Scale from 1.0 at DD_BRAKE_PCT to DD_BRAKE_FACTOR at 2*DD_BRAKE_PCT
            scale = max(0.0, 1.0 - (dd_from_peak - DD_BRAKE_PCT) / DD_BRAKE_PCT)
            size_factor = DD_BRAKE_FACTOR + scale * (1.0 - DD_BRAKE_FACTOR)
        else:
            size_factor = 1.0

        # Determine global vol regime from BTC
        btc_regime = "normal"
        if "BTC" in bar_data and len(bar_data["BTC"].history) >= MIN_HISTORY:
            btc_closes = bar_data["BTC"].history["close"].values
            btc_bb_pctile = self._bb_width_percentile(btc_closes, BB_PERIOD, VOL_LONG_LOOKBACK)
            if btc_bb_pctile > HIGH_VOL_PCTILE:
                btc_regime = "high_vol"
            elif btc_bb_pctile < LOW_VOL_PCTILE:
                btc_regime = "low_vol"

        active_count = sum(1 for s in ACTIVE_SYMBOLS if portfolio.positions.get(s, 0) != 0)

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < MIN_HISTORY:
                continue

            closes = bd.history["close"].values
            mid = bd.close
            current_pos = portfolio.positions.get(symbol, 0.0)
            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            # Per-coin vol regime
            bb_pctile = self._bb_width_percentile(closes, BB_PERIOD, VOL_LONG_LOOKBACK)
            if bb_pctile > HIGH_VOL_PCTILE:
                coin_regime = "high_vol"
            elif bb_pctile < LOW_VOL_PCTILE:
                coin_regime = "low_vol"
            else:
                coin_regime = "normal"

            # Use the more extreme regime between BTC global and coin-specific
            regime = coin_regime if coin_regime != "normal" else btc_regime

            # --- Manage existing positions ---
            if current_pos != 0:
                # Max trade loss check
                entry_px = self.entry_prices.get(symbol, mid)
                if current_pos > 0:
                    trade_pnl = (mid - entry_px) / entry_px * abs(current_pos)
                else:
                    trade_pnl = (entry_px - mid) / entry_px * abs(current_pos)
                if trade_pnl < -MAX_TRADE_LOSS_PCT * equity:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue

                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                entry_regime = self.entry_regime.get(symbol, "normal")
                stop_mult = ATR_STOP_MULT if entry_regime == "high_vol" else ATR_STOP_MULT_LOW

                if atr is not None:
                    if symbol not in self.peak_prices:
                        self.peak_prices[symbol] = mid

                    if current_pos > 0:
                        self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                        stop = self.peak_prices[symbol] - stop_mult * atr
                        if mid < stop:
                            signals.append(Signal(symbol=symbol, target_position=0.0))
                            self._clean_exit(symbol)
                            continue
                    else:
                        self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                        stop = self.peak_prices[symbol] + stop_mult * atr
                        if mid > stop:
                            signals.append(Signal(symbol=symbol, target_position=0.0))
                            self._clean_exit(symbol)
                            continue

                # Regime-specific exits
                rsi = calc_rsi(closes, RSI_PERIOD)
                if entry_regime == "high_vol":
                    # Mean-reversion: exit when RSI normalizes OR time limit
                    bars_held = self.bar_count - self.entry_bar.get(symbol, self.bar_count)
                    if current_pos > 0 and rsi > 60:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                    elif current_pos < 0 and rsi < 40:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                    elif bars_held >= MAX_HOLD_BARS_HIGHVOL:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                elif entry_regime == "low_vol":
                    # Time-based exit: close if breakout hasn't resolved
                    bars_held = self.bar_count - self.entry_bar.get(symbol, self.bar_count)
                    if bars_held >= MAX_HOLD_BARS_LOWVOL:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                continue

            # --- New entries ---
            if in_cooldown or regime == "normal":
                continue
            if active_count >= MAX_POSITIONS:
                continue

            if regime == "high_vol":
                # HIGH VOL: Mean-reversion — fade extremes with SMA confirmation
                size = equity * POSITION_SIZE_HIGHVOL * size_factor
                rsi = calc_rsi(closes, RSI_PERIOD)
                sma20 = np.mean(closes[-20:])
                if rsi > RSI_OVERBOUGHT and mid > sma20:
                    # Overbought AND above SMA = extended up, fade short
                    signals.append(Signal(symbol=symbol, target_position=-size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_regime[symbol] = "high_vol"
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1
                elif rsi < RSI_OVERSOLD and mid < sma20:
                    # Oversold AND below SMA = extended down, fade long
                    signals.append(Signal(symbol=symbol, target_position=size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_regime[symbol] = "high_vol"
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1

            elif regime == "low_vol":
                # LOW VOL: Breakout — ride EMA crossover direction
                size = equity * POSITION_SIZE_LOWVOL * size_factor
                ema_fast_arr = ema(closes[-(EMA_SLOW + 10):], EMA_FAST)
                ema_slow_arr = ema(closes[-(EMA_SLOW + 10):], EMA_SLOW)
                if ema_fast_arr[-1] > ema_slow_arr[-1]:
                    signals.append(Signal(symbol=symbol, target_position=size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_regime[symbol] = "low_vol"
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1
                elif ema_fast_arr[-1] < ema_slow_arr[-1]:
                    signals.append(Signal(symbol=symbol, target_position=-size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.entry_regime[symbol] = "low_vol"
                    self.entry_bar[symbol] = self.bar_count
                    active_count += 1

        return signals

    def _clean_exit(self, symbol):
        self.entry_prices.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.entry_regime.pop(symbol, None)
        self.entry_bar.pop(symbol, None)
        self.exit_bar[symbol] = self.bar_count
