"""Config loader with defaults and type-safe accessors."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class CircuitBreakerConfig:
    max_dd_from_high_water_pct: float = 10.0
    max_dd_24h_pct: float = 5.0
    max_loss_daily_usd: float = 500.0
    require_manual_resume: bool = True


@dataclass
class FlashCrashGuardConfig:
    enabled: bool = True
    poll_interval_sec: int = 5
    per_position_move_pct: float = 3.0
    portfolio_unrealized_pct: float = -2.0


@dataclass
class PositionLimitsConfig:
    max_leverage: float = 1.3
    max_notional_per_coin_usd: float = 5000.0
    max_concentration_pct: float = 50.0
    max_open_positions: int = 3
    allowed_symbols: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])


@dataclass
class DataGuardConfig:
    stale_candle_max_age_intervals: int = 2
    price_deviation_pct: float = 10.0
    funding_alert_per_8h_bps: float = 50.0


@dataclass
class OrderSafetyConfig:
    max_slippage_bps: float = 15.0
    order_timeout_sec: int = 30
    max_retries: int = 3
    retry_backoff_sec: list[int] = field(default_factory=lambda: [2, 5, 10])
    fill_verification: bool = True


@dataclass
class CorrelationGuardConfig:
    enabled: bool = True
    min_positions_losing: int = 3
    unrealized_threshold_pct: float = -1.5
    exposure_reduction_pct: float = 50.0
    cooldown_intervals: int = 1


@dataclass
class AlertsConfig:
    telegram_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    log_file: str = "logs/risk_manager.log"
    heartbeat_interval_hours: int = 6
    daily_summary_hour_utc: int = 14


@dataclass
class WatchdogConfig:
    heartbeat_interval_sec: int = 60
    max_silent_sec: int = 300
    heartbeat_file: str = "state/heartbeat"
    kill_flag_file: str = "state/kill.flag"


@dataclass
class RiskConfig:
    nominal_capital_usd: float = 10000.0
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    flash_crash_guard: FlashCrashGuardConfig = field(default_factory=FlashCrashGuardConfig)
    position_limits: PositionLimitsConfig = field(default_factory=PositionLimitsConfig)
    data_guard: DataGuardConfig = field(default_factory=DataGuardConfig)
    order_safety: OrderSafetyConfig = field(default_factory=OrderSafetyConfig)
    correlation_guard: CorrelationGuardConfig = field(default_factory=CorrelationGuardConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "RiskConfig":
        """Load config from YAML file. Falls back to defaults if yaml unavailable."""
        if yaml is None:
            return cls()
        if path is None:
            path = Path(__file__).parent / "config.yaml"
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        account = data.get("account", {})
        return cls(
            nominal_capital_usd=float(account.get("nominal_capital_usd", 10000)),
            circuit_breaker=CircuitBreakerConfig(**(data.get("circuit_breaker") or {})),
            flash_crash_guard=FlashCrashGuardConfig(**(data.get("flash_crash_guard") or {})),
            position_limits=PositionLimitsConfig(**(data.get("position_limits") or {})),
            data_guard=DataGuardConfig(**(data.get("data_guard") or {})),
            order_safety=OrderSafetyConfig(**(data.get("order_safety") or {})),
            correlation_guard=CorrelationGuardConfig(**(data.get("correlation_guard") or {})),
            alerts=AlertsConfig(**(data.get("alerts") or {})),
            watchdog=WatchdogConfig(**(data.get("watchdog") or {})),
        )
