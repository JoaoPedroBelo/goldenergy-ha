"""Tests for the Goldenergy coordinator's normalisation and error mapping."""

from datetime import date
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.goldenergy.api import (
    GoldenergyAboveAverage,
    GoldenergyAuthError,
    GoldenergyConnectionError,
    GoldenergyReadingRejected,
)
from custom_components.goldenergy.const import (
    CONF_BILLING_ACCOUNT,
    CONF_CONVERSION_FACTOR,
    CONF_ENABLE_ELECTRICITY,
    CONF_ENABLE_GAS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_CONVERSION_FACTOR,
    POLL_HOURS,
)
from custom_components.goldenergy.coordinator import (
    GoldenergyCoordinator,
    _parse_date,
    _parse_readings,
    _to_float,
    enabled_energies,
)

from .conftest import TEST_ACCOUNT, TEST_CUI, TEST_USERNAME, gas_reading

TODAY = date(2026, 7, 31)
FACTOR = 11.20808

_CONFIG = {
    CONF_USERNAME: TEST_USERNAME,
    CONF_PASSWORD: "secret",
    CONF_BILLING_ACCOUNT: TEST_ACCOUNT,
}


def normalise(raw: dict, factor: float = FACTOR) -> dict:
    """Normalise ``raw`` as of the fixed test day."""
    return GoldenergyCoordinator._normalise(raw, TODAY, factor)


def test_normalise_extracts_the_gas_meter_reading(raw_payload):
    gas = normalise(raw_payload)["services"]["gas"]

    assert gas["meter_index"] == 320.0
    assert gas["last_reading_iso"] == "2026-07-30"
    assert gas["service_no"] == "S0000000001"
    assert gas["delivery_point"] == TEST_CUI
    assert gas["tier"] == "1"
    assert gas["meter_serial"] == "00000000000000"
    assert gas["smart_meter"] is False


def test_normalise_computes_gas_consumption_between_the_last_two_readings(
    raw_payload,
):
    gas = normalise(raw_payload)["services"]["gas"]

    # 320 m³ on 30-07 minus 300 m³ on 11-07, over 19 days; the cancelled 999 on
    # 20-07 is ignored.
    assert gas["last_consumption"] == 20.0
    assert gas["last_consumption_from"] == "2026-07-11"
    assert gas["last_consumption_days"] == 19


def test_normalise_mirrors_gas_volumes_in_energy(raw_payload):
    """The supplier bills gas in kWh, so every m³ figure needs its energy twin."""
    gas = normalise(raw_payload)["services"]["gas"]

    assert gas["meter_index_energy"] == round(320.0 * FACTOR, 2)
    assert gas["last_consumption_energy"] == round(20.0 * FACTOR, 2)
    assert gas["conversion_factor"] == FACTOR


def test_normalise_sums_the_electricity_registers(raw_payload):
    electricity = normalise(raw_payload)["services"]["electricity"]

    # 1100 + 560 on 30-07, 1000 + 500 on 30-06.
    assert electricity["meter_index"] == 1660.0
    assert electricity["last_consumption"] == 160.0
    assert electricity["last_consumption_days"] == 30
    # Electricity is billed in kWh already: no conversion twin.
    assert "meter_index_energy" not in electricity
    assert "last_consumption_energy" not in electricity


def test_normalise_extracts_the_account(raw_payload):
    data = normalise(raw_payload)

    assert data["account_status"] == 1
    assert data["next_reading_date"] == date(2026, 8, 20)
    assert data["balance"] == 12.34
    assert data["direct_debit"] is True


def test_normalise_extracts_the_referral_programme(raw_payload):
    data = normalise(raw_payload)

    assert data["referral_code"] == "MGM0000000"
    assert data["referral_link"] == "https://amigo.goldenergy.pt/MGM0000000"
    assert data["referral_friends"] == 2
    assert data["referral_earnings"] == 20.0


def test_an_account_without_a_referral_code_has_no_link(raw_payload):
    raw_payload["account"]["mgmVoucherCode"] = "  "
    raw_payload["account"]["memberGetMemberInfo"] = None
    data = normalise(raw_payload)

    assert "referral_code" not in data
    assert "referral_link" not in data
    assert "referral_friends" not in data


def test_normalise_extracts_contract_start_and_billing_preferences(raw_payload):
    data = normalise(raw_payload)

    assert data["contract_start"] == date(2025, 10, 1)
    assert data["electronic_invoice"] is True
    assert data["direct_debit"] is True


