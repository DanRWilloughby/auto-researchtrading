"""
HL signal, CB execution: generates all entry signals from HL candle data
but executes on Coinbase prices. Tests hypothesis that HL produces better signals.
Exit logic (stops, TP, RSI) uses CB data since that's where positions are.
"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from prepare import Signal, PortfolioState, BarData
from dual_feed_loader import load_dual_candles
from dual_feed_helpers import (
    compute_votes, compute_htf, get_hl_closes, calc_rsi,
    ACTIVE_SYMBOLS, SYMBOL_WEIGHTS, BASE_POSITION_PCT,
    TAKE_PROFIT_PCT, ATR_STOP_MULT, ATR_LOOKBACK, COOLDOWN_BARS,
    MIN_HISTORY, MIN_VOTES, RSI_OVERBOUGHT, RSI_OVERSOLD, RSI_PERIOD,
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
            mid = bd.close  # CB price for execution/stops
            current_pos = portfolio.positions.get(symbol, 0.0)
            in_cooldown = (self.bar_count - self.exit_bar.get(symbol, -999)) < COOLDOWN_BARS

            # Use HL for signal generation, fall back to CB if unavailable
            hl_closes = get_hl_closes(self._dual_data, symbol, bd.timestamp)
            signal_closes = hl_closes if hl_closes is not None else cb_closes

            bull_votes, bear_votes = compute_votes(signal_closes)
            htf_direction = compute_htf(signal_closes)

            bullish = bull_votes >= MIN_VOTES and htf_direction >= 1
            bearish = bear_votes >= MIN_VOTES and htf_direction <= -1

            # RSI on CB data (for exit logic)
            rsi = calc_rsi(cb_closes, RSI_PERIOD)
            weight = SYMBOL_WEIGHTS.get(symbol, 1.0 / 3)
            size = equity * BASE_POSITION_PCT * weight

            # --- Exit logic (CB data for stops/TP/RSI) ---
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

                _meta = {"feed": "HL", "bull_votes": int(bull_votes),
                         "bear_votes": int(bear_votes), "htf": htf_direction}
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

            # --- Entry (HL signals, CB execution) ---
            if in_cooldown:
                continue

            _meta = {"feed": "HL", "bull_votes": int(bull_votes),
                     "bear_votes": int(bear_votes), "htf": htf_direction}
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
