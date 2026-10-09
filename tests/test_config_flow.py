"""Tests for the Goldenergy config and options flows."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.goldenergy.api import (
    GoldenergyAuthError,
    GoldenergyConnectionError,
)
from custom_components.goldenergy.config_flow import (
    ConfigFlow,
    OptionsFlowHandler,
    account_address,
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
)

from .conftest import ADDRESS_PAYLOAD, TEST_ACCOUNT, TEST_ADDRESS, TEST_USERNAME

CREDENTIALS = {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "secret"}
BOTH = {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: True}

ACCOUNT_A = {
    "billingAccountNo": TEST_ACCOUNT,
    "consumptionPointAddresses": [{"consumptionPointAddress": ADDRESS_PAYLOAD}],
}
ACCOUNT_B = {
    "billingAccountNo": "CG1111111",
    "consumptionPointAddresses": [
        {"consumptionPointAddress": {"address": "RUA OTHER", "city": "PORTO"}}
    ],
}


@pytest.fixture
def flow(hass):
    """A config flow wired to the test hass instance.

    ``context`` is normally a read-only mapping supplied by the flow manager;
    a hand-built handler needs a mutable one so ``async_set_unique_id`` works.
    """
    handler = ConfigFlow()
    handler.hass = hass
    handler.handler = DOMAIN
    handler.context = {}
    return handler


def patch_client(
    accounts=None, energies=None, *, side_effect=None, energies_side_effect=None
):
    """Patch the client the flow instantiates."""
    client = AsyncMock()
    client.async_list_accounts = AsyncMock(
        return_value=accounts, side_effect=side_effect
    )
    client.async_discover_energies = AsyncMock(
        return_value=energies if energies is not None else {"gas", "electricity"},
        side_effect=energies_side_effect,
    )
    client.async_login = AsyncMock(side_effect=side_effect)
    client.close = AsyncMock()
    return patch(
        "custom_components.goldenergy.config_flow.GoldenergyClient",
        return_value=client,
    )


async def test_form_is_shown_first(flow):
    result = await flow.async_step_user()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_a_single_account_skips_the_picker_and_asks_what_to_track(flow):
    with patch_client([ACCOUNT_A], {"gas"}):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "energies"
    assert result["description_placeholders"] == {
        "account": TEST_ACCOUNT,
        "supplies": "gas",
    }


async def test_the_toggles_default_to_the_energies_the_account_has(flow):
    with patch_client([ACCOUNT_A], {"gas"}):
        result = await flow.async_step_user(CREDENTIALS)

    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if key.default
    }
    assert defaults == {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: False}


async def test_choosing_energies_creates_the_entry(flow):
    with patch_client([ACCOUNT_A], {"gas", "electricity"}):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_energies(
            {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: False}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_ADDRESS
    assert result["data"] == {
        **CREDENTIALS,
        CONF_BILLING_ACCOUNT: TEST_ACCOUNT,
        CONF_ADDRESS: TEST_ADDRESS,
    }
    # Toggles live in options so the options flow can change them.
    assert result["options"] == {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: False}


async def test_enabling_nothing_is_rejected(flow):
    with patch_client([ACCOUNT_A], {"gas"}):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_energies(
            {CONF_ENABLE_GAS: False, CONF_ENABLE_ELECTRICITY: False}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_energy_selected"}


async def test_enabling_an_energy_the_account_lacks_is_rejected(flow):
    with patch_client([ACCOUNT_A], {"gas"}):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_energies(BOTH)

    assert result["errors"] == {"base": "energy_not_on_account"}


async def test_an_account_without_energy_supplies_aborts(flow):
    with patch_client([ACCOUNT_A], set()):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_energy_supplies"


async def test_a_failure_while_discovering_energies_aborts(flow):
    with patch_client(
        [ACCOUNT_A], energies_side_effect=GoldenergyConnectionError("down")
    ):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_several_accounts_show_the_picker(flow):
    with patch_client([ACCOUNT_A, ACCOUNT_B]):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "account"


async def test_picking_an_account_moves_on_to_the_energies(flow):
    with patch_client([ACCOUNT_A, ACCOUNT_B], {"electricity"}):
        await flow.async_step_user(CREDENTIALS)
        await flow.async_step_account({CONF_BILLING_ACCOUNT: "CG1111111"})
        result = await flow.async_step_energies(
            {CONF_ENABLE_GAS: False, CONF_ENABLE_ELECTRICITY: True}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BILLING_ACCOUNT] == "CG1111111"
    assert result["title"] == "RUA OTHER, PORTO"


async def test_picking_an_unknown_account_aborts(flow):
    with patch_client([ACCOUNT_A, ACCOUNT_B]):
        await flow.async_step_user(CREDENTIALS)
        result = await flow.async_step_account({CONF_BILLING_ACCOUNT: "gone"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown_account"


async def test_a_customer_without_accounts_aborts(flow):
    with patch_client([]):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_accounts"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (GoldenergyAuthError("rejected"), "invalid_auth"),
        (GoldenergyConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_login_failures_show_an_error(flow, error, key):
    with patch_client(side_effect=error):
        result = await flow.async_step_user(CREDENTIALS)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def test_the_username_is_trimmed(flow):
    with patch_client([ACCOUNT_A], {"gas"}):
        await flow.async_step_user({**CREDENTIALS, CONF_USERNAME: "  C0000000 "})
        result = await flow.async_step_energies(
            {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: False}
        )

    assert result["data"][CONF_USERNAME] == "C0000000"


def test_account_address_joins_street_flat_and_locality():
    assert account_address(ACCOUNT_A) == TEST_ADDRESS
    assert account_address({"consumptionPointAddresses": []}) is None
    assert account_address({}) is None
    padded = {
        "consumptionPointAddresses": [
            {"consumptionPointAddress": {"address": "RUA   X ", "city": " LISBOA"}}
        ]
    }
    assert account_address(padded) == "RUA X, LISBOA"


def _reauth_flow(hass, entry):
    """A flow already primed for re-authentication of ``entry``."""
    handler = ConfigFlow()
    handler.hass = hass
    handler.handler = DOMAIN
    handler.context = {"entry_id": entry.entry_id}
    return handler


async def test_reauth_asks_for_the_password(hass, mock_config_entry):
    with patch.object(
        hass.config_entries, "async_get_entry", return_value=mock_config_entry
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        result = await flow.async_step_reauth(mock_config_entry.data)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # The username is shown so the user knows which login is being fixed.
    assert result["description_placeholders"] == {"username": TEST_USERNAME}


async def test_reauth_stores_the_new_password_and_reloads(hass, mock_config_entry):
    with (
        patch.object(
            hass.config_entries, "async_get_entry", return_value=mock_config_entry
        ),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload,
        patch_client(),
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        await flow.async_step_reauth(mock_config_entry.data)
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new-secret"})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert update.call_args.kwargs["data"][CONF_PASSWORD] == "new-secret"
    # The account selection must survive a reauth untouched.
    assert update.call_args.kwargs["data"][CONF_BILLING_ACCOUNT] == TEST_ACCOUNT
    reload.assert_awaited_once()


async def test_reauth_reprompts_when_the_new_password_is_also_wrong(
    hass, mock_config_entry
):
    with (
        patch.object(
            hass.config_entries, "async_get_entry", return_value=mock_config_entry
        ),
        patch.object(hass.config_entries, "async_update_entry") as update,
        patch_client(side_effect=GoldenergyAuthError("rejected")),
    ):
        flow = _reauth_flow(hass, mock_config_entry)
        await flow.async_step_reauth(mock_config_entry.data)
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "still-wrong"})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    update.assert_not_called()


def _options_flow(hass, entry) -> OptionsFlowHandler:
    handler = OptionsFlowHandler(entry)
    handler.hass = hass
    handler.handler = entry.entry_id
    handler.flow_id = "test"
    return handler


def _entry(options: dict) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {**CREDENTIALS, CONF_BILLING_ACCOUNT: TEST_ACCOUNT}
    entry.options = options
    return entry


async def test_options_show_the_current_choices(hass):
    flow = _options_flow(hass, _entry({CONF_ENABLE_ELECTRICITY: False}))

    result = await flow.async_step_init()

    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_ENABLE_GAS] is True
    assert defaults[CONF_ENABLE_ELECTRICITY] is False
    assert defaults[CONF_CONVERSION_FACTOR] == 11.2


async def test_options_store_valid_choices(hass):
    flow = _options_flow(hass, _entry({}))
    choices = {**BOTH, CONF_CONVERSION_FACTOR: 11.5}

    with patch_client(energies={"gas", "electricity"}):
        result = await flow.async_step_init(choices)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == choices


async def test_options_reject_an_energy_the_account_lacks(hass):
    flow = _options_flow(hass, _entry({}))

    with patch_client(energies={"gas"}):
        result = await flow.async_step_init({**BOTH, CONF_CONVERSION_FACTOR: 11.2})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "energy_not_on_account"}


async def test_options_report_an_unreachable_api(hass):
    flow = _options_flow(hass, _entry({}))

    with patch_client(energies_side_effect=GoldenergyConnectionError("down")):
        result = await flow.async_step_init({**BOTH, CONF_CONVERSION_FACTOR: 11.2})

    assert result["errors"] == {"base": "cannot_connect"}
