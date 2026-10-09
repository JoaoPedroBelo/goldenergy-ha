"""Tests for the Goldenergy entities."""

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.goldenergy.binary_sensor import (
    BINARY_SENSORS,
    GoldenergyBinarySensor,
)
from custom_components.goldenergy.sensor import SENSORS, GoldenergySensor

from .conftest import TEST_ACCOUNT, TEST_ADDRESS


def _sensor(coordinator, entry, key):
    """Build the sensor with the given description key."""
    description = next(d for d in SENSORS if d.key == key)
    return GoldenergySensor(coordinator, entry, description)


def _binary_sensor(coordinator, entry, key):
    """Build the binary sensor with the given description key."""
    description = next(d for d in BINARY_SENSORS if d.key == key)
    return GoldenergyBinarySensor(coordinator, entry, description)


def test_every_sensor_has_a_unique_id(mock_coordinator, mock_config_entry):
    ids = [
        GoldenergySensor(mock_coordinator, mock_config_entry, d).unique_id
        for d in SENSORS
    ]

    assert len(ids) == len(set(ids))
    assert all(i.startswith("test_entry_id_") for i in ids)


def test_gas_meter_index_reports_cubic_metres(mock_coordinator, mock_config_entry):
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_meter_index")

    assert sensor.native_value == 320.0
    assert sensor.device_class is SensorDeviceClass.GAS
    assert sensor.native_unit_of_measurement == "m³"


def test_gas_meter_index_exposes_the_supply_details(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_meter_index")

    attributes = sensor.extra_state_attributes
    assert attributes["reading_date"] == "2026-07-30"
    assert attributes["delivery_point"].startswith("PT")
    assert attributes["tier"] == "1"
    assert attributes["service_no"] == "S0000000001"


def test_electricity_meter_index_reports_kwh(mock_coordinator, mock_config_entry):
    sensor = _sensor(mock_coordinator, mock_config_entry, "electricity_meter_index")

    assert sensor.native_value == 1660.0
    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.native_unit_of_measurement == "kWh"
    # Absent supply details are omitted rather than reported as None.
    assert "delivery_point" not in sensor.extra_state_attributes


def test_gas_energy_sensor_carries_the_conversion_factor(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_meter_index_energy")

    assert sensor.native_value == 3584.0
    assert sensor.extra_state_attributes["conversion_factor"] == 11.2


def test_last_consumption_exposes_the_period_it_covers(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_last_consumption")

    assert sensor.native_value == 20.0
    assert sensor.extra_state_attributes == {
        "period_start": "2026-07-11",
        "period_end": "2026-07-30",
        "days": 19,
    }


def test_date_sensors_convert_iso_strings_to_dates(mock_coordinator, mock_config_entry):
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_last_reading_date")

    assert sensor.native_value == date(2026, 7, 30)


def test_date_sensors_return_none_for_an_unparseable_value(
    mock_coordinator, mock_config_entry
):
    mock_coordinator.data["services"]["gas"]["last_reading_iso"] = "not-a-date"
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_last_reading_date")

    assert sensor.native_value is None


def test_account_sensors_read_the_account_level_data(
    mock_coordinator, mock_config_entry
):
    balance = _sensor(mock_coordinator, mock_config_entry, "balance")
    due = _sensor(mock_coordinator, mock_config_entry, "amount_due")

    assert balance.native_value == 12.34
    assert balance.native_unit_of_measurement == "€"
    assert balance.extra_state_attributes == {"direct_debit": True}
    assert due.native_value == 12.34


def test_sensors_return_none_when_the_energy_is_missing(
    mock_coordinator, mock_config_entry
):
    mock_coordinator.data["services"] = {}
    sensor = _sensor(mock_coordinator, mock_config_entry, "gas_meter_index")

    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_sensors_return_none_before_the_first_poll(mock_coordinator, mock_config_entry):
    mock_coordinator.data = None
    sensor = _sensor(mock_coordinator, mock_config_entry, "balance")

    assert sensor.native_value is None


def test_attributes_are_none_when_the_description_declares_none(
    mock_coordinator, mock_config_entry
):
    sensor = _sensor(mock_coordinator, mock_config_entry, "amount_due")

    assert sensor.extra_state_attributes is None


def test_device_info_is_named_after_the_supply_address(
    mock_coordinator, mock_config_entry
):
    device = _sensor(mock_coordinator, mock_config_entry, "balance").device_info

    assert device["name"] == TEST_ADDRESS
    assert device["manufacturer"] == "Goldenergy"
    assert device["serial_number"] == TEST_ACCOUNT


def test_gas_and_electricity_share_one_device(mock_coordinator, mock_config_entry):
    identifiers = {
        tuple(
            GoldenergySensor(mock_coordinator, mock_config_entry, d).device_info[
                "identifiers"
            ]
        )
        for d in SENSORS
    }

    assert len(identifiers) == 1


def test_invoice_pending_binary_sensor_follows_the_flag(
    mock_coordinator, mock_config_entry
):
    sensor = _binary_sensor(mock_coordinator, mock_config_entry, "invoice_pending")

    assert sensor.is_on is True

    mock_coordinator.data["invoice_pending"] = False
    assert sensor.is_on is False


def test_availability_sensor_also_requires_the_poll_to_have_succeeded(
    mock_coordinator, mock_config_entry
):
    sensor = _binary_sensor(mock_coordinator, mock_config_entry, "available")

    assert sensor.is_on is True

    mock_coordinator.last_update_success = False
    assert sensor.is_on is False


def test_availability_sensor_is_disabled_by_default():
    description = next(d for d in BINARY_SENSORS if d.key == "available")

    assert description.entity_registry_enabled_default is False
