"""Tests for the Goldenergy API client.

The login is the fragile part of this integration — it relies on the
captcha-free ``/token`` endpoint — so these tests pin down its contract and the
failure modes: a rejected login, an outage, a stale token.
"""

import base64
import json
import re
import time

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.goldenergy.api import (
    GoldenergyAuthError,
    GoldenergyClient,
    GoldenergyConnectionError,
    GoldenergyError,
    _token_expiry,
    service_energy,
)
from custom_components.goldenergy.const import API_BASE_URL, EP_LOGIN, MAX_PAGES

from .conftest import (
    TEST_ACCOUNT,
    account_payload,
    electricity_service_payload,
    gas_reading,
    gas_service_payload,
)

LOGIN_URL = f"{API_BASE_URL}{EP_LOGIN}"


def endpoint(path: str) -> re.Pattern:
    """Match an endpoint with any query string."""
    return re.compile(re.escape(f"{API_BASE_URL}{path}") + r"(\?.*)?$")


ACCOUNTS_URL = endpoint("/api/billingAccounts/groupedservices-list")
ACCOUNT_URL = endpoint("/api/billingAccounts/single")
SERVICE_URL = endpoint("/api/services/single")
READINGS_URL = endpoint("/api/readings/pagelist")
INVOICES_URL = endpoint("/api/account-documents/calc-docs")


