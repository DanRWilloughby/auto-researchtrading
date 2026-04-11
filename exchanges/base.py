"""
Exchange client abstract base class.

All venue-specific clients (Coinbase, Hyperliquid, etc.) implement this
interface so the rest of the trading system never needs to know which
exchange it's talking to.

Design principles:
  - Return shapes are consistent across venues (dataclasses, not dicts)
  - All monetary values are in USD notional (signed: +long, -short)
  - Contract-based exchanges (Coinbase) handle contract<->notional conversion
    internally so the strategy never sees contract counts
  - Dry-run mode is first-class: set dry_run=True and no live orders fire
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class ContractSpec:
    """Per-symbol contract specification for discrete-contract exchanges."""
    symbol: str                  # canonical symbol: "BTC", "ETH", "SOL"
    product_id: str              # venue-specific product ID (e.g. "BIP-20DEC30-CDE")
    contract_size: float         # base units per contract (e.g. 0.01 BTC)
    price_increment: float       # minimum price tick
    min_notional_usd: float = 10.0  # minimum order size in USD
    overnight_margin_rate: float = 0.25  # fraction of notional required as margin overnight
    intraday_margin_rate: float = 0.10


@dataclass
class Position:
    """An open position on a venue."""
    symbol: str
    contracts: float             # signed: positive=long, negative=short
    notional_usd: float          # signed: contracts * contract_size * mark_price
    entry_price: float
    mark_price: float
    unrealized_pnl_usd: float = 0.0


@dataclass
class OrderResult:
    """Result of a place_order call."""
    success: bool
    order_id: Optional[str]
    symbol: str
    side: str                    # "BUY" or "SELL"
    contracts: float             # signed
    notional_usd: float          # signed, using fill price
    fill_price: Optional[float]
    fee_usd: float = 0.0
    error_message: Optional[str] = None
    dry_run: bool = False
    raw_response: Optional[dict] = None


class ExchangeClient(ABC):
    """Abstract interface all exchange clients implement."""

    # --- Identification -------------------------------------------------
    @property
    @abstractmethod
    def name(self) -> str:
        """Short name, e.g. 'coinbase'."""

    @abstractmethod
    def contract_specs(self) -> dict[str, ContractSpec]:
        """Return {symbol: ContractSpec} for every tradable symbol."""

    # --- Market data ----------------------------------------------------
    @abstractmethod
    def fetch_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV candles.

        Returns DataFrame with columns: timestamp, open, high, low, close, volume.
        `timestamp` is bar open time in ms since epoch.
        """

    @abstractmethod
    def fetch_current_price(self, symbol: str) -> float:
        """Latest index/mark price for a symbol."""

    @abstractmethod
    def fetch_funding_rate(self, symbol: str) -> float:
        """Current funding rate (per funding interval, not annualized)."""

    # --- Account & positions --------------------------------------------
    @abstractmethod
    def get_cash_balance_usd(self) -> float:
        """Cash available for trading (not including position margin)."""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """Return {symbol: Position} for all open positions."""

    @abstractmethod
    def get_equity_usd(self) -> float:
        """Total equity = cash + sum(unrealized PnL)."""

    # --- Order execution ------------------------------------------------
    @abstractmethod
    def place_market_order(
        self,
        symbol: str,
        target_notional_usd: float,
        dry_run: bool = False,
    ) -> OrderResult:
        """
        Move to a target signed notional.

        Computes delta from current position, converts to integer contracts,
        and places a market order. If dry_run=True, logs the intended order
        and returns an OrderResult with success=True but no live submission.
        """

    # --- Contract quantization helpers ----------------------------------
    def notional_to_contracts(
        self,
        symbol: str,
        target_notional_usd: float,
        current_price: float,
        round_mode: str = "nearest",
    ) -> int:
        """
        Convert a signed $ notional to a signed integer contract count.

        round_mode:
            "nearest" - round to nearest integer (default)
            "down"    - floor absolute value (conservative entry)
            "up"      - ceil absolute value (full exit)
        """
        spec = self.contract_specs()[symbol]
        contract_notional = spec.contract_size * current_price
        if contract_notional <= 0:
            return 0
        raw = target_notional_usd / contract_notional
        sign = 1 if raw >= 0 else -1
        abs_raw = abs(raw)
        if round_mode == "down":
            return sign * int(abs_raw)
        if round_mode == "up":
            import math
            return sign * int(math.ceil(abs_raw))
        return sign * int(round(abs_raw))

    def contracts_to_notional(
        self,
        symbol: str,
        contracts: float,
        price: float,
    ) -> float:
        """Signed contract count -> signed USD notional."""
        spec = self.contract_specs()[symbol]
        return contracts * spec.contract_size * price