def test_paper_billing_and_no_direct_debit_read_as_off(raw_payload):
    raw_payload["account"]["electronicInvoice"] = {"active": False}
    raw_payload["account"]["directDebit"] = None
    data = normalise(raw_payload)

    assert data["electronic_invoice"] is False
    assert data["direct_debit"] is False


def test_normalise_extracts_the_supply_details(raw_payload):
    gas = normalise(raw_payload)["services"]["gas"]

    assert gas["meter_number"] == "CNTGAS0000000"
    assert gas["meter_digits"] == 5
    # Only active campaigns, by code (``name`` is empty live).
    assert gas["campaigns"] == "DIGITAL_01/26"


def test_a_service_without_active_campaigns_reports_none(raw_payload):
    raw_payload["services"]["gas"]["service"]["campaignList"] = []
    gas = normalise(raw_payload)["services"]["gas"]

    assert gas["campaigns"] is None


def test_normalise_extracts_the_latest_invoice(raw_payload):
    data = normalise(raw_payload)

    assert data["last_invoice_total"] == 12.34
    assert data["last_invoice_posted"] == date(2026, 7, 23)
    assert data["last_invoice_due"] == date(2026, 8, 17)
    assert data["last_invoice_period_start"] == date(2026, 6, 21)
    assert data["last_invoice_period_end"] == date(2026, 7, 20)
    assert data["last_invoice_number"] == "FT 2026-07-23"


def test_normalise_sums_what_is_left_to_pay_into_amount_due(raw_payload):
    data = normalise(raw_payload)

    assert data["amount_due"] == 12.34
    assert data["invoice_pending"] is True


def test_nothing_left_to_pay_means_no_pending_invoice(raw_payload):
    for inv in raw_payload["invoices"]:
        inv["remainingAmount"] = 0
    data = normalise(raw_payload)

    assert data["amount_due"] == 0.0
    assert data["invoice_pending"] is False


def test_normalise_billed_12m_excludes_older_invoices(raw_payload):
    data = normalise(raw_payload)

    # 12.34 + 25.67; the 99.99 invoice posted in 2024 falls outside the window.
    assert data["billed_12m"] == 38.01


def test_normalise_builds_an_invoice_series_keyed_by_period_end(raw_payload):
    """Cost belongs to the period it bills, not the day the invoice was posted."""
    series = normalise(raw_payload)["invoice_series"]

    assert series == [
        {"iso": "2024-06-20", "total": 99.99},
        {"iso": "2026-06-20", "total": 25.67},
        {"iso": "2026-07-20", "total": 12.34},
    ]


def test_invoices_closing_on_the_same_day_are_merged(raw_payload):
    raw_payload["invoices"].append(
        {
            "postingDate": "2026-07-25T00:00:00",
            "billingPeriodEndDate": "2026-07-20T00:00:00",
            "amount": 2.5,
            "remainingAmount": 0,
        }
    )
    series = normalise(raw_payload)["invoice_series"]

    assert [e for e in series if e["iso"] == "2026-07-20"] == [
        {"iso": "2026-07-20", "total": 14.84}
    ]


def test_a_brand_new_account_normalises_without_invoices_or_history():
    """The account captured live: one initial reading, no invoice yet."""
    raw = {
        "account": {"billingAccountNo": TEST_ACCOUNT, "balanceAmount": 0},
        "invoices": [],
        "services": {
            "gas": {
                "service": {"no": "S1", "gas": {"cui": TEST_CUI}},
                "readings": [gas_reading(1, "2026-03-02", 120)],
            }
        },
    }
    data = normalise(raw)
    gas = data["services"]["gas"]

    assert gas["meter_index"] == 120.0
    assert "last_consumption" not in gas
    assert "last_invoice_total" not in data
    assert data["amount_due"] == 0.0
    assert data["invoice_pending"] is False
    assert data["invoice_series"] == []


def test_normalise_survives_an_empty_payload():
    data = normalise({})

    assert data == {"available": True, "conversion_factor": FACTOR, "services": {}}


def test_a_service_without_readings_keeps_its_identity(raw_payload):
    raw_payload["services"]["gas"]["readings"] = []
    gas = normalise(raw_payload)["services"]["gas"]

    assert gas["service_no"] == "S0000000001"
    assert "meter_index" not in gas


def test_parse_readings_collapses_duplicate_days_keeping_the_highest():
    readings = _parse_readings(
        [gas_reading(1, "2026-07-30", 310), gas_reading(2, "2026-07-30", 320)]
    )

    assert readings == [{"iso": "2026-07-30", "index": 320.0}]