def make_token(exp: float | None = None) -> str:
    """Build an unsigned JWT-shaped token carrying ``exp``."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claims = {"role": "customer"}
    if exp is not None:
        claims["exp"] = exp
    return f"{part({'alg': 'HS256'})}.{part(claims)}.signature"


def envelope(result, *, has_error=False, error_code=None) -> dict:
    """Wrap a result the way every API response is wrapped."""
    return {
        "result": result,
        "generatedAt": "2026-03-04T12:00:00+00:00",
        "hasError": has_error,
        "message": "",
        "errorCode": error_code,
    }


def page(items: list, total: int | None = None, index: int = 0) -> dict:
    """A paginated list result."""
    return envelope(
        {
            "pageIndex": index,
            "pageSize": 10,
            "items": items,
            "totalItems": len(items) if total is None else total,
        }
    )


def mock_login(mocked: aioresponses, token: str | None = None) -> None:
    """Register one successful login."""
    mocked.post(
        LOGIN_URL,
        payload=envelope(
            {
                "token": token or make_token(time.time() + 120),
                "refreshToken": "refresh",
                "profile": {"customerNo": "C0000000"},
            }
        ),
    )


def calls_to(mocked: aioresponses, fragment: str) -> list:
    """Every request whose URL contains ``fragment``."""
    return [
        call
        for key, calls in mocked.requests.items()
        for call in calls
        if fragment in str(key[1])
    ]


async def test_login_posts_the_credentials_as_json():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        await client.async_login()

        body = calls_to(mocked, EP_LOGIN)[0].kwargs["json"]

    # The captcha-free endpoint takes no recaptcha fields at all.
    assert body == {"code": "C0000000", "password": "secret"}
    await client.close()


async def test_a_rejected_login_is_an_auth_error():
    """Wrong password and unknown customer both answer a bare 401."""
    client = GoldenergyClient("C0000000", "wrong")
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=401, payload={"title": "Unauthorized"})

        with pytest.raises(GoldenergyAuthError, match="401"):
            await client.async_login()
    await client.close()


async def test_a_login_without_a_token_is_an_auth_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload=envelope(None, has_error=True, error_code="CustomerNotRegistered"),
        )

        with pytest.raises(GoldenergyAuthError, match="CustomerNotRegistered"):
            await client.async_login()
    await client.close()


async def test_an_outage_at_login_is_a_connection_error_not_an_auth_failure():
    """A 5xx must not make Home Assistant ask for a password that was never wrong.

    ConfigEntryAuthFailed prompts for reauth with no retry, while
    ConfigEntryNotReady retries with backoff.
    """
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, status=503, body="<html>maintenance</html>")

        with pytest.raises(GoldenergyConnectionError, match="503"):
            await client.async_login()
    await client.close()


async def test_a_network_failure_at_login_is_a_connection_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, exception=aiohttp.ClientError("network down"))

        with pytest.raises(GoldenergyConnectionError):
            await client.async_login()
    await client.close()


async def test_a_timeout_at_login_is_a_connection_error():
    """A timeout is not an ``aiohttp.ClientError``, so it needs its own handling."""
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mocked.post(LOGIN_URL, exception=TimeoutError)

        with pytest.raises(GoldenergyConnectionError):
            await client.async_login()
    await client.close()


async def test_a_non_json_login_answer_is_a_connection_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL, body="<html>proxy error</html>", content_type="text/html"
        )

        with pytest.raises(GoldenergyConnectionError):
            await client.async_login()
    await client.close()


async def test_data_calls_send_the_bearer_token():
    token = make_token(time.time() + 120)
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked, token)
        mocked.get(ACCOUNTS_URL, payload=envelope({"billingAccounts": []}))
        await client.async_list_accounts()

        headers = calls_to(mocked, "groupedservices-list")[0].kwargs["headers"]

    assert headers["Authorization"] == f"Bearer {token}"
    await client.close()


async def test_list_accounts_returns_the_billing_accounts():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(
            ACCOUNTS_URL,
            payload=envelope(
                {"billingAccounts": [{"billingAccountNo": TEST_ACCOUNT}, "junk", {}]}
            ),
        )

        accounts = await client.async_list_accounts()

    assert accounts == [{"billingAccountNo": TEST_ACCOUNT}]
    await client.close()


async def test_list_accounts_tolerates_a_payload_without_accounts():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, payload=envelope({}))

        assert await client.async_list_accounts() == []
    await client.close()


async def test_a_valid_token_is_reused_instead_of_logging_in_again():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, payload=envelope({"billingAccounts": []}), repeat=True)

        await client.async_list_accounts()
        await client.async_list_accounts()

        logins = calls_to(mocked, EP_LOGIN)

    assert len(logins) == 1
    await client.close()


async def test_an_expired_token_triggers_a_new_login():
    """The access token lives 120 s; an old one must not be sent."""
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked, make_token(time.time() - 1))
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, payload=envelope({"billingAccounts": []}), repeat=True)

        await client.async_list_accounts()
        await client.async_list_accounts()

        logins = calls_to(mocked, EP_LOGIN)

    assert len(logins) == 2
    await client.close()


async def test_a_rejected_token_triggers_exactly_one_re_login():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, status=401)
        mock_login(mocked)
        mocked.get(
            ACCOUNTS_URL,
            payload=envelope({"billingAccounts": [{"billingAccountNo": "CG1"}]}),
        )

        accounts = await client.async_list_accounts()
        logins = calls_to(mocked, EP_LOGIN)

    assert len(accounts) == 1
    assert len(logins) == 2
    await client.close()


async def test_a_persistently_rejected_token_gives_up():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, status=401)
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, status=401)

        with pytest.raises(GoldenergyAuthError):
            await client.async_list_accounts()
    await client.close()


async def test_an_error_inside_the_envelope_is_raised():
    """Application errors answer 200 with ``hasError`` set."""
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(
            ACCOUNTS_URL, payload=envelope(None, has_error=True, error_code="Boom")
        )

        with pytest.raises(GoldenergyError, match="Boom"):
            await client.async_list_accounts()
    await client.close()


async def test_a_5xx_on_a_data_call_is_a_connection_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, status=502)

        with pytest.raises(GoldenergyConnectionError, match="502"):
            await client.async_list_accounts()
    await client.close()


async def test_a_4xx_on_a_data_call_is_a_plain_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, status=400, body="bad request")

        with pytest.raises(GoldenergyError, match="400") as err:
            await client.async_list_accounts()

    assert not isinstance(err.value, (GoldenergyAuthError, GoldenergyConnectionError))
    await client.close()


async def test_a_timeout_mid_poll_is_a_connection_error():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(ACCOUNTS_URL, exception=TimeoutError)

        with pytest.raises(GoldenergyConnectionError):
            await client.async_list_accounts()
    await client.close()


def mock_account_and_services(mocked: aioresponses) -> None:
    """Register the account and its two services."""
    mocked.get(ACCOUNT_URL, payload=envelope(account_payload()), repeat=True)
    mocked.get(
        re.compile(re.escape(f"{API_BASE_URL}/api/services/single") + r".*S0000000001"),
        payload=envelope(gas_service_payload()),
        repeat=True,
    )
    mocked.get(
        re.compile(re.escape(f"{API_BASE_URL}/api/services/single") + r".*S0000000002"),
        payload=envelope(electricity_service_payload()),
        repeat=True,
    )


async def test_discover_energies_classifies_each_service():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mock_account_and_services(mocked)

        energies = await client.async_discover_energies(TEST_ACCOUNT)

    assert energies == {"gas", "electricity"}
    await client.close()


async def test_get_data_fetches_readings_only_for_enabled_energies():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mock_account_and_services(mocked)
        mocked.get(INVOICES_URL, payload=page([]))
        mocked.get(READINGS_URL, payload=page([gas_reading(1, "2026-03-02", 120)]))

        raw = await client.async_get_data(TEST_ACCOUNT, {"gas"})
        reading_calls = calls_to(mocked, "readings/pagelist")

    assert set(raw["services"]) == {"gas"}
    assert raw["services"]["gas"]["readings"][0]["records"][0]["value"] == 120
    assert raw["account"]["billingAccountNo"] == TEST_ACCOUNT
    assert raw["invoices"] == []
    # Gas is EnergyType 0 in the API's enumeration.
    assert len(reading_calls) == 1
    assert reading_calls[0].kwargs["params"]["EnergyType"] == "0"
    assert reading_calls[0].kwargs["params"]["ServiceNo"] == "S0000000001"
    await client.close()


async def test_pagination_walks_until_total_items():
    client = GoldenergyClient("C0000000", "secret")
    first = [gas_reading(i, "2026-01-01", i) for i in range(10)]
    second = [gas_reading(i, "2026-01-01", i) for i in range(10, 13)]
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(READINGS_URL, payload=page(first, total=13, index=0))
        mocked.get(READINGS_URL, payload=page(second, total=13, index=1))

        items = await client._get_all_pages("/api/readings/pagelist", {})
        indexes = [
            c.kwargs["params"]["PageIndex"] for c in calls_to(mocked, "pagelist")
        ]

    assert len(items) == 13
    assert indexes == ["0", "1"]
    await client.close()


async def test_pagination_is_bounded_when_total_items_lies():
    client = GoldenergyClient("C0000000", "secret")
    full_page = [gas_reading(i, "2026-01-01", i) for i in range(10)]
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(READINGS_URL, payload=page(full_page, total=10_000), repeat=True)

        items = await client._get_all_pages("/api/readings/pagelist", {})

    assert len(items) == 10 * MAX_PAGES
    await client.close()


async def test_pagination_stops_on_an_empty_page():
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(READINGS_URL, payload=page([], total=5))

        assert await client._get_all_pages("/api/readings/pagelist", {}) == []
    await client.close()


async def test_an_active_service_wins_over_an_ended_one_of_the_same_energy():
    ended = {**gas_service_payload(), "no": "OLD", "endDate": "2025-01-01T00:00:00"}
    active = gas_service_payload()
    client = GoldenergyClient("C0000000", "secret")
    with aioresponses() as mocked:
        mock_login(mocked)
        mocked.get(
            re.compile(re.escape(f"{API_BASE_URL}/api/services/single") + r".*OLD"),
            payload=envelope(ended),
        )
        mocked.get(
            re.compile(
                re.escape(f"{API_BASE_URL}/api/services/single") + r".*S0000000001"
            ),
            payload=envelope(active),
        )

        services = await client.async_get_services(TEST_ACCOUNT, ["OLD", "S0000000001"])

    assert services["gas"]["no"] == "S0000000001"
    await client.close()


def test_service_energy_reads_the_populated_supply():
    assert service_energy(gas_service_payload()) == "gas"
    assert service_energy(electricity_service_payload()) == "electricity"
    # A value-added service (insurance, etc.) supplies no energy.
    assert service_energy({"no": "S1", "gas": None, "electricity": None}) is None
    assert service_energy(None) is None


def test_token_expiry_reads_the_exp_claim():
    assert _token_expiry(make_token(1791555607)) == 1791555607
    assert _token_expiry(make_token()) is None
    assert _token_expiry("not-a-jwt") is None
    assert _token_expiry("a.!!!.c") is None
