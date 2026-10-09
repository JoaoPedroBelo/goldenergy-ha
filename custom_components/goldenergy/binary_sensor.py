"""Binary sensor platform for the Goldenergy integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BINARY_SENSOR_AVAILABLE,
    BINARY_SENSOR_INVOICE_PENDING,
    DATA_AVAILABLE,
    DATA_INVOICE_PENDING,
    DOMAIN,
)
from .coordinator import GoldenergyCoordinator
from .entity import GoldenergyEntity


@dataclass(frozen=True, kw_only=True)
class GoldenergyBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Goldenergy binary sensor backed by an account-level flag."""

    data_key: str
    # When true the entity also requires the last poll to have succeeded, which
    # is what makes the availability sensor differ from the rest.
    require_poll_success: bool = False


BINARY_SENSORS: tuple[GoldenergyBinarySensorDescription, ...] = (
    GoldenergyBinarySensorDescription(
        key=BINARY_SENSOR_INVOICE_PENDING,
        translation_key=BINARY_SENSOR_INVOICE_PENDING,
        data_key=DATA_INVOICE_PENDING,
        icon="mdi:cash-clock",
    ),
    GoldenergyBinarySensorDescription(
        key=BINARY_SENSOR_AVAILABLE,
        translation_key=BINARY_SENSOR_AVAILABLE,
        data_key=DATA_AVAILABLE,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        require_poll_success=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Goldenergy binary sensors."""
    coordinator: GoldenergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        GoldenergyBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class GoldenergyBinarySensor(GoldenergyEntity, BinarySensorEntity):
    """A binary sensor reading one flag out of the coordinator data."""

    entity_description: GoldenergyBinarySensorDescription

    def __init__(
        self,
        coordinator: GoldenergyCoordinator,
        entry: ConfigEntry,
        description: GoldenergyBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor from its description."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the flag behind this sensor's data key."""
        value = bool(self._source(None).get(self.entity_description.data_key))
        if self.entity_description.require_poll_success:
            return value and bool(self.coordinator.last_update_success)
        return value
