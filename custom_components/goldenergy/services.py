"""Service actions for the Goldenergy integration.

``goldenergy.submit_reading`` communicates a meter reading — the integration's
only write. It is registered once for the domain (not per entry) and resolves
the target entry from ``config_entry_id``, which may be omitted when exactly one
Goldenergy entry is loaded.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN, ENERGIES
from .coordinator import GoldenergyCoordinator

SERVICE_SUBMIT_READING = "submit_reading"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENERGY = "energy"
ATTR_VALUE = "value"
ATTR_VALUES = "values"
ATTR_DAY = "day"
ATTR_CONFIRM_ABOVE_AVERAGE = "confirm_above_average"

# The web form only offers these two days.
DAY_TODAY = "today"
DAY_YESTERDAY = "yesterday"

_READING_VALUE = vol.All(vol.Coerce(int), vol.Range(min=0))

SUBMIT_READING_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_ENERGY): vol.In(ENERGIES),
        # ``value`` for a single-register meter (gas); ``values`` for one value
        # per register of a multi-rate electricity meter. Exactly one is required.
        vol.Exclusive(ATTR_VALUE, "reading"): _READING_VALUE,
        vol.Exclusive(ATTR_VALUES, "reading"): vol.All(
            cv.ensure_list, [_READING_VALUE], vol.Length(min=1)
        ),
        vol.Optional(ATTR_DAY, default=DAY_TODAY): vol.In((DAY_TODAY, DAY_YESTERDAY)),
        vol.Optional(ATTR_CONFIRM_ABOVE_AVERAGE, default=False): cv.boolean,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain's service actions."""

    async def _submit_reading(call: ServiceCall) -> None:
        values = _reading_values(call.data)
        coordinator = _coordinator_for(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        today = dt_util.now().date()
        reading_date = (
            today if call.data[ATTR_DAY] == DAY_TODAY else today - timedelta(days=1)
        )
        await coordinator.async_submit_reading(
            call.data[ATTR_ENERGY],
            values,
            reading_date,
            confirm_above_average=call.data[ATTR_CONFIRM_ABOVE_AVERAGE],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_READING,
        _submit_reading,
        schema=SUBMIT_READING_SCHEMA,
    )


def _reading_values(data: dict[str, Any]) -> list[int]:
    """Return the reading as a list of register values."""
    if ATTR_VALUE in data:
        return [data[ATTR_VALUE]]
    if ATTR_VALUES in data:
        return list(data[ATTR_VALUES])
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="value_required"
    )


def _coordinator_for(
    hass: HomeAssistant, entry_id: str | None
) -> GoldenergyCoordinator:
    """Return the coordinator of the targeted (or only) loaded entry."""
    coordinators: dict[str, GoldenergyCoordinator] = hass.data.get(DOMAIN, {})
    if entry_id:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entry_id": entry_id},
            )
        return coordinator
    if len(coordinators) == 1:
        return next(iter(coordinators.values()))
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="entry_required" if coordinators else "no_entry_loaded",
    )
