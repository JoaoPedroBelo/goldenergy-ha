"""The Goldenergy integration.

Author: João Belo
Independent open-source integration for the Goldenergy customer area. Not
affiliated with Goldenergy.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, ENERGIES
from .coordinator import GoldenergyCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

# Configured through the UI only; there is no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions once for the domain.

    Done here rather than per entry so the action exists (and validates) even
    while no entry is loaded, as Home Assistant's service guidelines require.
    """
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Goldenergy from a config entry."""
    _LOGGER.debug("Setting up Goldenergy integration")

    coordinator = GoldenergyCoordinator(hass, {**entry.data, **entry.options})

    # Fail fast (ConfigEntryNotReady triggers HA retry) if the first poll fails.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    _async_remove_disabled_energy_entities(hass, entry, coordinator.energies)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Poll twice a day instead of on a periodic interval.
    coordinator.async_setup_schedule()
    entry.async_on_unload(coordinator.async_teardown_schedule)

    # The enabled energies and the conversion factor are read at setup, so apply
    # an options edit by reloading the entry.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _LOGGER.info("Goldenergy setup complete for %s", coordinator.billing_account)
    return True


@callback
def _async_remove_disabled_energy_entities(
    hass: HomeAssistant, entry: ConfigEntry, energies: set[str]
) -> None:
    """Drop the registry entries of an energy the options just switched off.

    The platforms only create entities for enabled energies, so without this a
    disabled energy would leave its old entities behind as permanently
    unavailable orphans.
    """
    registry = er.async_get(hass)
    stale_prefixes = tuple(
        f"{entry.entry_id}_{energy}_" for energy in ENERGIES if energy not in energies
    )
    if not stale_prefixes:
        return
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.startswith(stale_prefixes):
            registry.async_remove(entity.entity_id)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Goldenergy integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: GoldenergyCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()

    return unload_ok
