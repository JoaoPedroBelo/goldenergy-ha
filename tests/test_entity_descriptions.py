"""Validate the entity descriptions against Home Assistant's own rules.

These are the checks that fail at *runtime* in a real Home Assistant rather than
in a unit test of our own logic — an illegal device_class/state_class pair is only
rejected once the entity is added, so pinning it here keeps the integration
installable.
"""

from homeassistant.components.sensor import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    SensorDeviceClass,
    SensorStateClass,
)
import pytest

from custom_components.goldenergy.binary_sensor import BINARY_SENSORS
from custom_components.goldenergy.const import ENERGIES
from custom_components.goldenergy.sensor import SENSORS


@pytest.mark.parametrize("description", SENSORS, ids=lambda d: d.key)
def test_state_class_is_legal_for_the_device_class(description):
    """HA rejects e.g. device_class=gas with state_class=measurement."""
    if description.device_class is None:
        return
    allowed = DEVICE_CLASS_STATE_CLASSES.get(description.device_class)
    if allowed is None:
        return
    if not allowed:
        assert description.state_class is None, (
            f"{description.key}: {description.device_class.value} takes no state_class"
        )
        return
    assert description.state_class in allowed, (
        f"{description.key}: state_class {description.state_class} is not one of "
        f"{sorted(s.value for s in allowed)} for {description.device_class.value}"
    )


@pytest.mark.parametrize("description", SENSORS, ids=lambda d: d.key)
def test_unit_is_legal_for_the_device_class(description):
    if description.device_class is None:
        return
    allowed = DEVICE_CLASS_UNITS.get(description.device_class)
    if allowed is None:
        return
    assert description.native_unit_of_measurement in allowed, (
        f"{description.key}: unit {description.native_unit_of_measurement!r} is not "
        f"one of {sorted(str(u) for u in allowed)}"
    )


@pytest.mark.parametrize("description", SENSORS, ids=lambda d: d.key)
def test_a_measured_value_declares_a_unit(description):
    """A numeric state class without a unit renders as a bare number."""
    if description.state_class is None:
        return
    assert description.native_unit_of_measurement is not None


def test_the_gas_meter_index_is_the_only_gas_sensor():
    gas = [d for d in SENSORS if d.device_class is SensorDeviceClass.GAS]

    assert [d.key for d in gas] == ["gas_meter_index"]
    assert gas[0].state_class is SensorStateClass.TOTAL


@pytest.mark.parametrize(
    "key", ["gas_last_consumption", "electricity_last_consumption"]
)
def test_period_consumption_has_no_device_class(key):
    """A delta between two past readings is not a meter; see sensor.py."""
    delta = next(d for d in SENSORS if d.key == key)

    assert delta.device_class is None
    assert delta.state_class is SensorStateClass.MEASUREMENT


@pytest.mark.parametrize("description", SENSORS, ids=lambda d: d.key)
def test_per_energy_keys_are_prefixed_with_their_energy(description):
    """Entity cleanup on disabling an energy relies on this prefix."""
    if description.energy is None:
        assert not description.key.startswith(tuple(f"{e}_" for e in ENERGIES))
    else:
        assert description.key.startswith(f"{description.energy}_")


@pytest.mark.parametrize(
    "description", [*SENSORS, *BINARY_SENSORS], ids=lambda d: d.key
)
def test_every_entity_declares_a_translation_key(description):
    """Without one, has_entity_name yields an unnamed entity."""
    assert description.translation_key == description.key


def test_entity_keys_are_unique_within_each_platform():
    for platform in (SENSORS, BINARY_SENSORS):
        keys = [d.key for d in platform]
        assert len(keys) == len(set(keys))
