"""End-to-end setup of the integration inside a real Home Assistant instance.

Everything else in this suite tests our own logic in isolation. This module boots
Home Assistant, adds a config entry, and asserts the entities and the long-term
statistics actually materialise — the class of failure (illegal device_class,
recorder wiring, entity naming) that only shows up once HA is in the loop.

The API is mocked at the client boundary; nothing here touches the network.

Every test that reads statistics back must call ``async_wait_recording_done``
first: ``async_add_external_statistics`` *queues* the write on the recorder
thread, and ``hass.async_block_till_done()`` does not flush that queue.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.goldenergy.const import (
    CONF_ADDRESS,
    CONF_BILLING_ACCOUNT,
    CONF_CONVERSION_FACTOR,
    CONF_ENABLE_ELECTRICITY,
    CONF_ENABLE_GAS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    POLL_HOURS,
)
from custom_components.goldenergy.statistics import (
    cost_statistic_id,
    electricity_statistic_id,
    gas_energy_statistic_id,
    gas_volume_statistic_id,
)

from .conftest import TEST_ACCOUNT, TEST_ADDRESS, TEST_USERNAME

FACTOR = 11.20808
ENTRY_DATA = {
    CONF_USERNAME: TEST_USERNAME,
    CONF_PASSWORD: "secret",
    CONF_BILLING_ACCOUNT: TEST_ACCOUNT,
    CONF_ADDRESS: TEST_ADDRESS,
}
ENTRY_OPTIONS = {
    CONF_ENABLE_GAS: True,
    CONF_ENABLE_ELECTRICITY: True,
    CONF_CONVERSION_FACTOR: FACTOR,
}
# The device is named after the address, and entity ids derive from it.
PREFIX = "rua_example_1_apt_2a_1000_000_lisboa"

# The gas fixture runs 21-10-2025 -> 30-07-2026, electricity 30-06 -> 30-07-2026,
# and every day in between carries a point.
GAS_DAYS = (date(2026, 7, 30) - date(2025, 10, 21)).days + 1
ELECTRICITY_DAYS = (date(2026, 7, 30) - date(2026, 6, 30)).days + 1


@pytest.fixture(autouse=True)
def _ha_environment(recorder_mock, enable_custom_integrations):
    """Prepare the HA environment for every test in this module.

    Order matters: ``recorder_mock`` must be built before anything pulls in the
    ``hass`` fixture (``recorder_db_url`` asserts hass has not started yet), and
    ``enable_custom_integrations`` does pull it in.
    """


@pytest.fixture
def mock_client(raw_payload):
    """Patch the client the coordinator instantiates."""
    client = AsyncMock()
    client.async_get_data = AsyncMock(return_value=raw_payload)
    client.close = AsyncMock()
    with patch(
        "custom_components.goldenergy.coordinator.GoldenergyClient",
        return_value=client,
    ):
        yield client


async def setup_entry(
    hass: HomeAssistant, options: dict | None = None
) -> MockConfigEntry:
    """Add and set up a config entry, returning it once loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=ENTRY_OPTIONS if options is None else options,
        unique_id=TEST_ACCOUNT,
        title=TEST_ADDRESS,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def count_by_platform(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, int]:
    """Count the entry's registered entities per platform."""
    counts: dict[str, int] = {}
    for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        counts[e.domain] = counts.get(e.domain, 0) + 1
    return counts


