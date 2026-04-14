"""Tests for monitoring/event_log.py — JSONL emitters for per-fix attribution."""
import json
import os
import tempfile
from datetime import datetime, timezone

from monitoring import event_log


def _read_jsonl(path):
    """Read all records from a JSONL file."""
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _today_filename(prefix):
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"


class TestSkipEventLogging:
    def test_writes_record_with_correct_schema(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        event_log.log_skip_event(
            symbol="BTC",
            target_notional_usd=3100.0,
            current_notional_usd=3000.0,
            delta_notional_usd=100.0,
            implied_fee_avoided_usd=0.31,
            tolerance_used_usd=200.0,
            skip_reason="below tolerance",
        )
        records = _read_jsonl(tmp_path / _today_filename("skip_events"))
        assert len(records) == 1
        r = records[0]
        # Required fields per PLANNED_FIXES.md
        assert r["symbol"] == "BTC"
        assert r["target_notional_usd"] == 3100.0
        assert r["current_notional_usd"] == 3000.0
        assert r["delta_notional_usd"] == 100.0
        assert r["implied_fee_avoided_usd"] == 0.31
        assert r["tolerance_used_usd"] == 200.0
        assert r["skip_reason"] == "below tolerance"
        assert isinstance(r["ts"], int)

    def test_appends_multiple(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        for sym in ["BTC", "ETH", "SOL"]:
            event_log.log_skip_event(
                symbol=sym, target_notional_usd=100, current_notional_usd=50,
                delta_notional_usd=50, implied_fee_avoided_usd=0.05,
                tolerance_used_usd=200,
            )
        records = _read_jsonl(tmp_path / _today_filename("skip_events"))
        assert len(records) == 3
        assert [r["symbol"] for r in records] == ["BTC", "ETH", "SOL"]


class TestHwmTickLogging:
    def test_writes_dual_track(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        event_log.log_hwm_tick(
            mtm_equity=10100.0,
            realized_equity=10000.0,
            hwm_new_realized=10000.0,
            hwm_old_mtm=10880.0,        # phantom peak from earlier MTM spike
            dd_new_pct=0.0,             # no DD vs realized HWM
            dd_old_pct=7.16,            # would have triggered under old logic
            threshold_pct=10.0,
        )
        records = _read_jsonl(tmp_path / _today_filename("hwm_track"))
        assert len(records) == 1
        r = records[0]
        assert r["hwm_new_realized"] == 10000.0
        assert r["hwm_old_mtm"] == 10880.0
        assert r["did_new_trigger"] is False
        # 7.16% < 10% threshold — would_old_trigger is also False here
        assert r["would_old_trigger"] is False

    def test_phantom_trigger_detected(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        # Old HWM phantom = $10880, MTM dropped to $9665 — old DD 11.2% triggers, new doesn't
        event_log.log_hwm_tick(
            mtm_equity=9665.0,
            realized_equity=10000.0,
            hwm_new_realized=10000.0,
            hwm_old_mtm=10880.0,
            dd_new_pct=3.35,
            dd_old_pct=11.17,
            threshold_pct=10.0,
        )
        records = _read_jsonl(tmp_path / _today_filename("hwm_track"))
        r = records[0]
        assert r["would_old_trigger"] is True   # 11.17 >= 10
        assert r["did_new_trigger"] is False    # 3.35 < 10
        # This is a phantom-trigger-prevented event we'd count in attribution


class TestHaltEventLogging:
    def test_writes_halt_record(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        event_log.log_halt_event(
            trigger_reason="DD 11% from HWM $10000",
            hwm_at_trigger=10000.0,
            equity_at_trigger=8900.0,
            open_positions={"BTC": 3000.0, "ETH": -1500.0},
            marks_at_trigger={"BTC": 75000.0, "ETH": 2400.0},
        )
        records = _read_jsonl(tmp_path / _today_filename("halt_events"))
        assert len(records) == 1
        r = records[0]
        assert "DD" in r["trigger_reason"]
        assert r["open_positions"] == {"BTC": 3000.0, "ETH": -1500.0}
        assert r["marks_at_trigger"]["BTC"] == 75000.0


class TestCooldownEventLogging:
    def test_writes_cooldown_clear(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        event_log.log_cooldown_event(
            ts_trigger_ms=1776000000000,
            ts_cleared_ms=1776007200000,  # 2 hours later
            cooldown_duration_sec=7200.0,
            dd_at_clear_pct=4.5,
        )
        records = _read_jsonl(tmp_path / _today_filename("cooldown_events"))
        assert len(records) == 1
        r = records[0]
        assert r["cooldown_duration_sec"] == 7200.0
        assert r["dd_at_clear_pct"] == 4.5


class TestFailSoft:
    def test_unwritable_dir_does_not_raise(self, tmp_path):
        """If log dir is read-only, logging should warn but not raise."""
        # Set log dir to a path that doesn't exist and can't be created
        event_log.set_log_dir("/nonexistent/path/that/cannot/be/created/xyz")
        # Should not raise — fail-soft
        event_log.log_skip_event(
            symbol="BTC", target_notional_usd=100, current_notional_usd=50,
            delta_notional_usd=50, implied_fee_avoided_usd=0.05,
            tolerance_used_usd=200,
        )


class TestBtcPairedTradeLogging:
    """Fix 6: paired (maker, taker) trade observations."""

    def test_writes_full_paired_record(self, tmp_path):
        event_log.set_log_dir(tmp_path)
        event_log.log_btc_paired_trade(
            signal_ts_ms=1776000000000,
            signal_size_usd=3000.0,
            taker_fill_px=75050.0,
            taker_fill_time_ms=1776000000500,
            taker_fee_usd=0.45,
            maker_limit_px=75000.0,
            maker_fill_px=75000.0,
            maker_fill_time_ms=1776000060000,
            maker_fallback=False,
            maker_fallback_penalty_bps=0.0,
            maker_fee_usd=0.38,
            realized_vol_15m_at_signal_bps=12.5,
            bid=75000.0, ask=75100.0, spread_bps=13.3,
        )
        records = _read_jsonl(tmp_path / _today_filename("maker_pilot"))
        assert len(records) == 1
        r = records[0]
        assert r["signal_size"] == 3000.0
        assert r["taker_fill_px"] == 75050.0
        assert r["maker_fill_px"] == 75000.0
        assert r["maker_fallback_bool"] is False
        assert r["realized_vol_15m_at_signal"] == 12.5

    def test_records_fallback_event(self, tmp_path):
        """When maker times out and falls back to taker, fields reflect that."""
        event_log.set_log_dir(tmp_path)
        event_log.log_btc_paired_trade(
            signal_ts_ms=1776000000000,
            signal_size_usd=3000.0,
            taker_fill_px=75050.0,
            taker_fill_time_ms=1776000000500,
            taker_fee_usd=0.45,
            maker_limit_px=75000.0,
            maker_fill_px=75100.0,         # filled at fallback price
            maker_fill_time_ms=1776000300000,  # 5 min later
            maker_fallback=True,
            maker_fallback_penalty_bps=13.3,
            maker_fee_usd=0.45,            # taker fee since fallback
            realized_vol_15m_at_signal_bps=20.0,
        )
        records = _read_jsonl(tmp_path / _today_filename("maker_pilot"))
        r = records[0]
        assert r["maker_fallback_bool"] is True
        assert r["maker_fallback_penalty_bps"] == 13.3

    def test_records_skip_reason(self, tmp_path):
        """When maker leg is skipped entirely, skipped_reason populated, fill fields None."""
        event_log.set_log_dir(tmp_path)
        event_log.log_btc_paired_trade(
            signal_ts_ms=1776000000000,
            signal_size_usd=3000.0,
            taker_fill_px=75050.0,
            taker_fill_time_ms=1776000000500,
            taker_fee_usd=0.45,
            maker_limit_px=None,
            maker_fill_px=None,
            maker_fill_time_ms=None,
            maker_fallback=False,
            maker_fallback_penalty_bps=0.0,
            maker_fee_usd=0.0,
            realized_vol_15m_at_signal_bps=55.0,  # extreme vol → skip
            skipped_reason="extreme_vol",
        )
        records = _read_jsonl(tmp_path / _today_filename("maker_pilot"))
        r = records[0]
        assert r["maker_fill_px"] is None
        assert r["skipped_reason"] == "extreme_vol"
