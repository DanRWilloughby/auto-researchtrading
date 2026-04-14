"""Tests for monitoring/aggregate.py — JSON aggregator for dashboard."""
import json
import time
from pathlib import Path

from monitoring import aggregate


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _now_ms() -> int:
    return int(time.time() * 1000)


class TestPhase1Attribution:
    def test_empty_logs_returns_zeros(self, tmp_path):
        result = aggregate.build_phase1_attribution(log_dir=tmp_path)
        assert result["fix_1_skip_bug"]["skip_events_fired"] == 0
        assert result["fix_1_skip_bug"]["fees_avoided_usd"] == 0.0
        assert result["fix_5_hwm"]["phantom_triggers_prevented"] == 0
        assert result["sum_attributed_savings_usd"] == 0.0

    def test_skip_attribution_sums_correctly(self, tmp_path):
        skip_log = tmp_path / "skip_events_2026-04-15.jsonl"
        _write_jsonl(skip_log, [
            {"ts": _now_ms(), "symbol": "BTC", "implied_fee_avoided_usd": 1.50,
             "target_notional_usd": 3100, "current_notional_usd": 3000,
             "delta_notional_usd": 100, "tolerance_used_usd": 200},
            {"ts": _now_ms(), "symbol": "ETH", "implied_fee_avoided_usd": 0.75,
             "target_notional_usd": 3100, "current_notional_usd": 3000,
             "delta_notional_usd": 100, "tolerance_used_usd": 200},
            {"ts": _now_ms(), "symbol": "SOL", "implied_fee_avoided_usd": 1.00,
             "target_notional_usd": 3100, "current_notional_usd": 3000,
             "delta_notional_usd": 100, "tolerance_used_usd": 200},
        ])
        result = aggregate.build_phase1_attribution(log_dir=tmp_path)
        assert result["fix_1_skip_bug"]["skip_events_fired"] == 3
        assert result["fix_1_skip_bug"]["fees_avoided_usd"] == 3.25

    def test_phantom_triggers_counted(self, tmp_path):
        hwm_log = tmp_path / "hwm_track_2026-04-15.jsonl"
        _write_jsonl(hwm_log, [
            # Normal tick — no phantom
            {"ts": _now_ms(), "mtm_equity": 10000, "realized_equity": 10000,
             "hwm_new_realized": 10000, "hwm_old_mtm": 10000,
             "dd_new_pct": 0, "dd_old_pct": 0, "would_old_trigger": False,
             "did_new_trigger": False, "threshold_pct": 10},
            # PHANTOM TRIGGER scenario
            {"ts": _now_ms(), "mtm_equity": 9665, "realized_equity": 10000,
             "hwm_new_realized": 10000, "hwm_old_mtm": 10880,
             "dd_new_pct": 3.35, "dd_old_pct": 11.17, "would_old_trigger": True,
             "did_new_trigger": False, "threshold_pct": 10},
            # Another phantom
            {"ts": _now_ms(), "mtm_equity": 9700, "realized_equity": 10000,
             "hwm_new_realized": 10000, "hwm_old_mtm": 10880,
             "dd_new_pct": 3.0, "dd_old_pct": 10.85, "would_old_trigger": True,
             "did_new_trigger": False, "threshold_pct": 10},
        ])
        result = aggregate.build_phase1_attribution(log_dir=tmp_path)
        assert result["fix_5_hwm"]["phantom_triggers_prevented"] == 2
        assert result["fix_5_hwm"]["ticks_logged"] == 3
        assert result["fix_5_hwm"]["hwm_new_current"] == 10000
        assert result["fix_5_hwm"]["hwm_old_current"] == 10880

    def test_with_baseline_computes_days_live(self, tmp_path):
        baseline = {"phase1_ship_date": "2025-01-01T00:00:00+00:00"}
        result = aggregate.build_phase1_attribution(log_dir=tmp_path, baseline=baseline)
        assert result["days_live"] > 0


