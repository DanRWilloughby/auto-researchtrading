"""
15m Multi-Timeframe Scalper — Higher frequency variant targeting 1% daily return.

Adapts the proven MTF architecture from 1h/30m to 30m/15m:
- 30m trend filter (computed from 15m bars, every 2 bars)
- 15m entry signals for precise timing
- More trades per day = faster compounding
- Aggressive sizing for $20K capital

Key research dimensions:
- HTF window sizes (adapted for faster timeframe)
- Entry signal sensitivity (more opportunities per bar)
- Position sizing: 15-35%
- Stop/TP calibration for 15m noise levels
- Cooldown: 1-4 bars (15-60 min)
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Configuration ---
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {s: 1.0 / len(ACTIVE_SYMBOLS) for s in ACTIVE_SYMBOLS}

# --- 30m Trend Filter (from 15m bars, 2-bar aggregation) ---
HTF_EMA_FAST = 10       # ~2.5h in 15m bars
HTF_EMA_SLOW = 40       # ~10h in 15m bars
HTF_MOM_WINDOW = 14     # ~3.5h lookback
HTF_MOM_THRESHOLD = 0.0
HTF_MIN_VOTES = 2

# --- 15m Entry Signal Parameters ---
SHORT_WINDOW = 8        # 2h
MED_WINDOW = 8          # 2h
EMA_FAST = 3
EMA_SLOW = 10
RSI_PERIOD = 8
RSI_BULL = 51
RSI_BEAR = 49
RSI_OVERBOUGHT = 69
RSI_OVERSOLD = 31
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 5
MIN_VOTES = 3

# --- Aggressive Risk Management ---
BASE_POSITION_PCT = 2.15
ATR_LOOKBACK = 24       # 6h in 15m bars
ATR_STOP_MULT = 8.0
COOLDOWN_BARS = 2       # 30 min cooldown
TAKE_PROFIT_PCT = 0.008 # 0.8% — faster TP for scalping
MIN_HISTORY = 60


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

    def _calc_macd(self, closes):
        if len(closes) < MACD_SLOW + MACD_SIGNAL + 5:
            return 0.0
        fast = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_FAST)
        slow = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_SLOW)
        macd_line = fast - slow
        signal_line = ema_calc(macd_line, MACD_SIGNAL)
        return macd_line[-1] - signal_line[-1]

    def _htf_trend(self, closes):
        if len(closes) < max(HTF_EMA_SLOW + 10, HTF_MOM_WINDOW + 1):
            return 0
        ema_fast = ema_calc(closes[-(HTF_EMA_SLOW + 10):], HTF_EMA_FAST)
        ema_slow = ema_calc(closes[-(HTF_EMA_SLOW + 10):], HTF_EMA_SLOW)
        ema_bull = ema_fast[-1] > ema_slow[-1]
        mom = (closes[-1] - closes[-HTF_MOM_WINDOW]) / closes[-HTF_MOM_WINDOW]
        mom_bull = mom > HTF_MOM_THRESHOLD
        mom_bear = mom < -HTF_MOM_THRESHOLD
        htf_macd = self._calc_macd(closes)
        macd_bull = htf_macd > 0
        bull_votes = sum([ema_bull, mom_bull, macd_bull])
        bear_votes = sum([not ema_bull, mom_bear, not macd_bull])
        if bull_votes >= HTF_MIN_VOTES:
            return 1
        elif bear_votes >= HTF_MIN_VOTES:
            return -1
        return 0

    def on_bar(self, bar_data, portfolio):
        signals = []
        self.bar_count += 1
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

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

            htf_direction = self._htf_trend(closes)

            dyn_threshold = 0.008  # Lower threshold for 15m (smaller moves)
            ret_short = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            ret_vshort = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]

            mom_bull = ret_short > dyn_threshold
            mom_bear = ret_short < -dyn_threshold
            vshort_bull = ret_vshort > dyn_threshold * 0.7
            vshort_bear = ret_vshort < -dyn_threshold * 0.7

            ema_fast_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_FAST)
            ema_slow_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > RSI_BULL
            rsi_bear = rsi < RSI_BEAR

            macd_hist = self._calc_macd(closes)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull])
            bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear])

            bullish = bull_votes >= MIN_VOTES and htf_direction >= 1
            bearish = bear_votes >= MIN_VOTES and htf_direction <= -1

            weight = SYMBOL_WEIGHTS.get(symbol, 1.0 / 6)
            size = equity * BASE_POSITION_PCT * weight

            if current_pos != 0:
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.02)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                if current_pos > 0:
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] - ATR_STOP_MULT * atr
                    if mid < stop:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                        continue
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mid)
                    stop = self.peak_prices[symbol] + ATR_STOP_MULT * atr
                    if mid > stop:
                        signals.append(Signal(symbol=symbol, target_position=0.0))
                        self._clean_exit(symbol)
                        continue

                entry_px = self.entry_prices.get(symbol, mid)
                if current_pos > 0 and mid > entry_px * (1 + TAKE_PROFIT_PCT):
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue
                elif current_pos < 0 and mid < entry_px * (1 - TAKE_PROFIT_PCT):
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue

                if current_pos > 0 and rsi > RSI_OVERBOUGHT:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue
                elif current_pos < 0 and rsi < RSI_OVERSOLD:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue

                if current_pos > 0 and bearish and not in_cooldown:
                    signals.append(Signal(symbol=symbol, target_position=-size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = atr
                elif current_pos < 0 and bullish and not in_cooldown:
                    signals.append(Signal(symbol=symbol, target_position=size))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = atr
                continue

            if in_cooldown:
                continue

            if bullish:
                signals.append(Signal(symbol=symbol, target_position=size))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
            elif bearish:
                signals.append(Signal(symbol=symbol, target_position=-size))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02

        return signals

    def _clean_exit(self, symbol):
        self.entry_prices.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.atr_at_entry.pop(symbol, None)
        self.exit_bar[symbol] = self.bar_count
