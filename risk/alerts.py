"""
Alert dispatch system.

Sends trading alerts to Telegram (optional) and always logs to file.
Works with or without a Telegram bot configured — logs-only mode is the
default so the system doesn't block on Telegram setup.

Alert types represent the severity and category of an event.
"""
from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import AlertsConfig

logger = logging.getLogger(__name__)


class AlertType(enum.Enum):
    TRADE = "TRADE"                   # every entry/exit
    DAILY_SUMMARY = "DAILY"           # daily equity + P&L recap
    WARNING = "WARNING"               # stale data, high funding, anomalies
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"  # drawdown threshold hit
    FLASH_CRASH = "FLASH_CRASH"       # emergency exit triggered
    KILL_SWITCH = "KILL_SWITCH"       # manual halt activated
    HEARTBEAT = "HEARTBEAT"           # periodic "still alive"
    POSITION_LIMIT = "POSITION_LIMIT"  # blocked by position cap
    ORDER_ERROR = "ORDER_ERROR"       # fill failed, retry exhausted
    STARTUP = "STARTUP"               # trader process started
    SHUTDOWN = "SHUTDOWN"             # trader process stopped


@dataclass
class Alert:
    type: AlertType
    message: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "message": self.message,
            "data": self.data,
            "timestamp": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
        }

    def format_plain(self) -> str:
        """Format for log file / text output."""
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        parts = [f"[{ts}] [{self.type.value}] {self.message}"]
        if self.data:
            for k, v in self.data.items():
                parts.append(f"    {k}: {v}")
        return "\n".join(parts)

    def format_telegram(self) -> str:
        """Format for Telegram. Supports basic markdown."""
        emoji_map = {
            AlertType.TRADE: "💱",
            AlertType.DAILY_SUMMARY: "📊",
            AlertType.WARNING: "⚠️",
            AlertType.CIRCUIT_BREAKER: "🛑",
            AlertType.FLASH_CRASH: "🚨",
            AlertType.KILL_SWITCH: "🔴",
            AlertType.HEARTBEAT: "💓",
            AlertType.POSITION_LIMIT: "🚫",
            AlertType.ORDER_ERROR: "❌",
            AlertType.STARTUP: "▶️",
            AlertType.SHUTDOWN: "⏹",
        }
        emoji = emoji_map.get(self.type, "")
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%H:%M UTC")
        lines = [f"{emoji} *{self.type.value}* · {ts}", self.message]
        if self.data:
            lines.append("")
            for k, v in self.data.items():
                lines.append(f"`{k}`: {v}")
        return "\n".join(lines)


class AlertDispatch:
    """Sends alerts to all configured sinks."""

    def __init__(self, config: AlertsConfig, log_dir: Optional[Path] = None):
        self.config = config
        self.log_dir = log_dir or Path(__file__).resolve().parents[1]
        self._log_path = self.log_dir / config.log_file
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_heartbeat = 0.0
        self._telegram_warned = False

    def dispatch(self, alert: Alert) -> None:
        """Route an alert to all enabled sinks."""
        # Always write to log file
        self._write_log(alert)

        # Python logger (for console visibility during dev)
        lvl = {
            AlertType.WARNING: logging.WARNING,
            AlertType.CIRCUIT_BREAKER: logging.ERROR,
            AlertType.FLASH_CRASH: logging.ERROR,
            AlertType.KILL_SWITCH: logging.CRITICAL,
            AlertType.ORDER_ERROR: logging.ERROR,
            AlertType.POSITION_LIMIT: logging.WARNING,
        }.get(alert.type, logging.INFO)
        logger.log(lvl, "[%s] %s", alert.type.value, alert.message)

        # Telegram (if configured)
        if self.config.telegram_enabled:
            self._send_telegram(alert)

    def _write_log(self, alert: Alert) -> None:
        try:
            with open(self._log_path, "a") as f:
                f.write(alert.format_plain() + "\n")
        except Exception as e:
            logger.error("Failed to write alert log: %s", e)

    def _send_telegram(self, alert: Alert) -> None:
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            if not self._telegram_warned:
                logger.warning(
                    "Telegram enabled but bot_token or chat_id missing; alerts going to log only"
                )
                self._telegram_warned = True
            return
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
            r = requests.post(
                url,
                json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": alert.format_telegram(),
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if r.status_code != 200:
                logger.error("Telegram send failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("Telegram dispatch error: %s", e)

    # -- Convenience methods for common alert types --

    def trade(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.TRADE, message, data))

    def warning(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.WARNING, message, data))

    def circuit_breaker(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.CIRCUIT_BREAKER, message, data))

    def flash_crash(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.FLASH_CRASH, message, data))

    def kill_switch(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.KILL_SWITCH, message, data))

    def order_error(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.ORDER_ERROR, message, data))

    def position_limit(self, message: str, **data) -> None:
        self.dispatch(Alert(AlertType.POSITION_LIMIT, message, data))

    def heartbeat(self, message: str = "alive", **data) -> None:
        self.dispatch(Alert(AlertType.HEARTBEAT, message, data))
        self._last_heartbeat = time.time()
