"""
Dual-feed consensus: only enter when both HL and CB indicators agree.
Runs the 5 indicator votes on both price feeds independently.
Entry requires MIN_VOTES on BOTH feeds + HTF confirmation on BOTH.
Exit logic (stops, TP, RSI) uses CB data only (execution feed).
"""
import sys
import numpy as np
from pathlib import Path

# Ensure strategy dir is on path for local imports
sys.path.insert(0, str(Path(__file__).parent))

from prepare import Signal, PortfolioState, BarData
from dual_feed_loader import load_dual_candles
from dual_feed_helpers import (
    compute_votes, compute_htf, get_hl_closes, calc_rsi,
    ACTIVE_SYMBOLS, SYMBOL_WEIGHTS, BASE_POSITION_PCT,
    TAKE_PROFIT_PCT, ATR_STOP_MULT, ATR_LOOKBACK, COOLDOWN_BARS,
    MIN_HISTORY, MIN_VOTES, RSI_OVERBOUGHT, RSI_OVERSOLD, RSI_PERIOD,
    ema_calc, EMA_SLOW, EMA_FAST,
)


class Strategy:
    def __init__(self):
        self.entry_prices = {}
        self.peak_prices = {}
        self.atr_at_entry = {}
        self.exit_bar = {}
        self.bar_count = 0
        self._dual_data = {}
        for sym in ACTIVE_SYMBOLS:
            try:
                self._dual_data[sym] = load_dual_candles(sym)
            except Exception as e:
                print(f"Warning: could not load dual data for {sym}: {e}")

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

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < MIN_HISTORY:
                continue

            cb_closes = bd.history["close"].values
            mid = bd.close
            current_pos = portfolio.positions.get(symbol, 0.0)
            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            # Compute votes on CB feed
            cb_bull, cb_bear = compute_votes(cb_closes)
            cb_htf = compute_htf(cb_closes)

            # Compute votes on HL feed
            hl_closes = get_hl_closes(self._dual_data, symbol, bd.timestamp)
            if hl_closes is not None:
                hl_bull, hl_bear = compute_votes(hl_closes)
                hl_htf = compute_htf(hl_closes)
            else:
                hl_bull, hl_bear = cb_bull, cb_bear
                hl_htf = cb_htf

            # CONSENSUS: both feeds must independently confirm
            bullish = (cb_bull >= MIN_VOTES and hl_bull >= MIN_VOTES and
                       cb_htf >= 1 and hl_htf >= 1)
            bearish = (cb_bear >= MIN_VOTES and hl_bear >= MIN_VOTES and
                       cb_htf <= -1 and hl_htf <= -1)

            rsi = calc_rsi(cb_closes, RSI_PERIOD)
            weight = SYMBOL_WEIGHTS.get(symbol, 1.0 / 3)
            size = equity * BASE_POSITION_PCT * weight

            # --- Exit logic (identical to base strategy, uses CB data) ---
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

                # Flip: requires consensus
                _meta = {"cb_bull": int(cb_bull), "cb_bear": int(cb_bear),
                         "hl_bull": int(hl_bull), "hl_bear": int(hl_bear)}
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

            # --- Entry logic (consensus gated) ---
            if in_cooldown:
                continue

            _meta = {"cb_bull": int(cb_bull), "cb_bear": int(cb_bear),
                     "hl_bull": int(hl_bull), "hl_bear": int(hl_bear)}
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
