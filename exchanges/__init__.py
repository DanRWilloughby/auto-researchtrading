"""
Exchange abstraction layer.

Decouples the trading loop from any specific venue. Clients implement the
ExchangeClient ABC defined in `base.py`. The strategy and backtest engine
remain exchange-agnostic — only the execution layer knows about venues.

Available clients:
    CoinbaseClient    - Coinbase Financial Markets (CFTC-regulated perps) [primary]
    HyperliquidClient - Hyperliquid DEX (historical data + optional secondary)
"""

from .base import ExchangeClient, Position, OrderResult, ContractSpec
from .coinbase_client import CoinbaseClient

__all__ = ["ExchangeClient", "Position", "OrderResult", "ContractSpec", "CoinbaseClient"]
