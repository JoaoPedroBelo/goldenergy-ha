"""Tests for the statistics importer's decision logic.

``build_statistic_points`` is the part worth pinning down: it spreads the
consumption a reading reports over the days that reading actually covers, and
turns an absolute meter index into the monotonic ``sum`` the dashboard diffs.
"""

from datetime import date, timedelta

from homeassistant.util import dt as dt_util
import pytest

from custom_components.goldenergy.statistics import (
    _anchor_sum,
    _metadata,
    _ts_to_local_iso,
    build_cost_points,
    build_statistic_points,
    cost_statistic_id,
    electricity_statistic_id,
    gas_energy_statistic_id,
    gas_volume_statistic_id,
)

READINGS = [
    {"iso": "2026-05-15", "index": 250.0},
    {"iso": "2026-06-11", "index": 270.0},
    {"iso": "2026-07-11", "index": 300.0},
    {"iso": "2026-07-30", "index": 320.0},
]


def sums_by_iso(points: list[dict]) -> dict[str, float]:
    """Index a point list by day for readable assertions."""
    return {p["iso"]: p["sum"] for p in points}


def test_every_day_between_the_first_and_last_reading_gets_a_point():
    points = build_statistic_points(READINGS, 0.0)

    span = (date(2026, 7, 30) - date(2026, 5, 15)).days + 1
    assert len(points) == span
    days = [date.fromisoformat(p["iso"]) for p in points]
    assert days == [days[0] + timedelta(days=i) for i in range(span)]


def test_the_oldest_reading_contributes_no_consumption():
    """What passed through the meter before the first reading is not knowable."""
    assert build_statistic_points(READINGS, 0.0)[0]["sum"] == 0.0


def test_each_reading_day_lands_on_the_exact_meter_delta():
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    assert at["2026-06-11"] == pytest.approx(20.0)
    assert at["2026-07-11"] == pytest.approx(50.0)
    assert at["2026-07-30"] == pytest.approx(70.0)


def test_a_reading_delta_is_spread_evenly_over_the_days_it_covers():
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    per_day = 20.0 / 27
    assert at["2026-05-16"] == pytest.approx(per_day, abs=1e-3)
    assert at["2026-05-20"] == pytest.approx(5 * per_day, abs=1e-3)


def test_a_month_does_not_absorb_the_previous_month_s_consumption():
    """Regression test for dating a whole delta at the reading day."""
    at = sums_by_iso(build_statistic_points(READINGS, 0.0))

    july = at["2026-07-30"] - at["2026-06-30"]

    assert july == pytest.approx(30.0 * 11 / 30 + 20.0, abs=1e-3)
    assert july < 50.0


def test_state_interpolates_the_meter_index_between_readings():
    points = build_statistic_points(READINGS, 0.0)
    states = [p["state"] for p in points]

    assert points[0]["state"] == 250.0
    assert points[-1]["state"] == pytest.approx(320.0)
    assert states == sorted(states)


def test_the_running_sum_continues_from_the_stored_anchor():
    at = sums_by_iso(build_statistic_points(READINGS, 100.0))

    assert at["2026-05-15"] == 100.0
    assert at["2026-07-30"] == pytest.approx(170.0)


def test_a_single_reading_yields_a_single_point():
    """A brand-new contract has exactly one (initial) reading."""
    assert build_statistic_points([{"iso": "2026-03-02", "index": 120.0}], 0.0) == [
        {"iso": "2026-03-02", "state": 120.0, "sum": 0.0}
    ]


def test_empty_series_yields_no_points():
    assert build_statistic_points([], 0.0) == []


def test_sum_never_decreases_when_the_meter_goes_backwards():
    replaced_meter = [
        {"iso": "2026-07-01", "index": 320.0},
        {"iso": "2026-07-15", "index": 2.0},
        {"iso": "2026-07-30", "index": 9.0},
    ]

    points = build_statistic_points(replaced_meter, 0.0)
    sums = [p["sum"] for p in points]

    assert sums == sorted(sums)
    assert sums_by_iso(points)["2026-07-30"] == pytest.approx(7.0)
    assert points[-1]["state"] == pytest.approx(9.0)


def test_unsorted_input_is_ordered_before_accumulating():
    shuffled = [READINGS[2], READINGS[0], READINGS[3], READINGS[1]]

    assert build_statistic_points(shuffled, 0.0) == build_statistic_points(
        READINGS, 0.0
    )


def test_two_readings_for_one_day_collapse_to_the_highest_index():
    same_day = [
        {"iso": "2026-07-11", "index": 300.0},
        {"iso": "2026-07-30", "index": 310.0},
        {"iso": "2026-07-30", "index": 320.0},
    ]

    points = build_statistic_points(same_day, 0.0)

    assert points[-1]["state"] == pytest.approx(320.0)
    assert points[-1]["sum"] == pytest.approx(20.0)


