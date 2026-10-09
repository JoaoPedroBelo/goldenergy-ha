"""Sensor platform for the Goldenergy integration.

Gas volumes are in cubic metres (the unit the gas meter index is read in),
electricity and billed gas energy in kWh, money in EUR.

The Energy dashboard is fed by the long-term statistics imported by the
coordinator (see ``statistics.py``), not by a live ``total_increasing`` sensor:
readings are backdated and sparse, so a live sensor would attribute a whole
month's consumption to the poll hour. These sensors are informative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    EntityCategory,
    UnitOfEnergy,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_CONVERSION_FACTOR,
    ATTR_DAYS,
    ATTR_DELIVERY_POINT,
    ATTR_INVOICE_NUMBER,
    ATTR_METER_DIGITS,
    ATTR_METER_NUMBER,
    ATTR_METER_SERIAL,
    ATTR_PERIOD_END,
    ATTR_PERIOD_START,
    ATTR_POSTING_DATE,
    ATTR_READING_DATE,
    ATTR_REFERRAL_EARNINGS,
    ATTR_REFERRAL_LINK,
    ATTR_SERVICE_NO,
    ATTR_SMART_METER,
    ATTR_TIER,
    DATA_AMOUNT_DUE,
    DATA_BALANCE,
    DATA_BILLED_12M,
    DATA_CAMPAIGNS,
    DATA_CONTRACT_START,
    DATA_CONVERSION_FACTOR,
    DATA_DELIVERY_POINT,
    DATA_LAST_CONSUMPTION,
    DATA_LAST_CONSUMPTION_DAYS,
    DATA_LAST_CONSUMPTION_ENERGY,
    DATA_LAST_CONSUMPTION_FROM,
    DATA_LAST_INVOICE_DUE,
    DATA_LAST_INVOICE_NUMBER,
    DATA_LAST_INVOICE_PERIOD_END,
    DATA_LAST_INVOICE_PERIOD_START,
    DATA_LAST_INVOICE_POSTED,
    DATA_LAST_INVOICE_TOTAL,
    DATA_LAST_READING_ISO,
    DATA_METER_DIGITS,
    DATA_METER_INDEX,
    DATA_METER_INDEX_ENERGY,
    DATA_METER_NUMBER,
    DATA_METER_SERIAL,
    DATA_NEXT_READING_DATE,
    DATA_REFERRAL_CODE,
    DATA_REFERRAL_EARNINGS,
    DATA_REFERRAL_FRIENDS,
    DATA_REFERRAL_LINK,
    DATA_SERVICE_NO,
    DATA_SMART_METER,
    DATA_TIER,
    DOMAIN,
    ENERGY_ELECTRICITY,
    ENERGY_GAS,
    SENSOR_AMOUNT_DUE,
    SENSOR_BALANCE,
    SENSOR_BILLED_12M,
    SENSOR_CAMPAIGN,
    SENSOR_CONTRACT_START,
    SENSOR_DELIVERY_POINT,
    SENSOR_LAST_CONSUMPTION,
    SENSOR_LAST_CONSUMPTION_ENERGY,
    SENSOR_LAST_INVOICE_DUE,
    SENSOR_LAST_INVOICE_TOTAL,
    SENSOR_LAST_READING_DATE,
    SENSOR_METER_INDEX,
    SENSOR_METER_INDEX_ENERGY,
    SENSOR_METER_SERIAL,
    SENSOR_NEXT_READING_DATE,
    SENSOR_REFERRAL_CODE,
    SENSOR_REFERRAL_FRIENDS,
    SENSOR_TIER,
)
from .coordinator import GoldenergyCoordinator
from .entity import GoldenergyEntity


@dataclass(frozen=True, kw_only=True)
class GoldenergySensorDescription(SensorEntityDescription):
    """Describes a Goldenergy sensor backed by a single normalised value."""

    data_key: str
    # ``None`` for an account-level sensor; otherwise the energy whose service
    # dict (``coordinator.data["services"][energy]``) the value is read from.
    energy: str | None = None
    # ``attribute name -> data key``, read from the same dict as the value.
    attribute_keys: tuple[tuple[str, str], ...] = ()


_PERIOD_ATTRIBUTES = (
    (ATTR_PERIOD_START, DATA_LAST_CONSUMPTION_FROM),
    (ATTR_PERIOD_END, DATA_LAST_READING_ISO),
    (ATTR_DAYS, DATA_LAST_CONSUMPTION_DAYS),
)
_METER_ATTRIBUTES = (
    (ATTR_READING_DATE, DATA_LAST_READING_ISO),
    (ATTR_SERVICE_NO, DATA_SERVICE_NO),
    (ATTR_DELIVERY_POINT, DATA_DELIVERY_POINT),
    (ATTR_METER_SERIAL, DATA_METER_SERIAL),
    (ATTR_TIER, DATA_TIER),
)


def _key(energy: str, suffix: str) -> str:
    """Return a per-energy entity key, e.g. ``gas_meter_index``."""
    return f"{energy}_{suffix}"


def _supply_sensors(energy: str) -> tuple[GoldenergySensorDescription, ...]:
    """Diagnostic sensors describing one energy's supply and meter."""
    return (
        GoldenergySensorDescription(
            key=_key(energy, SENSOR_METER_SERIAL),
            translation_key=_key(energy, SENSOR_METER_SERIAL),
            energy=energy,
            data_key=DATA_METER_SERIAL,
            icon="mdi:counter",
            entity_category=EntityCategory.DIAGNOSTIC,
            attribute_keys=(
                (ATTR_METER_NUMBER, DATA_METER_NUMBER),
                (ATTR_METER_DIGITS, DATA_METER_DIGITS),
                (ATTR_SMART_METER, DATA_SMART_METER),
            ),
        ),
        GoldenergySensorDescription(
            key=_key(energy, SENSOR_CAMPAIGN),
            translation_key=_key(energy, SENSOR_CAMPAIGN),
            energy=energy,
            data_key=DATA_CAMPAIGNS,
            icon="mdi:tag-outline",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    )


GAS_SENSORS: tuple[GoldenergySensorDescription, ...] = (
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_METER_INDEX),
        translation_key=_key(ENERGY_GAS, SENSOR_METER_INDEX),
        energy=ENERGY_GAS,
        data_key=DATA_METER_INDEX,
        icon="mdi:gauge",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        # An absolute, never-resetting counter, but not ``total_increasing``: the
        # Energy dashboard is driven by the imported statistic instead.
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        attribute_keys=_METER_ATTRIBUTES,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_METER_INDEX_ENERGY),
        translation_key=_key(ENERGY_GAS, SENSOR_METER_INDEX_ENERGY),
        energy=ENERGY_GAS,
        data_key=DATA_METER_INDEX_ENERGY,
        icon="mdi:meter-gas",
        # ``energy`` rather than ``gas``: HA only allows m³/ft³/CCF for gas.
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        attribute_keys=(
            (ATTR_READING_DATE, DATA_LAST_READING_ISO),
            (ATTR_CONVERSION_FACTOR, DATA_CONVERSION_FACTOR),
        ),
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_LAST_CONSUMPTION),
        translation_key=_key(ENERGY_GAS, SENSOR_LAST_CONSUMPTION),
        energy=ENERGY_GAS,
        data_key=DATA_LAST_CONSUMPTION,
        icon="mdi:fire",
        # Deliberately no device_class: this is a delta between two past
        # readings, not a meter. HA only accepts ``total``/``total_increasing``
        # for GAS, and ``total`` would falsely claim the value accumulates.
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attribute_keys=_PERIOD_ATTRIBUTES,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_LAST_CONSUMPTION_ENERGY),
        translation_key=_key(ENERGY_GAS, SENSOR_LAST_CONSUMPTION_ENERGY),
        energy=ENERGY_GAS,
        data_key=DATA_LAST_CONSUMPTION_ENERGY,
        icon="mdi:fire",
        # A period delta, so no device class - see the volume sensor above.
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attribute_keys=(
            *_PERIOD_ATTRIBUTES,
            (ATTR_CONVERSION_FACTOR, DATA_CONVERSION_FACTOR),
        ),
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_LAST_READING_DATE),
        translation_key=_key(ENERGY_GAS, SENSOR_LAST_READING_DATE),
        energy=ENERGY_GAS,
        data_key=DATA_LAST_READING_ISO,
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.DATE,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_DELIVERY_POINT),
        translation_key=_key(ENERGY_GAS, SENSOR_DELIVERY_POINT),
        energy=ENERGY_GAS,
        data_key=DATA_DELIVERY_POINT,
        icon="mdi:map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_GAS, SENSOR_TIER),
        translation_key=_key(ENERGY_GAS, SENSOR_TIER),
        energy=ENERGY_GAS,
        data_key=DATA_TIER,
        icon="mdi:stairs",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    *_supply_sensors(ENERGY_GAS),
)