async def read_series(hass: HomeAssistant, *ids: str) -> dict[str, list[dict]]:
    """Read whole daily series back from the recorder."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    await async_wait_recording_done(hass)
    return await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        set(ids),
        "day",
        None,
        {"state", "sum"},
    )


async def test_entry_loads(hass, mock_client):
    entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data


async def test_both_energies_create_every_entity(hass, mock_client):
    entry = await setup_entry(hass)

    # 9 gas + 5 electricity + 9 account sensors; 4 binary sensors.
    assert count_by_platform(hass, entry) == {"sensor": 23, "binary_sensor": 4}


async def test_a_disabled_energy_creates_no_entities(hass, mock_client):
    entry = await setup_entry(
        hass, {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: False}
    )

    assert count_by_platform(hass, entry) == {"sensor": 18, "binary_sensor": 4}
    assert hass.states.get(f"sensor.{PREFIX}_electricity_meter_index") is None
    mock_client.async_get_data.assert_awaited_with(TEST_ACCOUNT, {"gas"})


async def test_disabling_an_energy_in_the_options_removes_its_entities(
    hass, mock_client
):
    """No orphaned, permanently unavailable entities may be left behind."""
    entry = await setup_entry(hass)
    assert count_by_platform(hass, entry)["sensor"] == 23

    hass.config_entries.async_update_entry(
        entry, options={**ENTRY_OPTIONS, CONF_ENABLE_GAS: False}
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert not any("_gas_" in u for u in unique_ids)
    # 5 electricity + 9 account sensors remain.
    assert count_by_platform(hass, entry)["sensor"] == 14


async def test_gas_states_carry_the_normalised_values(hass, mock_client):
    await setup_entry(hass)

    index = hass.states.get(f"sensor.{PREFIX}_gas_meter_index")
    assert index is not None, sorted(hass.states.async_entity_ids("sensor"))
    assert index.state == "320.0"
    assert index.attributes["unit_of_measurement"] == "m³"
    assert index.attributes["device_class"] == "gas"
    assert index.attributes["state_class"] == "total"

    energy = hass.states.get(f"sensor.{PREFIX}_gas_meter_index_energy")
    assert float(energy.state) == round(320.0 * FACTOR, 2)
    assert energy.attributes["unit_of_measurement"] == "kWh"

    delta = hass.states.get(f"sensor.{PREFIX}_gas_consumption_since_previous_reading")
    assert delta.state == "20.0"
    # No device_class: HA forbids gas + measurement.
    assert "device_class" not in delta.attributes


async def test_electricity_states_carry_the_normalised_values(hass, mock_client):
    await setup_entry(hass)

    index = hass.states.get(f"sensor.{PREFIX}_electricity_meter_index")
    assert index is not None, sorted(hass.states.async_entity_ids("sensor"))
    assert index.state == "1660.0"
    assert index.attributes["device_class"] == "energy"
    assert index.attributes["unit_of_measurement"] == "kWh"


async def test_account_states(hass, mock_client):
    await setup_entry(hass)

    due = hass.states.get(f"sensor.{PREFIX}_last_invoice_due_date")
    assert due.state == "2026-08-17"

    amount = hass.states.get(f"sensor.{PREFIX}_amount_due")
    assert amount.state == "12.34"
    assert amount.attributes["device_class"] == "monetary"

    nxt = hass.states.get(f"sensor.{PREFIX}_next_reading_date")
    assert nxt.state == "2026-08-20"

    pending = hass.states.get(f"binary_sensor.{PREFIX}_invoice_pending_payment")
    assert pending.state == "on"

    referral = hass.states.get(f"sensor.{PREFIX}_referral_code")
    assert referral.state == "MGM0000000"
    assert referral.attributes["referral_link"] == (
        "https://amigo.goldenergy.pt/MGM0000000"
    )
    assert hass.states.get(f"sensor.{PREFIX}_friends_referred").state == "2"
    assert hass.states.get(f"binary_sensor.{PREFIX}_direct_debit").state == "on"
    assert hass.states.get(f"binary_sensor.{PREFIX}_electronic_invoice").state == "on"


async def test_supply_details_are_diagnostic_sensors(hass, mock_client):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)

    by_key = {
        e.unique_id.removeprefix(f"{entry.entry_id}_"): e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    for key in ("gas_delivery_point", "gas_tier", "gas_meter_serial", "gas_campaign"):
        assert by_key[key].entity_category is er.EntityCategory.DIAGNOSTIC, key

    campaign = hass.states.get(f"sensor.{PREFIX}_gas_campaign")
    assert campaign.state == "DIGITAL_01/26"


async def test_availability_sensor_is_not_registered_by_default(hass, mock_client):
    entry = await setup_entry(hass)
    registry = er.async_get(hass)

    available = next(
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id.endswith("_available")
    )

    assert available.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_one_device_named_after_the_supply_address(hass, mock_client):
    entry = await setup_entry(hass)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    assert len(devices) == 1
    assert devices[0].name == TEST_ADDRESS
    assert devices[0].serial_number == TEST_ACCOUNT
    assert devices[0].manufacturer == "Goldenergy"


async def test_gas_volume_statistic_is_imported(hass, mock_client):
    await setup_entry(hass)

    volume_id = gas_volume_statistic_id(TEST_ACCOUNT)
    series = (await read_series(hass, volume_id)).get(volume_id)

    assert series, f"no statistics under {volume_id}"
    # One point per day, so a month's gas is never credited to a single day.
    assert len(series) == GAS_DAYS
    # 0, +100, +100, +20 over the window (the cancelled 999 is ignored).
    assert series[-1]["sum"] == pytest.approx(220.0)
    assert series[-1]["state"] == pytest.approx(320.0)


async def test_gas_energy_statistic_is_derived_from_the_volume(hass, mock_client):
    await setup_entry(hass)

    volume_id = gas_volume_statistic_id(TEST_ACCOUNT)
    energy_id = gas_energy_statistic_id(TEST_ACCOUNT)
    stats = await read_series(hass, volume_id, energy_id)

    volume, energy = stats[volume_id], stats[energy_id]
    assert len(energy) == len(volume) == GAS_DAYS
    for v, e in zip(volume, energy, strict=True):
        assert e["sum"] == pytest.approx(v["sum"] * FACTOR, abs=1e-3)


async def test_correcting_the_factor_rewrites_the_whole_gas_energy_history(
    hass, mock_client
):
    entry = await setup_entry(hass)
    volume_id = gas_volume_statistic_id(TEST_ACCOUNT)
    energy_id = gas_energy_statistic_id(TEST_ACCOUNT)
    before = await read_series(hass, volume_id, energy_id)

    hass.config_entries.async_update_entry(
        entry, options={**ENTRY_OPTIONS, CONF_CONVERSION_FACTOR: 12.5}
    )
    await hass.async_block_till_done()
    after = await read_series(hass, volume_id, energy_id)

    for v, e in zip(after[volume_id], after[energy_id], strict=True):
        assert e["sum"] == pytest.approx(v["sum"] * 12.5, abs=1e-3)
    # The volume series is untouched by the factor change.
    assert [v["sum"] for v in after[volume_id]] == [v["sum"] for v in before[volume_id]]


async def test_electricity_statistic_is_imported(hass, mock_client):
    await setup_entry(hass)

    stat_id = electricity_statistic_id(TEST_ACCOUNT)
    series = (await read_series(hass, stat_id)).get(stat_id)

    assert series, f"no statistics under {stat_id}"
    assert len(series) == ELECTRICITY_DAYS
    assert series[-1]["sum"] == pytest.approx(160.0)
    assert series[-1]["state"] == pytest.approx(1660.0)


async def test_cost_statistic_is_imported_from_the_invoices(hass, mock_client):
    await setup_entry(hass)

    cost_id = cost_statistic_id(TEST_ACCOUNT)
    series = (await read_series(hass, cost_id)).get(cost_id)

    assert series, f"no cost statistics under {cost_id}"
    assert len(series) == 3
    assert series[-1]["sum"] == pytest.approx(99.99 + 25.67 + 12.34)


async def test_cost_statistic_is_registered_in_the_local_currency(hass, mock_client):
    """A cost statistic in the wrong currency is rejected as a cost source."""
    from homeassistant.components.recorder.statistics import list_statistic_ids

    await setup_entry(hass)
    await async_wait_recording_done(hass)

    ids = await hass.async_add_executor_job(list_statistic_ids, hass)
    cost = next(x for x in ids if x["statistic_id"] == cost_statistic_id(TEST_ACCOUNT))

    assert cost["statistics_unit_of_measurement"] == hass.config.currency
    assert cost["has_sum"] is True


async def test_a_second_poll_does_not_duplicate_statistics(hass, mock_client):
    await setup_entry(hass)
    await async_wait_recording_done(hass)

    coordinator = next(iter(hass.data[DOMAIN].values()))
    await coordinator.async_refresh()

    gas_id = gas_volume_statistic_id(TEST_ACCOUNT)
    cost_id = cost_statistic_id(TEST_ACCOUNT)
    stats = await read_series(hass, gas_id, cost_id)

    assert len(stats[gas_id]) == GAS_DAYS
    assert stats[gas_id][-1]["sum"] == pytest.approx(220.0)
    assert len(stats[cost_id]) == 3
    assert stats[cost_id][-1]["sum"] == pytest.approx(138.00)


async def test_entry_unloads_and_closes_the_client(hass, mock_client):
    entry = await setup_entry(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_the_poll_schedule_fires_at_the_configured_hours(hass, mock_client):
    """The integration must refresh on its own without a periodic interval."""
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await setup_entry(hass)
    calls_after_setup = mock_client.async_get_data.await_count

    now = dt_util.now()
    target = now.replace(hour=POLL_HOURS[0], minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    async_fire_time_changed(hass, target)
    await hass.async_block_till_done()

    assert mock_client.async_get_data.await_count > calls_after_setup


async def test_a_failing_first_poll_leaves_the_entry_retrying(hass):
    """An API outage at setup must be retryable, not a hard failure."""
    from custom_components.goldenergy.api import GoldenergyConnectionError

    client = AsyncMock()
    client.async_get_data = AsyncMock(side_effect=GoldenergyConnectionError("down"))
    client.close = AsyncMock()

    with patch(
        "custom_components.goldenergy.coordinator.GoldenergyClient",
        return_value=client,
    ):
        entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_bad_credentials_at_setup_trigger_reauth(hass):
    from custom_components.goldenergy.api import GoldenergyAuthError

    client = AsyncMock()
    client.async_get_data = AsyncMock(side_effect=GoldenergyAuthError("rejected"))
    client.close = AsyncMock()

    with patch(
        "custom_components.goldenergy.coordinator.GoldenergyClient",
        return_value=client,
    ):
        entry = await setup_entry(hass)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def call_submit(hass: HomeAssistant, **data) -> None:
    """Call goldenergy.submit_reading and wait for it."""
    await hass.services.async_call(DOMAIN, "submit_reading", data, blocking=True)


async def test_the_submit_action_is_registered(hass, mock_client):
    await setup_entry(hass)

    assert hass.services.has_service(DOMAIN, "submit_reading")


async def test_submitting_a_reading_reaches_goldenergy(hass, mock_client):
    entry = await setup_entry(hass)
    mock_client.async_submit_reading = AsyncMock()

    await call_submit(hass, energy="gas", value=325, config_entry_id=entry.entry_id)

    kwargs = mock_client.async_submit_reading.await_args.kwargs
    assert kwargs["records"] == [{"type": 0, "value": 325}]
    assert kwargs["reading_date"] == dt_util.now().date()
    assert kwargs["confirm_above_average"] is False


async def test_the_entry_may_be_omitted_when_there_is_only_one(hass, mock_client):
    await setup_entry(hass)
    mock_client.async_submit_reading = AsyncMock()

    await call_submit(hass, energy="gas", value=325, day="yesterday")

    kwargs = mock_client.async_submit_reading.await_args.kwargs
    assert kwargs["reading_date"] == dt_util.now().date() - timedelta(days=1)


async def test_a_reading_below_the_last_never_leaves_home_assistant(hass, mock_client):
    from homeassistant.exceptions import ServiceValidationError

    await setup_entry(hass)
    mock_client.async_submit_reading = AsyncMock()

    with pytest.raises(ServiceValidationError) as err:
        await call_submit(hass, energy="gas", value=152)

    assert err.value.translation_key == "below_last_reading"
    mock_client.async_submit_reading.assert_not_awaited()


async def test_a_reading_needs_a_value(hass, mock_client):
    from homeassistant.exceptions import ServiceValidationError

    await setup_entry(hass)

    with pytest.raises(ServiceValidationError) as err:
        await call_submit(hass, energy="gas")

    assert err.value.translation_key == "value_required"


async def test_value_and_values_are_mutually_exclusive(hass, mock_client):
    import voluptuous as vol

    await setup_entry(hass)

    with pytest.raises(vol.Invalid):
        await call_submit(hass, energy="electricity", value=1, values=[1, 2])


async def test_an_unknown_entry_is_refused(hass, mock_client):
    from homeassistant.exceptions import ServiceValidationError

    await setup_entry(hass)

    with pytest.raises(ServiceValidationError) as err:
        await call_submit(hass, energy="gas", value=325, config_entry_id="nope")

    assert err.value.translation_key == "entry_not_loaded"