def test_parse_readings_is_sorted_oldest_first(raw_payload):
    readings = _parse_readings(raw_payload["services"]["gas"]["readings"])

    assert [r["iso"] for r in readings] == [
        "2025-10-21",
        "2026-04-13",
        "2026-07-11",
        "2026-07-30",
    ]


def test_parse_readings_drops_cancelled_and_unparseable_entries():
    items = [
        gas_reading(1, "2026-01-01", 10, cancelled=True),
        {"date": "not-a-date", "records": [{"value": 10}]},
        {"date": "2026-01-02T00:00:00", "records": []},
        {"date": "2026-01-03T00:00:00", "records": [{"value": "abc"}]},
        gas_reading(2, "2026-01-04", 12),
        "garbage",
    ]

    assert _parse_readings(items) == [{"iso": "2026-01-04", "index": 12.0}]


def test_parse_readings_rejects_a_non_list():
    assert _parse_readings(None) == []
    assert _parse_readings({"items": []}) == []


def test_parse_date_handles_the_api_timestamp_without_an_offset():
    assert _parse_date("2026-03-02T00:00:00") == date(2026, 3, 2)
    assert _parse_date("2026-03-02") == date(2026, 3, 2)
    assert _parse_date("nonsense") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_to_float_accepts_numbers_and_numeric_strings_only():
    assert _to_float(120) == 120.0
    assert _to_float(12.5) == 12.5
    assert _to_float(" 12,34 ") == 12.34
    assert _to_float("abc") is None
    assert _to_float(None) is None
    # A JSON boolean is not a number, even though Python thinks it is.
    assert _to_float(True) is None


def test_enabled_energies_follow_the_toggles():
    assert enabled_energies({}) == {"gas", "electricity"}
    assert enabled_energies({CONF_ENABLE_ELECTRICITY: False}) == {"gas"}
    assert enabled_energies({CONF_ENABLE_GAS: False}) == {"electricity"}
    assert (
        enabled_energies({CONF_ENABLE_GAS: False, CONF_ENABLE_ELECTRICITY: False})
        == set()
    )


async def test_update_maps_auth_errors_to_reauth(hass):
    """An auth failure must prompt the user, not retry forever."""
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(
        side_effect=GoldenergyAuthError("rejected")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_update_maps_transport_errors_to_update_failed(hass):
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(
        side_effect=GoldenergyConnectionError("down")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_asks_only_for_the_enabled_energies(hass, raw_payload):
    coordinator = GoldenergyCoordinator(
        hass, {**_CONFIG, CONF_ENABLE_ELECTRICITY: False}
    )
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.goldenergy.coordinator.async_import_statistics",
        AsyncMock(),
    ):
        await coordinator._async_update_data()

    coordinator.client.async_get_data.assert_awaited_once_with(TEST_ACCOUNT, {"gas"})


async def test_update_normalises_and_imports_statistics(hass, raw_payload):
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.goldenergy.coordinator.async_import_statistics",
        AsyncMock(),
    ) as importer:
        data = await coordinator._async_update_data()

    assert data["services"]["gas"]["meter_index"] == 320.0
    importer.assert_awaited_once()
    _, account, readings, factor, invoices = importer.await_args.args
    assert account == TEST_ACCOUNT
    assert set(readings) == {"gas", "electricity"}
    assert factor == DEFAULT_CONVERSION_FACTOR
    assert len(invoices) == 3


async def test_a_statistics_failure_does_not_fail_the_poll(hass, raw_payload):
    """Sensors must still update when the recorder misbehaves."""
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.goldenergy.coordinator.async_import_statistics",
        AsyncMock(side_effect=RuntimeError("recorder not ready")),
    ):
        data = await coordinator._async_update_data()

    assert data["services"]["gas"]["meter_index"] == 320.0


async def test_an_enabled_energy_missing_from_the_account_is_logged(
    hass, raw_payload, caplog
):
    del raw_payload["services"]["electricity"]
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.client.async_get_data = AsyncMock(return_value=raw_payload)

    with patch(
        "custom_components.goldenergy.coordinator.async_import_statistics",
        AsyncMock(),
    ):
        await coordinator._async_update_data()

    assert "electricity is enabled" in caplog.text


def test_schedule_registers_one_refresh_per_poll_hour(hass):
    coordinator = GoldenergyCoordinator(hass, _CONFIG)

    coordinator.async_setup_schedule()
    assert len(coordinator._unsub_schedule) == len(POLL_HOURS)

    coordinator.async_teardown_schedule()
    assert coordinator._unsub_schedule == []


