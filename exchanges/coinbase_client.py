"""
Coinbase Financial Markets client.

Trades CFTC-regulated perpetual-style futures (the "nano PERP" contracts
that actually expire Dec 2030 but behave as perps). Uses the official
`coinbase-advanced-py` SDK with JWT ES256 auth.

Product IDs (discovered 2026-04-11):
    BTC -> BIP-20DEC30-CDE  (contract size 0.01 BTC)
    ETH -> ETP-20DEC30-CDE  (contract size 0.1 ETH)
    SOL -> SLP-20DEC30-CDE  (contract size 5 SOL)

Key quirks handled here:
  * Coinbase returns numeric fields as strings - we cast consistently
  * Contract margin rate != notional value; UI shows margin, we track notional
  * Hourly funding (vs 8h on Hyperliquid) - funding_time exposed per product
  * Positions for futures come from a different endpoint than spot balances
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd

from .base import ContractSpec, ExchangeClient, OrderResult, Position

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardcoded product specs for the 3 coins we trade
# ---------------------------------------------------------------------------
# Discovered from scripts/find_us_perps.py against the live API.
# If Coinbase ever adds new perp contracts, expand this table.

_PRODUCT_SPECS: dict[str, ContractSpec] = {
    "BTC": ContractSpec(
        symbol="BTC",
        product_id="BIP-20DEC30-CDE",
        contract_size=0.01,
        price_increment=1.0,
        min_notional_usd=10.0,
        overnight_margin_rate=0.2456,
        intraday_margin_rate=0.10,
    ),
    "ETH": ContractSpec(
        symbol="ETH",
        product_id="ETP-20DEC30-CDE",
        contract_size=0.1,
        price_increment=0.01,
        min_notional_usd=10.0,
        overnight_margin_rate=0.2466,
        intraday_margin_rate=0.10,
    ),
    "SOL": ContractSpec(
        symbol="SOL",
        product_id="SLP-20DEC30-CDE",
        contract_size=5.0,
        price_increment=0.001,
        min_notional_usd=10.0,
        overnight_margin_rate=0.25,  # placeholder, refreshed from API on init
        intraday_margin_rate=0.10,
    ),
}

# Granularity mapping for candle intervals.
# Coinbase Advanced Trade API uses string enums like ONE_MINUTE / FIFTEEN_MINUTE.
_GRANULARITY_MAP = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "2h": "TWO_HOUR",
    "6h": "SIX_HOUR",
    "1d": "ONE_DAY",
}


def _to_float(value, default: float = 0.0) -> float:
    """Coinbase returns numerics as strings - cast defensively."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_dict(obj) -> dict:
    """Some SDK responses are dicts, others are objects with __dict__."""
    if isinstance(obj, dict):
        return obj
    return getattr(obj, "__dict__", {}) or {}