ELECTRICITY_SENSORS: tuple[GoldenergySensorDescription, ...] = (
    GoldenergySensorDescription(
        key=_key(ENERGY_ELECTRICITY, SENSOR_METER_INDEX),
        translation_key=_key(ENERGY_ELECTRICITY, SENSOR_METER_INDEX),
        energy=ENERGY_ELECTRICITY,
        data_key=DATA_METER_INDEX,
        icon="mdi:meter-electric",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        attribute_keys=_METER_ATTRIBUTES,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_ELECTRICITY, SENSOR_LAST_CONSUMPTION),
        translation_key=_key(ENERGY_ELECTRICITY, SENSOR_LAST_CONSUMPTION),
        energy=ENERGY_ELECTRICITY,
        data_key=DATA_LAST_CONSUMPTION,
        icon="mdi:lightning-bolt",
        # A period delta: ENERGY only takes ``total``/``total_increasing``.
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attribute_keys=_PERIOD_ATTRIBUTES,
    ),
    GoldenergySensorDescription(
        key=_key(ENERGY_ELECTRICITY, SENSOR_LAST_READING_DATE),
        translation_key=_key(ENERGY_ELECTRICITY, SENSOR_LAST_READING_DATE),
        energy=ENERGY_ELECTRICITY,
        data_key=DATA_LAST_READING_ISO,
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.DATE,
    ),
    *_supply_sensors(ENERGY_ELECTRICITY),
)

