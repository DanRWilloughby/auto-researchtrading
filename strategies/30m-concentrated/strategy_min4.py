"""
MIN_VOTES=4 variant — only enter on 4/5 or 5/5 conviction signals.
Skips 3/5 signals to reduce trade frequency and fee exposure.
"""
import importlib.util
from pathlib import Path

_base_path = Path(__file__).parent / "strategy.py"
_spec = importlib.util.spec_from_file_location("base_strategy", str(_base_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Override MIN_VOTES at module level before Strategy uses it
_mod.MIN_VOTES = 4


class Strategy(_mod.Strategy):
    pass