def stored_at(*days: tuple[str, float]) -> dict[float, tuple[float, float]]:
    """Build a stored series keyed the way the recorder reports it."""
    return {
        dt_util.start_of_local_day(date.fromisoformat(iso)).timestamp(): (0.0, total)
        for iso, total in days
    }


def test_a_fresh_install_anchors_the_running_total_at_zero():
    assert _anchor_sum({}, "2026-05-15") == 0.0


def test_the_anchor_is_the_total_already_stored_for_the_oldest_reading():
    stored = stored_at(("2026-05-15", 113.0), ("2026-06-11", 123.0))

    assert _anchor_sum(stored, "2026-05-15") == 113.0


def test_a_slid_window_anchors_on_the_last_total_before_it():
    stored = stored_at(("2024-01-10", 40.0), ("2025-02-20", 90.0))

    assert _anchor_sum(stored, "2026-05-15") == 90.0


def test_totals_stored_after_the_oldest_reading_are_ignored():
    stored = stored_at(("2026-06-11", 123.0), ("2026-07-30", 137.0))

    assert _anchor_sum(stored, "2026-05-15") == 0.0


def test_recorder_timestamps_are_read_as_local_dates():
    seconds = 1785456000  # 2026-07-31T12:00:00Z
    assert _ts_to_local_iso(seconds) == _ts_to_local_iso(seconds * 1000)
    assert _ts_to_local_iso(seconds).startswith("2026-07-3")


INVOICES = [
    {"iso": "2026-05-16", "total": 30.11},
    {"iso": "2026-06-20", "total": 25.67},
    {"iso": "2026-07-20", "total": 12.34},
]


def test_cost_points_accumulate_invoice_totals():
    points = build_cost_points(INVOICES, None, 0.0)

    assert [p["state"] for p in points] == [30.11, 25.67, 12.34]
    assert [p["sum"] for p in points] == [30.11, 55.78, 68.12]


def test_cost_points_continue_from_what_is_stored():
    points = build_cost_points(INVOICES, "2026-06-20", 55.78)

    assert [p["iso"] for p in points] == ["2026-07-20"]
    assert points[0]["sum"] == 68.12


def test_cost_points_are_empty_when_nothing_is_new():
    assert build_cost_points(INVOICES, "2026-07-20", 68.12) == []
    assert build_cost_points([], None, 0.0) == []


def test_a_credit_note_never_makes_the_cost_sum_fall():
    with_credit = [
        {"iso": "2026-05-16", "total": 30.11},
        {"iso": "2026-06-20", "total": -5.00},
        {"iso": "2026-07-20", "total": 12.34},
    ]

    sums = [p["sum"] for p in build_cost_points(with_credit, None, 0.0)]

    assert sums == [30.11, 30.11, 42.45]


def test_metadata_uses_has_mean_on_a_core_without_mean_type():
    """Older cores reject unknown keys: ``StatisticsMeta(**metadata)``."""
    from homeassistant.components.recorder.models import StatisticMetaData

    metadata = _metadata("goldenergy:x", "X", "m³")

    assert set(metadata) <= set(StatisticMetaData.__annotations__)
    if "mean_type" not in StatisticMetaData.__annotations__:
        assert metadata["has_mean"] is False


def test_metadata_uses_mean_type_and_unit_class_on_a_newer_core(monkeypatch):
    """Newer cores stop importing metadata without them (2026.11)."""
    from enum import IntEnum

    from homeassistant.components.recorder import models

    class FakeMeanType(IntEnum):
        NONE = 0

    class NewerMetaData(dict):
        __annotations__ = {
            "mean_type": int,
            "has_sum": bool,
            "name": str,
            "source": str,
            "statistic_id": str,
            "unit_class": str,
            "unit_of_measurement": str,
        }

    monkeypatch.setattr(models, "StatisticMetaData", NewerMetaData)
    monkeypatch.setattr(models, "StatisticMeanType", FakeMeanType, raising=False)

    volume = _metadata("goldenergy:v", "V", "m³")
    energy = _metadata("goldenergy:e", "E", "kWh")
    cost = _metadata("goldenergy:c", "C", "EUR")

    assert "has_mean" not in volume
    assert volume["mean_type"] is FakeMeanType.NONE
    assert volume["unit_class"] == "volume"
    assert energy["unit_class"] == "energy"
    # A currency has no unit converter.
    assert cost["unit_class"] is None
    assert set(volume) == set(NewerMetaData.__annotations__)


def test_the_four_statistic_ids_are_distinct_and_namespaced():
    ids = {
        gas_volume_statistic_id("CG0000000"),
        gas_energy_statistic_id("CG0000000"),
        electricity_statistic_id("CG0000000"),
        cost_statistic_id("CG0000000"),
    }

    assert len(ids) == 4
    assert all(i.startswith("goldenergy:") for i in ids)
    assert gas_volume_statistic_id("CG0000000") == "goldenergy:gas_volume_cg0000000"
    # Different accounts must not share a statistic.
    assert cost_statistic_id("CG1") != cost_statistic_id("CG2")
