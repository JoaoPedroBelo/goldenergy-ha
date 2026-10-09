"""Config flow for the Goldenergy integration.

Setup is a live login, a billing-account picker (a customer can hold several,
one per supply address, each configured as its own entry) and a choice of which
energies of that account to track — gas, electricity or both.

There is no captcha or two-factor step on the login this integration uses (see
``api.py``), so polling stays fully headless once the entry exists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import re
from typing import Any, TypeVar

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import GoldenergyAuthError, GoldenergyClient, GoldenergyConnectionError
from .const import (
    CONF_ADDRESS,
    CONF_BILLING_ACCOUNT,
    CONF_CONVERSION_FACTOR,
    CONF_ENABLE_ELECTRICITY,
    CONF_ENABLE_GAS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_CONVERSION_FACTOR,
    DOMAIN,
    ENERGY_ELECTRICITY,
    ENERGY_GAS,
    MAX_CONVERSION_FACTOR,
    MIN_CONVERSION_FACTOR,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

# Selectors rather than bare ``str``: a plain string renders as a *visible* text
# box, so the password would be shown in clear while being typed.
USERNAME_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): USERNAME_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

# Which toggle switches which energy on.
ENERGY_TOGGLES: dict[str, str] = {
    ENERGY_GAS: CONF_ENABLE_GAS,
    ENERGY_ELECTRICITY: CONF_ENABLE_ELECTRICITY,
}

_WHITESPACE_RE = re.compile(r"\s+")


def _tidy(raw: Any) -> str | None:
    """Collapse padding and return ``None`` for a blank value."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _WHITESPACE_RE.sub(" ", raw).strip()


def account_address(account: dict[str, Any]) -> str | None:
    """Build a one-line supply address from a billing account, if it has one.

    ``RUA EXAMPLE 1, APT 2A, 1000-000 LISBOA`` — street and door, the
    ``duplicator`` (apartment/floor), then post code and city.
    """
    places = account.get("consumptionPointAddresses")
    if not isinstance(places, list) or not places or not isinstance(places[0], dict):
        return None
    address = places[0].get("consumptionPointAddress")
    if not isinstance(address, dict):
        return None
    street = " ".join(
        part
        for part in (_tidy(address.get("address")), _tidy(address.get("doorNo")))
        if part
    )
    locality = " ".join(
        part
        for part in (_tidy(address.get("postCode")), _tidy(address.get("city")))
        if part
    )
    parts = [p for p in (street, _tidy(address.get("duplicator")), locality) if p]
    return ", ".join(parts) or None


def _account_label(account: dict[str, Any]) -> str:
    """Build a picker label that distinguishes accounts at a glance."""
    number = str(account.get("billingAccountNo") or "")
    address = account_address(account)
    return f"{address} ({number})" if address else number


def _energies_schema(defaults: dict[str, bool]) -> dict[Any, Any]:
    """Return the two energy toggles, pre-set to ``defaults``."""
    return {
        vol.Required(toggle, default=defaults.get(toggle, True)): bool
        for toggle in ENERGY_TOGGLES.values()
    }


def _validate_energies(user_input: dict[str, Any], available: set[str]) -> str | None:
    """Return the error key for an invalid energy choice, or ``None`` if it is fine."""
    chosen = {
        energy for energy, toggle in ENERGY_TOGGLES.items() if user_input.get(toggle)
    }
    if not chosen:
        return "no_energy_selected"
    if chosen - available:
        return "energy_not_on_account"
    return None