class CoinbaseClient(ExchangeClient):
    """Live Coinbase perpetual futures client."""

    def __init__(
        self,
        key_file: Optional[str] = None,
        dry_run_default: bool = True,
    ):
        """
        Args:
            key_file: Path to Coinbase API key JSON. Defaults to
                secrets/coinbase_api.json relative to repo root.
            dry_run_default: If True, place_market_order() defaults to dry_run.
                Flip this to False explicitly when ready for live execution.
        """
        from coinbase.rest import RESTClient  # local import; optional dep

        if key_file is None:
            repo_root = Path(__file__).resolve().parents[1]
            key_file = str(repo_root / "secrets" / "coinbase_api.json")

        with open(key_file) as f:
            key_data = json.load(f)

        self._client = RESTClient(
            api_key=key_data["name"],
            api_secret=key_data["privateKey"],
        )
        self._dry_run_default = dry_run_default
        self._product_specs = dict(_PRODUCT_SPECS)
        self._refresh_margin_rates()

    # ---------- identification ----------
    @property
    def name(self) -> str:
        return "coinbase"

    def contract_specs(self) -> dict[str, ContractSpec]:
        return dict(self._product_specs)

    def _product_id(self, symbol: str) -> str:
        return self._product_specs[symbol].product_id

    def _refresh_margin_rates(self) -> None:
        """Pull fresh margin rates from the API for SOL (which was a placeholder)."""
        try:
            for sym, spec in self._product_specs.items():
                try:
                    resp = self._client.get_product(product_id=spec.product_id)
                    pd_ = _to_dict(resp)
                    details = pd_.get("future_product_details") or {}
                    if isinstance(details, dict):
                        overnight = (details.get("overnight_margin_rate") or {})
                        intraday = (details.get("intraday_margin_rate") or {})
                        if isinstance(overnight, dict) and overnight.get("long_margin_rate"):
                            spec.overnight_margin_rate = _to_float(
                                overnight.get("long_margin_rate"), spec.overnight_margin_rate
                            )
                        if isinstance(intraday, dict) and intraday.get("long_margin_rate"):
                            spec.intraday_margin_rate = _to_float(
                                intraday.get("long_margin_rate"), spec.intraday_margin_rate
                            )
                except Exception as e:
                    logger.debug("Could not refresh margin for %s: %s", sym, e)
        except Exception as e:
            logger.debug("Margin refresh skipped: %s", e)

    # ---------- market data ----------
    def fetch_current_price(self, symbol: str) -> float:
        """Live mark price for a symbol."""
        pid = self._product_id(symbol)
        resp = self._client.get_product(product_id=pid)
        pd_ = _to_dict(resp)
        # Prefer index_price (fair value) over settlement_price (stale)
        details = pd_.get("future_product_details") or {}
        if isinstance(details, dict):
            idx = _to_float(details.get("index_price"))
            if idx > 0:
                return idx
        # Fallback: product-level price
        return _to_float(pd_.get("price"))

    def fetch_funding_rate(self, symbol: str) -> float:
        """Current funding rate per 8h (Coinbase settles hourly; we report 8h equivalent)."""
        pid = self._product_id(symbol)
        resp = self._client.get_product(product_id=pid)
        pd_ = _to_dict(resp)
        details = pd_.get("future_product_details") or {}
        if isinstance(details, dict):
            # Coinbase returns hourly funding rate; multiply by 8 for 8h equivalent
            hourly = _to_float(details.get("funding_rate"))
            return hourly * 8
        return 0.0

    def fetch_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles. Coinbase caps at 350 candles per request,
        so we chunk if the range is larger.
        """
        granularity = _GRANULARITY_MAP.get(interval)
        if granularity is None:
            raise ValueError(f"Unsupported interval: {interval}")

        pid = self._product_id(symbol)
        interval_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400,
        }[interval]

        start_sec = start_ms // 1000
        end_sec = end_ms // 1000
        chunk_seconds = 300 * interval_seconds  # stay under 350 limit

        frames = []
        cursor = start_sec
        while cursor < end_sec:
            chunk_end = min(cursor + chunk_seconds, end_sec)
            try:
                resp = self._client.get_public_candles(
                    product_id=pid,
                    start=str(cursor),
                    end=str(chunk_end),
                    granularity=granularity,
                )
                pd_ = _to_dict(resp)
                candles = pd_.get("candles", [])
                if candles is None:
                    candles = []
                for c in candles:
                    cd = _to_dict(c)
                    frames.append({
                        "timestamp": int(_to_float(cd.get("start"))) * 1000,
                        "open": _to_float(cd.get("open")),
                        "high": _to_float(cd.get("high")),
                        "low": _to_float(cd.get("low")),
                        "close": _to_float(cd.get("close")),
                        "volume": _to_float(cd.get("volume")),
                    })
            except Exception as e:
                logger.warning("Candle fetch failed for %s %s: %s", symbol, interval, e)
            cursor = chunk_end

        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(frames)
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
        return df

    # ---------- account ----------
    def get_cash_balance_usd(self) -> float:
        """USD available for futures trading."""
        try:
            resp = self._client.get_accounts()
            pd_ = _to_dict(resp)
            accts = pd_.get("accounts", [])
            if accts is None:
                accts = []
            # Find USD account(s) and sum available balance
            total_usd = 0.0
            for a in accts:
                ad = _to_dict(a)
                currency = ad.get("currency", "")
                if currency == "USD":
                    bal = ad.get("available_balance") or {}
                    if isinstance(bal, dict):
                        total_usd += _to_float(bal.get("value"))
            return total_usd
        except Exception as e:
            logger.error("get_cash_balance_usd failed: %s", e)
            return 0.0

    def get_positions(self) -> dict[str, Position]:
        """
        Fetch open futures positions.

        Uses the CFM positions endpoint. Returns {symbol: Position} for any
        symbol we trade (BTC/ETH/SOL); symbols not in our universe are ignored.
        """
        positions: dict[str, Position] = {}
        try:
            # The SDK method for futures positions
            resp = self._client.list_futures_positions()
            pd_ = _to_dict(resp)
            pos_list = pd_.get("positions", [])
            if pos_list is None:
                pos_list = []
            product_to_symbol = {
                spec.product_id: sym
                for sym, spec in self._product_specs.items()
            }
            for p in pos_list:
                pd_p = _to_dict(p)
                pid = pd_p.get("product_id", "")
                if pid not in product_to_symbol:
                    continue
                symbol = product_to_symbol[pid]
                spec = self._product_specs[symbol]

                # Contracts: positive = long, negative = short
                # Coinbase may use "net_size", "number_of_contracts", or similar
                contracts = _to_float(
                    pd_p.get("number_of_contracts")
                    or pd_p.get("net_size")
                    or pd_p.get("contracts")
                )
                # Side flag may indicate short
                side = pd_p.get("side", "") or pd_p.get("position_side", "")
                if isinstance(side, str) and "SHORT" in side.upper() and contracts > 0:
                    contracts = -contracts

                entry_price = _to_float(pd_p.get("entry_vwap") or pd_p.get("avg_entry_price"))
                mark_price = _to_float(pd_p.get("current_price") or pd_p.get("mark_price"))
                if mark_price == 0:
                    mark_price = self.fetch_current_price(symbol)
                unrealized = _to_float(pd_p.get("unrealized_pnl"))

                notional = contracts * spec.contract_size * mark_price
                positions[symbol] = Position(
                    symbol=symbol,
                    contracts=contracts,
                    notional_usd=notional,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    unrealized_pnl_usd=unrealized,
                )
        except Exception as e:
            # If the endpoint doesn't exist or fails, return empty - strategy
            # starts flat. Safer than crashing.
            logger.warning("get_positions failed (returning empty): %s", e)
        return positions

    def get_equity_usd(self) -> float:
        """Total equity = cash + unrealized PnL across all positions."""
        cash = self.get_cash_balance_usd()
        pnl = sum(p.unrealized_pnl_usd for p in self.get_positions().values())
        return cash + pnl

    # ---------- order execution ----------
    def place_market_order(
        self,
        symbol: str,
        target_notional_usd: float,
        dry_run: Optional[bool] = None,
    ) -> OrderResult:
        """
        Move position to target_notional_usd (signed).

        Steps:
          1. Get current price for the symbol
          2. Get current position (if any)
          3. Convert current + target to contract counts
          4. Compute delta contracts to trade
          5. If abs(delta) < 1, skip (nothing to do)
          6. Place market order for delta contracts (or log if dry_run)
        """
        if dry_run is None:
            dry_run = self._dry_run_default

        spec = self._product_specs.get(symbol)
        if spec is None:
            return OrderResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side="NONE",
                contracts=0,
                notional_usd=0,
                fill_price=None,
                error_message=f"Unknown symbol: {symbol}",
                dry_run=dry_run,
            )

        try:
            current_price = self.fetch_current_price(symbol)
        except Exception as e:
            return OrderResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side="NONE",
                contracts=0,
                notional_usd=0,
                fill_price=None,
                error_message=f"Price fetch failed: {e}",
                dry_run=dry_run,
            )

        if current_price <= 0:
            return OrderResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side="NONE",
                contracts=0,
                notional_usd=0,
                fill_price=None,
                error_message="Invalid price (<=0)",
                dry_run=dry_run,
            )

        # Current position (contracts)
        positions = self.get_positions()
        current_contracts = positions.get(symbol).contracts if symbol in positions else 0

        # Target contracts (rounded to nearest for best leverage match at small capital)
        target_contracts = self.notional_to_contracts(
            symbol, target_notional_usd, current_price, round_mode="nearest"
        )

        delta_contracts = int(target_contracts - current_contracts)
        if delta_contracts == 0:
            return OrderResult(
                success=True,
                order_id=None,
                symbol=symbol,
                side="SKIP",
                contracts=0,
                notional_usd=0,
                fill_price=current_price,
                dry_run=dry_run,
            )

        side = "BUY" if delta_contracts > 0 else "SELL"
        abs_contracts = abs(delta_contracts)
        notional = abs_contracts * spec.contract_size * current_price

        # Minimum notional check
        if notional < spec.min_notional_usd:
            return OrderResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                contracts=delta_contracts,
                notional_usd=notional * (1 if delta_contracts > 0 else -1),
                fill_price=current_price,
                error_message=f"Below min notional ${spec.min_notional_usd}",
                dry_run=dry_run,
            )

        if dry_run:
            logger.info(
                "[DRY RUN] %s %s %d contracts ($%.2f notional) @ $%.2f",
                side, symbol, abs_contracts, notional, current_price,
            )
            return OrderResult(
                success=True,
                order_id="DRY_RUN",
                symbol=symbol,
                side=side,
                contracts=delta_contracts,
                notional_usd=notional * (1 if delta_contracts > 0 else -1),
                fill_price=current_price,
                dry_run=True,
            )

        # LIVE order
        try:
            import uuid
            client_order_id = str(uuid.uuid4())
            if side == "BUY":
                resp = self._client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=spec.product_id,
                    base_size=str(abs_contracts),
                )
            else:
                resp = self._client.market_order_sell(
                    client_order_id=client_order_id,
                    product_id=spec.product_id,
                    base_size=str(abs_contracts),
                )
            pd_ = _to_dict(resp)
            success_response = pd_.get("success_response") or {}
            order_id = success_response.get("order_id") if isinstance(success_response, dict) else None
            return OrderResult(
                success=pd_.get("success", False),
                order_id=order_id,
                symbol=symbol,
                side=side,
                contracts=delta_contracts,
                notional_usd=notional * (1 if delta_contracts > 0 else -1),
                fill_price=current_price,
                dry_run=False,
                raw_response=pd_,
            )
        except Exception as e:
            logger.error("Order placement failed: %s", e)
            return OrderResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                contracts=delta_contracts,
                notional_usd=notional * (1 if delta_contracts > 0 else -1),
                fill_price=current_price,
                error_message=str(e),
                dry_run=False,
            )
