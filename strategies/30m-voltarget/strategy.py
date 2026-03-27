"""
Volatility-Targeted 30m Strategy — Proven signal logic with adaptive sizing.

Based on the successful 30m-btc-eth-sol architecture but with vol-targeted position sizing:
- When vol is LOW -> increase size (calm market, steady trends)
- When vol is HIGH -> decrease size (protect from blowups)
- Target a fixed daily portfolio volatility

Key research dimensions:
- TARGET_DAILY_VOL: 0.005 - 0.03
- VOL_LOOKBACK: 24-96 bars
- Vol scaling method: sqrt vs linear vs rank-based
- BASE_POSITION_PCT, MAX_POSITION_PCT, MIN_POSITION_PCT
- Coin concentration: 3, 4, 6, 8 coins
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Configuration ---
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.33, "ETH": 0.33, "SOL": 0.33}

# --- Signal Parameters (proven from 30m-btc-eth-sol) ---
SHORT_WINDOW = 6
MED_WINDOW = 14
LONG_WINDOW = 48
EMA_FAST = 8
EMA_SLOW = 42
RSI_PERIOD = 10
RSI_BULL = 50
RSI_BEAR = 50
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MACD_FAST = 8
MACD_SLOW = 20
MACD_SIGNAL = 7
BB_PERIOD = 10
MIN_VOTES = 4  # out of 6

# --- Volatility-Targeted Sizing ---
TARGET_DAILY_VOL = 0.03         # Target 3% daily portfolio volatility
VOL_LOOKBACK = 72               # 36h of 30m bars
BASE_POSITION_PCT = 0.90        # Base position size (scaled by vol)
MAX_POSITION_PCT = 1.20         # Cap per position
MIN_POSITION_PCT = 0.25         # Floor per position
VOL_SCALE_CAP = 2.5             # Max vol scalar

# --- Risk Management ---
ATR_LOOKBACK = 48
ATR_STOP_MULT = 6.5
COOLDOWN_BARS = 4
TAKE_PROFIT_PCT = 0.025
BASE_THRESHOLD = 0.010


def ema_calc(values, span):
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
        self.atr_at_entry = {}
        self.exit_bar = {}
        self.bar_count = 0

    def _calc_atr(self, history, lookback):
        if len(history) < lookback + 1:
            return None
        highs = history["high"].values[-lookback:]
        lows = history["low"].values[-lookback:]
        closes = history["close"].values[-(lookback + 1):-1]
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - closes), np.abs(lows - closes)))
        return np.mean(tr)

    def _calc_realized_vol(self, closes, lookback):
        """Compute daily realized volatility from bar returns."""
        if len(closes) < lookback + 1:
            return TARGET_DAILY_VOL  # Default to target
        log_rets = np.diff(np.log(closes[-lookback:]))
        return max(np.std(log_rets), 1e-6)

    def _vol_scaled_size(self, equity, closes, weight):
        """Scale position size inversely with realized vol."""
        realized_vol = self._calc_realized_vol(closes, VOL_LOOKBACK)
        vol_ratio = realized_vol / TARGET_DAILY_VOL
        # sqrt scaling to dampen extreme adjustments
        vol_scalar = np.sqrt(1.0 / vol_ratio) if vol_ratio > 0 else 1.0
        vol_scalar = min(vol_scalar, VOL_SCALE_CAP)
        scaled_pct = BASE_POSITION_PCT * vol_scalar
        clamped_pct = max(MIN_POSITION_PCT, min(MAX_POSITION_PCT, scaled_pct))
        return equity * clamped_pct * weight

    def _calc_macd(self, closes):
        if len(closes) < MACD_SLOW + MACD_SIGNAL + 5:
            return 0.0
        fast = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_FAST)
        slow = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_SLOW)
        macd_line = fast - slow
        signal_line = ema_calc(macd_line, MACD_SIGNAL)
        return macd_line[-1] - signal_line[-1]

    def _calc_bb_width_pctile(self, closes, period):
        if len(closes) < period * 3:
            return 50.0
        widths = []
        for i in range(period * 2, len(closes)):
            window = closes[i - period:i]
            sma = np.mean(window)
            std = np.std(window)
            width = (2 * std) / sma if sma > 0 else 0
            widths.append(width)
        if len(widths) < 2:
            return 50.0
        current_width = widths[-1]
        pctile = 100 * np.sum(np.array(widths) <= current_width) / len(widths)
        return pctile

    def on_bar(self, bar_data, portfolio):
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash
        self.bar_count += 1

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < max(LONG_WINDOW, EMA_SLOW, MACD_SLOW + MACD_SIGNAL + 5, BB_PERIOD * 3) + 5:
                continue

            closes = bd.history["close"].values
            mid = bd.close

            # Dynamic threshold based on realized vol
            realized_vol = self._calc_realized_vol(closes, VOL_LOOKBACK)
            vol_ratio = realized_vol / TARGET_DAILY_VOL
            dyn_threshold = BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)
            dyn_threshold = max(0.005, min(0.018, dyn_threshold))

            # Signal 1: Momentum
            ret_short = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold

            # Signal 2: Short momentum
            ret_vshort = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            vshort_bull = ret_vshort > dyn_threshold * 0.85
            vshort_bear = ret_vshort < -dyn_threshold * 0.85

            # Signal 3: EMA crossover
            ema_fast_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_FAST)
            ema_slow_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            # Signal 4: RSI
            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > RSI_BULL
            rsi_bear = rsi < RSI_BEAR

            # Signal 5: MACD
            macd_hist = self._calc_macd(closes)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            # Signal 6: BB compression
            bb_pctile = self._calc_bb_width_pctile(closes, BB_PERIOD)
            bb_compressed = bb_pctile < 90

            bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed])
            bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed])

            bullish = bull_votes >= MIN_VOTES
            bearish = bear_votes >= MIN_VOTES

            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            weight = SYMBOL_WEIGHTS.get(symbol, 0.33)
            # KEY DIFFERENCE: vol-targeted sizing
            size = self._vol_scaled_size(equity, closes, weight)

            current_pos = portfolio.positions.get(symbol, 0.0)
            target = current_pos

            if current_pos == 0:
                if not in_cooldown:
                    if bullish:
                        target = size
                    elif bearish:
                        target = -size
            else:
                # ATR trailing stop
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] - ATR_STOP_MULT * atr
                    if mid < stop:
                        target = 0.0
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] + ATR_STOP_MULT * atr
                    if mid > stop:
                        target = 0.0

                # Take profit
                if symbol in self.entry_prices:
                    entry = self.entry_prices[symbol]
                    pnl = (mid - entry) / entry if current_pos > 0 else (entry - mid) / entry
                    if pnl > TAKE_PROFIT_PCT:
                        target = 0.0

                # RSI exit
                if current_pos > 0 and rsi > RSI_OVERBOUGHT:
                    target = 0.0
                elif current_pos < 0 and rsi < RSI_OVERSOLD:
                    target = 0.0

                # Signal flip
                if current_pos > 0 and bearish and not in_cooldown:
                    target = -size
                elif current_pos < 0 and bullish and not in_cooldown:
                    target = size

            if abs(target - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target))
                if target != 0 and current_pos == 0:
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
                elif target == 0:
                    self.entry_prices.pop(symbol, None)
                    self.peak_prices.pop(symbol, None)
                    self.atr_at_entry.pop(symbol, None)
                    self.exit_bar[symbol] = self.bar_count
                elif (target > 0 and current_pos < 0) or (target < 0 and current_pos > 0):
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02

        return signals
