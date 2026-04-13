"""
Shared indicator helpers for dual-feed strategies.
Computes bull/bear votes and HTF direction from any close price array.
"""
import numpy as np
import importlib.util
from pathlib import Path

# Load base strategy module for parameters and helper functions
_base_path = Path(__file__).parent / "strategy.py"
_spec = importlib.util.spec_from_file_location("base_strategy", str(_base_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Re-export parameters
ACTIVE_SYMBOLS = _mod.ACTIVE_SYMBOLS
SYMBOL_WEIGHTS = _mod.SYMBOL_WEIGHTS
BASE_POSITION_PCT = _mod.BASE_POSITION_PCT
TAKE_PROFIT_PCT = _mod.TAKE_PROFIT_PCT
ATR_STOP_MULT = _mod.ATR_STOP_MULT
ATR_LOOKBACK = _mod.ATR_LOOKBACK
COOLDOWN_BARS = _mod.COOLDOWN_BARS
MIN_HISTORY = _mod.MIN_HISTORY
MIN_VOTES = _mod.MIN_VOTES
RSI_OVERBOUGHT = _mod.RSI_OVERBOUGHT
RSI_OVERSOLD = _mod.RSI_OVERSOLD
RSI_PERIOD = _mod.RSI_PERIOD

SHORT_WINDOW = _mod.SHORT_WINDOW
MED_WINDOW = _mod.MED_WINDOW
EMA_FAST = _mod.EMA_FAST
EMA_SLOW = _mod.EMA_SLOW
RSI_BULL = _mod.RSI_BULL
RSI_BEAR = _mod.RSI_BEAR
MACD_FAST = _mod.MACD_FAST
MACD_SLOW = _mod.MACD_SLOW
MACD_SIGNAL = _mod.MACD_SIGNAL
HTF_EMA_FAST = _mod.HTF_EMA_FAST
HTF_EMA_SLOW = _mod.HTF_EMA_SLOW
HTF_MOM_WINDOW = _mod.HTF_MOM_WINDOW
HTF_MOM_THRESHOLD = _mod.HTF_MOM_THRESHOLD
HTF_MIN_VOTES = _mod.HTF_MIN_VOTES

ema_calc = _mod.ema_calc
calc_rsi = _mod.calc_rsi


def compute_votes(closes):
    """Compute bull_votes and bear_votes from a close price array."""
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

    if len(closes) >= MACD_SLOW + MACD_SIGNAL + 5:
        fast = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_FAST)
        slow = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_SLOW)
        macd_line = fast - slow
        signal_line = ema_calc(macd_line, MACD_SIGNAL)
        macd_hist = macd_line[-1] - signal_line[-1]
    else:
        macd_hist = 0.0
    macd_bull = macd_hist > 0
    macd_bear = macd_hist < 0

    bull = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull])
    bear = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear])
    return bull, bear


def compute_htf(closes):
    """Compute higher-timeframe trend direction from close prices."""
    if len(closes) < max(HTF_EMA_SLOW + 10, HTF_MOM_WINDOW + 1):
        return 0

    ema_f = ema_calc(closes[-(HTF_EMA_SLOW + 10):], HTF_EMA_FAST)
    ema_s = ema_calc(closes[-(HTF_EMA_SLOW + 10):], HTF_EMA_SLOW)
    ema_bull = ema_f[-1] > ema_s[-1]

    mom = (closes[-1] - closes[-HTF_MOM_WINDOW]) / closes[-HTF_MOM_WINDOW]
    mom_bull = mom > HTF_MOM_THRESHOLD
    mom_bear = mom < -HTF_MOM_THRESHOLD

    if len(closes) >= MACD_SLOW + MACD_SIGNAL + 5:
        fast = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_FAST)
        slow = ema_calc(closes[-(MACD_SLOW + MACD_SIGNAL + 5):], MACD_SLOW)
        ml = fast - slow
        sl = ema_calc(ml, MACD_SIGNAL)
        macd_bull = (ml[-1] - sl[-1]) > 0
    else:
        macd_bull = False

    bull_v = sum([ema_bull, mom_bull, macd_bull])
    bear_v = sum([not ema_bull, mom_bear, not macd_bull])
    if bull_v >= HTF_MIN_VOTES:
        return 1
    elif bear_v >= HTF_MIN_VOTES:
        return -1
    return 0


def get_hl_closes(dual_data, symbol, timestamp, max_len=500):
    """Extract HL close prices up to the given timestamp from pre-loaded dual data."""
    if symbol not in dual_data:
        return None
    dual = dual_data[symbol]
    mask = dual["timestamp"] <= timestamp
    count = mask.sum()
    if count < MIN_HISTORY:
        return None
    return dual.loc[mask, "close_hl"].values[-max_len:]