@pytest.mark.parametrize("bad", [None, 0, -1, "abc", ""])
def test_a_nonsensical_configured_factor_falls_back(hass, bad):
    """A zero or negative factor would silently zero out every gas energy figure."""
    coordinator = GoldenergyCoordinator(hass, {**_CONFIG, CONF_CONVERSION_FACTOR: bad})

    assert coordinator.conversion_factor == DEFAULT_CONVERSION_FACTOR


def test_a_configured_factor_is_used(hass):
    coordinator = GoldenergyCoordinator(
        hass, {**_CONFIG, CONF_CONVERSION_FACTOR: FACTOR}
    )

    assert coordinator.conversion_factor == FACTOR


READING_DAY = date(2026, 3, 9)


def submit_coordinator(hass, raw_payload) -> GoldenergyCoordinator:
    """A coordinator holding normalised data, with the client mocked."""
    coordinator = GoldenergyCoordinator(hass, _CONFIG)
    coordinator.data = normalise(raw_payload)
    coordinator.client.async_submit_reading = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def test_submit_sends_the_reading_and_refreshes(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)

    await coordinator.async_submit_reading("gas", [325], READING_DAY)

    coordinator.client.async_submit_reading.assert_awaited_once_with(
        billing_account=TEST_ACCOUNT,
        service_no="S0000000001",
        energy="gas",
        meter_no="CNTGAS0000000",
        reading_date=READING_DAY,
        records=[{"type": 0, "value": 325}],
        confirm_above_average=False,
    )
    # The new reading is pulled in straight away.
    coordinator.async_request_refresh.assert_awaited_once()


async def test_submit_pairs_values_with_the_meter_s_registers(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)

    await coordinator.async_submit_reading(
        "electricity", [1200, 600], READING_DAY, confirm_above_average=True
    )

    kwargs = coordinator.client.async_submit_reading.await_args.kwargs
    assert kwargs["records"] == [{"type": 0, "value": 1200}, {"type": 1, "value": 600}]
    assert kwargs["meter_no"] == "CNTELE0000000"
    assert kwargs["confirm_above_average"] is True


async def test_a_reading_below_the_last_is_refused_before_sending(hass, raw_payload):
    """The 152-after-158 case: never reaches Goldenergy."""
    coordinator = submit_coordinator(hass, raw_payload)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_submit_reading("gas", [152], READING_DAY)

    assert err.value.translation_key == "below_last_reading"
    assert err.value.translation_placeholders == {"value": "152", "last": "320"}
    coordinator.client.async_submit_reading.assert_not_awaited()


async def test_an_untracked_energy_is_refused(hass, raw_payload):
    del raw_payload["services"]["electricity"]
    coordinator = submit_coordinator(hass, raw_payload)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_submit_reading("electricity", [1, 2], READING_DAY)

    assert err.value.translation_key == "energy_not_tracked"


async def test_the_wrong_number_of_registers_is_refused(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_submit_reading("electricity", [2000], READING_DAY)

    assert err.value.translation_key == "wrong_register_count"
    assert err.value.translation_placeholders == {"expected": "2", "got": "1"}


async def test_an_unknown_meter_is_refused(hass, raw_payload):
    raw_payload["services"]["gas"]["service"]["gas"]["meter"] = None
    coordinator = submit_coordinator(hass, raw_payload)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_submit_reading("gas", [325], READING_DAY)

    assert err.value.translation_key == "meter_unknown"


async def test_above_average_consumption_asks_to_confirm(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)
    coordinator.client.async_submit_reading.side_effect = GoldenergyAboveAverage(
        "1", "Consumo acima da média"
    )

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_submit_reading("gas", [900], READING_DAY)

    assert err.value.translation_key == "above_average"
    coordinator.async_request_refresh.assert_not_awaited()


async def test_a_refusal_surfaces_goldenergy_s_reason(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)
    coordinator.client.async_submit_reading.side_effect = GoldenergyReadingRejected(
        "2", "Erro - Leitura inferior à última registada!"
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_submit_reading("gas", [325], READING_DAY)

    assert err.value.translation_key == "reading_rejected"
    assert err.value.translation_placeholders == {
        "message": "Erro - Leitura inferior à última registada!"
    }


async def test_a_transport_failure_while_submitting_is_reported(hass, raw_payload):
    coordinator = submit_coordinator(hass, raw_payload)
    coordinator.client.async_submit_reading.side_effect = GoldenergyConnectionError(
        "down"
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_submit_reading("gas", [325], READING_DAY)

    assert err.value.translation_key == "submit_failed"
