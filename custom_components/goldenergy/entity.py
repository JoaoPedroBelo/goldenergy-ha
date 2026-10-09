"""Shared entity base for the Goldenergy integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_BILLING_ACCOUNT,
    DATA_SERVICES,
    DOMAIN,
    PORTAL_ORIGIN,
)
from .coordinator import GoldenergyCoordinator

MANUFACTURER = "Goldenergy"


class GoldenergyEntity(CoordinatorEntity[GoldenergyCoordinator]):
    """Identity and device info shared by every Goldenergy entity.

    One device per config entry, i.e. per billing account, named after the supply
    address so a customer with several accounts stays readable. Gas and
    electricity entities of the same account share it, as they share the invoice.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoldenergyCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise identity and device info."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        account = entry.data.get(CONF_BILLING_ACCOUNT) or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_ADDRESS) or f"{MANUFACTURER} {account}",
            manufacturer=MANUFACTURER,
            model="Billing account",
            serial_number=entry.data.get(CONF_BILLING_ACCOUNT),
            configuration_url=PORTAL_ORIGIN,
        )

    def _source(self, energy: str | None) -> dict[str, Any]:
        """Return the dict an entity reads from: one energy's, or the account's."""
        data = self.coordinator.data or {}
        if energy is None:
            return data
        service = (data.get(DATA_SERVICES) or {}).get(energy)
        return service if isinstance(service, dict) else {}