ACCOUNT_SENSORS: tuple[GoldenergySensorDescription, ...] = (
    GoldenergySensorDescription(
        key=SENSOR_NEXT_READING_DATE,
        translation_key=SENSOR_NEXT_READING_DATE,
        data_key=DATA_NEXT_READING_DATE,
        icon="mdi:calendar-start",
        device_class=SensorDeviceClass.DATE,
    ),
    GoldenergySensorDescription(
        key=SENSOR_BALANCE,
        translation_key=SENSOR_BALANCE,
        data_key=DATA_BALANCE,
        icon="mdi:scale-balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    GoldenergySensorDescription(
        key=SENSOR_LAST_INVOICE_TOTAL,
        translation_key=SENSOR_LAST_INVOICE_TOTAL,
        data_key=DATA_LAST_INVOICE_TOTAL,
        icon="mdi:receipt-text",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        attribute_keys=(
            (ATTR_INVOICE_NUMBER, DATA_LAST_INVOICE_NUMBER),
            (ATTR_POSTING_DATE, DATA_LAST_INVOICE_POSTED),
            (ATTR_PERIOD_START, DATA_LAST_INVOICE_PERIOD_START),
            (ATTR_PERIOD_END, DATA_LAST_INVOICE_PERIOD_END),
        ),
    ),
    GoldenergySensorDescription(
        key=SENSOR_LAST_INVOICE_DUE,
        translation_key=SENSOR_LAST_INVOICE_DUE,
        data_key=DATA_LAST_INVOICE_DUE,
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
    ),
    GoldenergySensorDescription(
        key=SENSOR_AMOUNT_DUE,
        translation_key=SENSOR_AMOUNT_DUE,
        data_key=DATA_AMOUNT_DUE,
        icon="mdi:cash-clock",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    GoldenergySensorDescription(
        key=SENSOR_BILLED_12M,
        translation_key=SENSOR_BILLED_12M,
        data_key=DATA_BILLED_12M,
        icon="mdi:chart-timeline-variant",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    GoldenergySensorDescription(
        key=SENSOR_CONTRACT_START,
        translation_key=SENSOR_CONTRACT_START,
        data_key=DATA_CONTRACT_START,
        icon="mdi:calendar-start-outline",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    GoldenergySensorDescription(
        key=SENSOR_REFERRAL_CODE,
        translation_key=SENSOR_REFERRAL_CODE,
        data_key=DATA_REFERRAL_CODE,
        icon="mdi:account-multiple-plus",
        attribute_keys=(
            (ATTR_REFERRAL_LINK, DATA_REFERRAL_LINK),
            (ATTR_REFERRAL_EARNINGS, DATA_REFERRAL_EARNINGS),
        ),
    ),
    GoldenergySensorDescription(
        key=SENSOR_REFERRAL_FRIENDS,
        translation_key=SENSOR_REFERRAL_FRIENDS,
        data_key=DATA_REFERRAL_FRIENDS,
        icon="mdi:account-heart",
    ),
)

SENSORS: tuple[GoldenergySensorDescription, ...] = (
    *GAS_SENSORS,
    *ELECTRICITY_SENSORS,
    *ACCOUNT_SENSORS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors for the account and each enabled energy."""
    coordinator: GoldenergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        GoldenergySensor(coordinator, entry, description)
        for description in SENSORS
        if description.energy is None or description.energy in coordinator.energies
    )


class GoldenergySensor(GoldenergyEntity, SensorEntity):
    """A sensor reading one normalised value out of the coordinator data."""

    entity_description: GoldenergySensorDescription

    def __init__(
        self,
        coordinator: GoldenergyCoordinator,
        entry: ConfigEntry,
        description: GoldenergySensorDescription,
    ) -> None:
        """Initialise the sensor from its description."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | date | None:
        """Return the value behind this sensor's data key."""
        description = self.entity_description
        value = self._source(description.energy).get(description.data_key)
        if description.device_class is SensorDeviceClass.DATE and isinstance(
            value, str
        ):
            # Reading dates are normalised to ISO strings; a DATE sensor needs a
            # real ``date`` object.
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the description's extra attributes, omitting missing ones."""
        description = self.entity_description
        if not description.attribute_keys:
            return None
        source = self._source(description.energy)
        attributes = {name: source.get(key) for name, key in description.attribute_keys}
        return {k: v for k, v in attributes.items() if v is not None} or None