async def _async_call(
    username: str,
    password: str,
    errors: dict[str, str],
    call: Callable[[GoldenergyClient], Awaitable[_T]],
) -> _T | None:
    """Run ``call`` against a throwaway client, mapping failures to form errors."""
    client = GoldenergyClient(username=username, password=password)
    try:
        return await call(client)
    except GoldenergyAuthError:
        errors["base"] = "invalid_auth"
    except GoldenergyConnectionError:
        errors["base"] = "cannot_connect"
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception talking to Goldenergy")
        errors["base"] = "unknown"
    finally:
        await client.close()
    return None


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Goldenergy."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Expose the energy toggles and the gas conversion factor for editing."""
        return OptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialise transient flow state."""
        self._creds: dict[str, str] = {}
        self._accounts: list[dict[str, Any]] = []
        self._account: dict[str, Any] = {}
        self._available: set[str] = set()
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate the credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._creds = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            accounts = await self._async_call(
                errors, lambda client: client.async_list_accounts()
            )
            if accounts is not None:
                if not accounts:
                    return self.async_abort(reason="no_accounts")
                self._accounts = accounts
                return await self.async_step_account()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick which billing account this entry tracks.

        A single-account customer skips the form: there is nothing to choose.
        """
        if user_input is None and len(self._accounts) == 1:
            user_input = {CONF_BILLING_ACCOUNT: self._accounts[0]["billingAccountNo"]}

        if user_input is not None:
            account = next(
                (
                    a
                    for a in self._accounts
                    if a.get("billingAccountNo") == user_input[CONF_BILLING_ACCOUNT]
                ),
                None,
            )
            if account is None:
                return self.async_abort(reason="unknown_account")
            await self.async_set_unique_id(str(account["billingAccountNo"]))
            self._abort_if_unique_id_configured()
            self._account = account
            return await self.async_step_energies()

        options = {
            str(a["billingAccountNo"]): _account_label(a) for a in self._accounts
        }
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BILLING_ACCOUNT, default=next(iter(options), None)
                    ): vol.In(options)
                }
            ),
        )

    async def async_step_energies(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose which of the account's energies to track."""
        errors: dict[str, str] = {}
        number = str(self._account["billingAccountNo"])

        if user_input is None:
            available = await self._async_call(
                errors, lambda client: client.async_discover_energies(number)
            )
            if available is None:
                return self.async_abort(reason=errors["base"])
            if not available:
                return self.async_abort(reason="no_energy_supplies")
            self._available = available
        else:
            error = _validate_energies(user_input, self._available)
            if error:
                errors["base"] = error
            else:
                return self._async_create_entry(user_input)

        defaults = {
            toggle: energy in self._available
            for energy, toggle in ENERGY_TOGGLES.items()
        }
        return self.async_show_form(
            step_id="energies",
            data_schema=vol.Schema(_energies_schema(defaults)),
            errors=errors,
            description_placeholders={
                "account": number,
                "supplies": ", ".join(sorted(self._available)),
            },
        )

    def _async_create_entry(self, toggles: dict[str, Any]) -> FlowResult:
        """Create the entry for the chosen account and energies."""
        number = str(self._account["billingAccountNo"])
        address = account_address(self._account)
        data = {**self._creds, CONF_BILLING_ACCOUNT: number}
        if address:
            data[CONF_ADDRESS] = address
        # The toggles live in options so the options flow can change them later.
        options = {
            toggle: bool(toggles.get(toggle)) for toggle in ENERGY_TOGGLES.values()
        }
        return self.async_create_entry(
            title=address or f"Goldenergy ({number})", data=data, options=options
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start re-authentication after the stored password stopped working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the current password and validate it with a live login."""
        assert self._reauth_entry is not None
        username = self._reauth_entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}

        if user_input is not None:
            self._creds = {
                CONF_USERNAME: username,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            await self._async_call(errors, lambda client: client.async_login())
            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, **self._creds},
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={"username": username},
        )

    async def _async_call(
        self,
        errors: dict[str, str],
        call: Callable[[GoldenergyClient], Awaitable[_T]],
    ) -> _T | None:
        """Run ``call`` with the credentials collected so far."""
        return await _async_call(
            self._creds[CONF_USERNAME], self._creds[CONF_PASSWORD], errors, call
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Edit the tracked energies and the gas conversion factor after setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Keep the entry under a private name.

        Assigning ``self.config_entry`` is deprecated in newer Home Assistant
        cores (it became a managed property), so this stays compatible with both.
        """
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show and store the options, checking the energies against the account."""
        errors: dict[str, str] = {}
        current = {**self._entry.data, **self._entry.options}

        if user_input is not None:
            available = await _async_call(
                current[CONF_USERNAME],
                current[CONF_PASSWORD],
                errors,
                lambda client: client.async_discover_energies(
                    current[CONF_BILLING_ACCOUNT]
                ),
            )
            if available is not None:
                error = _validate_energies(user_input, available)
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(title="", data=user_input)

        defaults = {
            toggle: bool(current.get(toggle, True))
            for toggle in ENERGY_TOGGLES.values()
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    **_energies_schema(defaults),
                    vol.Required(
                        CONF_CONVERSION_FACTOR,
                        default=float(
                            current.get(
                                CONF_CONVERSION_FACTOR, DEFAULT_CONVERSION_FACTOR
                            )
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=MIN_CONVERSION_FACTOR, max=MAX_CONVERSION_FACTOR),
                    ),
                }
            ),
            errors=errors,
        )
