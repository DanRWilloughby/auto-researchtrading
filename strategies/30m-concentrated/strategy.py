"""
Concentrated 3-Coin MTF-Fusion — BTC/ETH/SOL only with large positions.

Hypothesis: The 8-coin version dilutes capital across illiquid alts.
Concentrating on 3 liquid majors allows:
- Bigger positions per coin (better fills in practice)
- Lower slippage (deeper order books)
- Stronger trend signals (BTC/ETH/SOL have clearest trends)
- 1/3 weight per coin = ~10% equity per position at 30% base

Key research dimensions:
- BASE_POSITION_PCT: 20-40% (1/3 weight → 7-13% per position)
- Signal sensitivity: can be more aggressive on liquid pairs
- Stop/TP tuning: different optimal values for majors vs alts
- Whether to add a BTC-as-regime-filter (only trade ETH/SOL when BTC confirms)
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Configuration ---
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
SYMBOL_WEIGHTS = {"BTC": 0.333, "ETH": 0.333, "SOL": 0.334}

# --- 1h Trend Filter Parameters ---
HTF_EMA_FAST = 14
HTF_EMA_SLOW = 52
HTF_MOM_WINDOW = 12
HTF_MOM_THRESHOLD = 0.0
HTF_MIN_VOTES = 2

# --- 30m Entry Signal Parameters ---
SHORT_WINDOW = 8
MED_WINDOW = 14
EMA_FAST = 3
EMA_SLOW = 12
RSI_PERIOD = 5
RSI_BULL = 51
RSI_BEAR = 49
RSI_OVERBOUGHT = 69
RSI_OVERSOLD = 31
MACD_FAST = 14
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_VOTES = 3

# --- Aggressive Risk Management ---
BASE_POSITION_PCT = 1.20        # deploy: reduced from 1.50 for OOS DD safety (8.6% vs 11.6%)
ATR_LOOKBACK = 24
ATR_STOP_MULT = 8.0
COOLDOWN_BARS = 1
TAKE_PROFIT_PCT = 0.012
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

            dyn_threshold = 0.009
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

            weight = SYMBOL_WEIGHTS.get(symbol, 1.0 / 3)
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

                _meta = {"bull_votes": bull_votes, "bear_votes": bear_votes, "htf": htf_direction}
                if current_pos > 0 and bearish and not in_cooldown:
                    signals.append(Signal(symbol=symbol, target_position=-size, metadata=_meta))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = atr
                elif current_pos < 0 and bullish and not in_cooldown:
                    signals.append(Signal(symbol=symbol, target_position=size, metadata=_meta))
                    self.entry_prices[symbol] = mid
                    self.peak_prices[symbol] = mid
                    self.atr_at_entry[symbol] = atr
                continue

            if in_cooldown:
                continue

            _meta = {"bull_votes": bull_votes, "bear_votes": bear_votes, "htf": htf_direction}
            if bullish:
                signals.append(Signal(symbol=symbol, target_position=size, metadata=_meta))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02
            elif bearish:
                signals.append(Signal(symbol=symbol, target_position=-size, metadata=_meta))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.02

        return signals

    def _clean_exit(self, symbol):
        self.entry_prices.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.atr_at_entry.pop(symbol, None)
        self.exit_bar[symbol] = self.bar_count
