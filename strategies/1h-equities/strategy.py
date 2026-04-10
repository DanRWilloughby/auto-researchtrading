"""
Equity Multi-Asset Momentum — 10 non-correlated ETFs on 1h bars.

Ported from the 30m-concentrated crypto strategy. Same core signal logic
(5-indicator voting + higher-timeframe trend filter) adapted for equities:

- Lower volatility → scaled-down thresholds (~0.4x crypto)
- 10 ETFs across asset classes → diversification replaces concentration
- 2x leverage via margin (vs 1.2x on crypto)
- Zero commissions, near-zero slippage (SPY spread < 0.5 bps)
- No funding rates
- Market hours only (6.5 bars/day/ticker × 10 tickers = 65 signals/day)

Universe (non-correlated):
  SPY  (S&P 500)        QQQ  (Nasdaq 100)      IWM  (Russell 2000)
  XLE  (Energy)         XLF  (Financials)       GLD  (Gold)
  TLT  (Treasuries)     EEM  (Emerging Mkts)    XBI  (Biotech)
  SOXX (Semiconductors)

Target: 1% daily return with <10% max drawdown.
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

# --- Universe ---
ACTIVE_SYMBOLS = ["SPY", "QQQ", "IWM", "XLE", "XLF", "GLD", "TLT", "EEM", "XBI", "SOXX"]

# Volatility-weighted allocation: lower-vol assets get larger positions
# Approximate annual vol: SPY 16%, QQQ 22%, IWM 22%, XLE 28%, XLF 20%,
#                         GLD 15%, TLT 18%, EEM 20%, XBI 30%, SOXX 30%
# Inverse-vol weights (normalized to sum=1):
SYMBOL_WEIGHTS = {
    "SPY":  0.125,   # low vol anchor
    "QQQ":  0.095,
    "IWM":  0.095,
    "XLE":  0.075,
    "XLF":  0.105,
    "GLD":  0.130,   # low vol, true diversifier
    "TLT":  0.115,   # counter-cyclical
    "EEM":  0.095,
    "XBI":  0.070,   # high vol, lower weight
    "SOXX": 0.070,   # high vol, lower weight
}
# Verify weights sum to ~1.0 (0.975 — leave 2.5% buffer)

# --- Higher-Timeframe Trend Filter (uses daily-scale from 1h bars) ---
# Optimized: faster HTF response catches regime changes sooner
# HTF_MOM_WINDOW=15 (~2.3 trading days) was biggest single win in sweep
HTF_EMA_FAST = 7        # ~1 trading day (optimized from 10)
HTF_EMA_SLOW = 30       # ~4.6 trading days (optimized from 40)
HTF_MOM_WINDOW = 15     # ~2.3 trading days (optimized from 33)
HTF_MOM_THRESHOLD = 0.0
HTF_MIN_VOTES = 1       # only need 1/3 HTF signals (optimized from 2)

# --- 1h Entry Signal Parameters ---
# Equity vol is ~0.4x crypto vol, so scale momentum thresholds down
SHORT_WINDOW = 7        # ~1 trading day lookback
MED_WINDOW = 13         # ~2 trading days lookback
EMA_FAST = 3
EMA_SLOW = 10
RSI_PERIOD = 5
RSI_BULL = 52
RSI_BEAR = 48
RSI_OVERBOUGHT = 80     # let winners run longer (optimized from 70)
RSI_OVERSOLD = 20       # let winners run longer (optimized from 30)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_VOTES = 2           # more permissive entry (optimized from 3)

# --- Position Sizing & Risk Management ---
BASE_POSITION_PCT = 3.00    # 3x leverage (portfolio margin) — hits ~1% daily target
ATR_LOOKBACK = 20           # ~3 trading days of ATR
ATR_STOP_MULT = 10.0        # wider stops, let trends develop (optimized from 6.0)
COOLDOWN_BARS = 1
TAKE_PROFIT_PCT = 0.008     # 0.8% TP (vs 1.2% crypto — lower vol)
MIN_HISTORY = 45            # need ~7 trading days of history

# Momentum threshold — equity daily vol ~1%, hourly ~0.4%
# Crypto used 0.9% for 30m bars with ~0.7% per-bar vol
# Scale: 0.9% * (0.4%/0.7%) ≈ 0.5%
MOMENTUM_THRESHOLD = 0.005


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
        """Higher-timeframe trend filter using multi-day lookback on 1h bars."""
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

            # Higher-timeframe trend filter
            htf_direction = self._htf_trend(closes)

            # --- 1h Entry Signals (5-indicator vote) ---
            dyn_threshold = MOMENTUM_THRESHOLD

            # Medium-term momentum (~2 trading days)
            ret_med = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
            mom_bull = ret_med > dyn_threshold
            mom_bear = ret_med < -dyn_threshold

            # Short-term momentum (~1 trading day)
            ret_short = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]
            vshort_bull = ret_short > dyn_threshold * 0.7
            vshort_bear = ret_short < -dyn_threshold * 0.7

            # EMA crossover
            ema_fast_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_FAST)
            ema_slow_arr = ema_calc(closes[-(EMA_SLOW + 10):], EMA_SLOW)
            ema_bull = ema_fast_arr[-1] > ema_slow_arr[-1]
            ema_bear = ema_fast_arr[-1] < ema_slow_arr[-1]

            # RSI
            rsi = calc_rsi(closes, RSI_PERIOD)
            rsi_bull = rsi > RSI_BULL
            rsi_bear = rsi < RSI_BEAR

            # MACD histogram
            macd_hist = self._calc_macd(closes)
            macd_bull = macd_hist > 0
            macd_bear = macd_hist < 0

            # Vote count
            bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull])
            bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear])

            bullish = bull_votes >= MIN_VOTES and htf_direction >= 1
            bearish = bear_votes >= MIN_VOTES and htf_direction <= -1

            # Position sizing: equity × leverage × per-symbol weight
            weight = SYMBOL_WEIGHTS.get(symbol, 1.0 / len(ACTIVE_SYMBOLS))
            size = equity * BASE_POSITION_PCT * weight

            # --- Position management (open positions) ---
            if current_pos != 0:
                atr = self._calc_atr(bd.history, ATR_LOOKBACK)
                if atr is None:
                    atr = self.atr_at_entry.get(symbol, mid * 0.01)

                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mid

                # ATR trailing stop
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

                # Take profit
                entry_px = self.entry_prices.get(symbol, mid)
                if current_pos > 0 and mid > entry_px * (1 + TAKE_PROFIT_PCT):
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue
                elif current_pos < 0 and mid < entry_px * (1 - TAKE_PROFIT_PCT):
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue

                # RSI exhaustion exit
                if current_pos > 0 and rsi > RSI_OVERBOUGHT:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue
                elif current_pos < 0 and rsi < RSI_OVERSOLD:
                    signals.append(Signal(symbol=symbol, target_position=0.0))
                    self._clean_exit(symbol)
                    continue

                # Signal reversal → flip
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

            # --- No position: check for entry ---
            if in_cooldown:
                continue

            if bullish:
                signals.append(Signal(symbol=symbol, target_position=size))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.01
            elif bearish:
                signals.append(Signal(symbol=symbol, target_position=-size))
                self.entry_prices[symbol] = mid
                self.peak_prices[symbol] = mid
                self.atr_at_entry[symbol] = self._calc_atr(bd.history, ATR_LOOKBACK) or mid * 0.01

        return signals

    def _clean_exit(self, symbol):
        self.entry_prices.pop(symbol, None)
        self.peak_prices.pop(symbol, None)
        self.atr_at_entry.pop(symbol, None)
        self.exit_bar[symbol] = self.bar_count