class TestKillSwitchStatus:
    def test_no_data_returns_safe_defaults(self, tmp_path):
        result = aggregate.build_kill_switch_status(log_dir=tmp_path)
        assert "switches" in result
        # Should at least include dd_approach + hwm_drift_detection
        names = [s["name"] for s in result["switches"]]
        assert "dd_approach" in names
        assert "hwm_drift_detection" in names

    def test_dd_approach_red_when_close_to_trigger(self, tmp_path):
        hwm_log = tmp_path / "hwm_track_2026-04-15.jsonl"
        _write_jsonl(hwm_log, [
            {"ts": _now_ms(), "mtm_equity": 9100, "realized_equity": 10000,
             "hwm_new_realized": 10000, "hwm_old_mtm": 10000,
             "dd_new_pct": 9.0, "dd_old_pct": 9.0, "would_old_trigger": False,
             "did_new_trigger": False, "threshold_pct": 10},
        ])
        result = aggregate.build_kill_switch_status(log_dir=tmp_path)
        dd_switch = [s for s in result["switches"] if s["name"] == "dd_approach"][0]
        # 9% out of 10% threshold — should be red (above 80% of threshold)
        assert dd_switch["status"] == "red"

    def test_hwm_drift_invariant_red_when_breached(self, tmp_path):
        hwm_log = tmp_path / "hwm_track_2026-04-15.jsonl"
        _write_jsonl(hwm_log, [
            # IMPOSSIBLE state: new HWM > old HWM. Indicates implementation bug.
            {"ts": _now_ms(), "mtm_equity": 10000, "realized_equity": 11000,
             "hwm_new_realized": 11000, "hwm_old_mtm": 10500,
             "dd_new_pct": 0, "dd_old_pct": 0, "would_old_trigger": False,
             "did_new_trigger": False, "threshold_pct": 10},
        ])
        result = aggregate.build_kill_switch_status(log_dir=tmp_path)
        invariant_switch = [s for s in result["switches"] if s["name"] == "hwm_drift_detection"][0]
        assert invariant_switch["status"] == "red"


class TestRecentEvents:
    def test_aggregates_events_from_all_logs(self, tmp_path):
        ts = _now_ms()
        _write_jsonl(tmp_path / "skip_events_2026-04-15.jsonl", [
            {"ts": ts, "symbol": "BTC", "implied_fee_avoided_usd": 1.50,
             "target_notional_usd": 3100, "current_notional_usd": 3000,
             "delta_notional_usd": 100, "tolerance_used_usd": 200},
        ])
        _write_jsonl(tmp_path / "hwm_track_2026-04-15.jsonl", [
            {"ts": ts + 1, "mtm_equity": 9665, "realized_equity": 10000,
             "hwm_new_realized": 10000, "hwm_old_mtm": 10880,
             "dd_new_pct": 3.35, "dd_old_pct": 11.17, "would_old_trigger": True,
             "did_new_trigger": False, "threshold_pct": 10},
        ])
        result = aggregate.build_recent_events(log_dir=tmp_path)
        assert len(result["events"]) == 2
        types = [e["type"] for e in result["events"]]
        assert "skip_fired" in types
        assert "hwm_phantom_prevented" in types

    def test_caps_at_max_events(self, tmp_path):
        # Write many skip events
        records = [
            {"ts": _now_ms() + i, "symbol": "BTC", "implied_fee_avoided_usd": 0.5,
             "target_notional_usd": 100, "current_notional_usd": 50,
             "delta_notional_usd": 50, "tolerance_used_usd": 200}
            for i in range(100)
        ]
        _write_jsonl(tmp_path / "skip_events_2026-04-15.jsonl", records)
        result = aggregate.build_recent_events(log_dir=tmp_path, max_events=10)
        assert len(result["events"]) == 10


class TestWriteAll:
    def test_writes_three_files(self, tmp_path):
        log_dir = tmp_path / "logs"
        out_dir = tmp_path / "out"
        log_dir.mkdir()
        # Write minimal data
        _write_jsonl(log_dir / "skip_events_2026-04-15.jsonl", [
            {"ts": _now_ms(), "symbol": "BTC", "implied_fee_avoided_usd": 1.0,
             "target_notional_usd": 100, "current_notional_usd": 50,
             "delta_notional_usd": 50, "tolerance_used_usd": 200},
        ])
        written = aggregate.write_all(log_dir=log_dir, output_dir=out_dir)
        assert "phase1_attribution.json" in written
        assert "kill_switch_status.json" in written
        assert "recent_events.json" in written
        # Verify files actually exist and are valid JSON
        for path in written.values():
            assert path.exists()
            with open(path) as f:
                json.load(f)  # raises if invalid

    def test_runs_with_no_logs(self, tmp_path):
        """Aggregator should handle empty log directory gracefully."""
        log_dir = tmp_path / "logs"
        out_dir = tmp_path / "out"
        log_dir.mkdir()
        written = aggregate.write_all(log_dir=log_dir, output_dir=out_dir)
        # Should still produce all 3 files (with zero/empty data)
        assert len(written) == 3
