"""Fixtures for Goldenergy tests.

The payloads mirror the shapes captured live (see ``docs/API.md``) with every
identifier replaced by a placeholder — no real customer number, NIF, IBAN, CUI or
address. Two shapes were *not* observable on the captured account and are built
from the front end's field names instead, so treat them as assumptions:

- the invoice list (``calc-docs`` items) — the account had no invoice yet;
- the electricity service and its multi-register readings — the account is
  gas-only.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.goldenergy.const import (
    CONF_ADDRESS,
    CONF_BILLING_ACCOUNT,
    CONF_ENABLE_ELECTRICITY,
    CONF_ENABLE_GAS,
    CONF_PASSWORD,
    CONF_USERNAME,
)


@pytest.fixture(autouse=True, scope="session")
def _start_dns_resolver_thread() -> None:
    """Let pycares start its permanent daemon thread before tests are watched.

    aiohttp resolves DNS through aiodns/pycares, and pycares destroys resolver
    channels on a single module-level daemon thread that it starts lazily and never
    joins. ``pytest-homeassistant-custom-component``'s cleanup check diffs the
    thread list around every test, so whichever test first builds an
    ``aiohttp.ClientSession`` gets blamed for that thread. Building and closing one
    session here, before any test is observed, keeps the diff honest.
    """
    loop = asyncio.new_event_loop()
    try:
        session = loop.run_until_complete(_open_session())
        loop.run_until_complete(session.close())
    finally:
        loop.close()


async def _open_session() -> aiohttp.ClientSession:
    """Build a session on the running loop, as aiohttp requires."""
    return aiohttp.ClientSession()


TEST_USERNAME = "C0000000"
TEST_ACCOUNT = "CG0000000"
TEST_GAS_SERVICE = "S0000000001"
TEST_ELECTRICITY_SERVICE = "S0000000002"
TEST_CUI = "PT0000000000000000XX"
TEST_ADDRESS = "RUA EXAMPLE 1, APT 2A, 1000-000 LISBOA"

ADDRESS_PAYLOAD = {
    "address": "RUA EXAMPLE",
    "postCode": "1000-000",
    "city": "LISBOA",
    "doorNo": "1",
    "duplicator": "APT 2A",
}


def account_payload() -> dict:
    """A ``billingAccounts/single`` result, shaped like the live one."""
    return {
        "billingAccountNo": TEST_ACCOUNT,
        "status": 1,
        "initDate": "2025-10-01T00:00:00",
        "endDate": None,
        "nextReadingDate": "2026-08-20T00:00:00",
        "balanceAmount": 12.34,
        "lastInvoice": None,
        "directDebit": {"iban": "PT50000000000000000000000", "adc": "00000000"},
        "electronicInvoice": {"email": "user@example.pt", "active": True},
        "mgmVoucherCode": "MGM0000000",
        "memberGetMemberInfo": {"totalCollectedFriends": 2, "totalProfit": 20},
        "services": [
            {"no": TEST_GAS_SERVICE, "type": 1, "gas": None, "electricity": None},
            {
                "no": TEST_ELECTRICITY_SERVICE,
                "type": 0,
                "gas": None,
                "electricity": None,
            },
        ],
    }


def gas_service_payload() -> dict:
    """A ``services/single`` result for gas, shaped like the live one."""
    meter = {
        "meterNo": "CNTGAS0000000",
        "serialNo": "00000000000000",
        "digits": 5,
        "smartMeter": False,
        "recordTypes": [0],
    }
    return {
        "no": TEST_GAS_SERVICE,
        "type": 1,
        "description": "Gás",
        "status": 1,
        "endDate": None,
        "gas": {"energyType": 0, "cui": TEST_CUI, "escalao": "1", "meter": meter},
        "electricity": None,
        "campaignList": [
            {"no": "DIGITAL_01/26", "name": "", "active": True},
            {"no": "OLD_01/25", "name": "", "active": False},
        ],
    }


def electricity_service_payload() -> dict:
    """A ``services/single`` result for electricity — assumed shape, unobserved."""
    return {
        "no": TEST_ELECTRICITY_SERVICE,
        "description": "Eletricidade",
        "status": 1,
        "endDate": None,
        "gas": None,
        "electricity": {
            "energyType": 1,
            "meter": {
                "meterNo": "CNTELE0000000",
                "serialNo": "11111111111111",
                "smartMeter": False,
                "recordTypes": [0, 1],
            },
        },
    }


def gas_reading(entry_no: int, iso: str, value: float, **extra) -> dict:
    """One ``readings/pagelist`` item for gas, shaped like the live one."""
    return {
        "entryNo": entry_no,
        "cancelled": False,
        "canCancel": False,
        "energyType": 0,
        "meterNo": "CNTGAS0000000",
        "date": f"{iso}T00:00:00",
        "type": 0,
        "source": 5,
        "records": [{"type": 0, "description": "", "value": value}],
        **extra,
    }


def electricity_reading(entry_no: int, iso: str, *values: float) -> dict:
    """One electricity reading with a register per tariff period — assumed shape."""
    return {
        "entryNo": entry_no,
        "cancelled": False,
        "energyType": 1,
        "date": f"{iso}T00:00:00",
        "records": [{"type": i, "value": v} for i, v in enumerate(values)],
    }


def invoice(posted: str, amount: float, remaining: float, start: str, end: str) -> dict:
    """One ``calc-docs`` item — field names from the front end, unobserved live."""
    return {
        "entryNo": 1,
        "documentNo": f"FT {posted}",
        "postingDate": f"{posted}T00:00:00",
        "dueDate": "2026-08-17T00:00:00" if remaining else f"{posted}T00:00:00",
        "amount": amount,
        "remainingAmount": remaining,
        "billingPeriodInitDate": f"{start}T00:00:00",
        "billingPeriodEndDate": f"{end}T00:00:00",
    }


@pytest.fixture
def raw_payload():
    """A full ``client.async_get_data`` payload for a dual-fuel account."""
    return {
        "account": account_payload(),
        "invoices": [
            invoice("2026-07-23", 12.34, 12.34, "2026-06-21", "2026-07-20"),
            invoice("2026-06-20", 25.67, 0, "2026-05-17", "2026-06-20"),
            # Older than the 12-month window used by billed_12m.
            invoice("2024-06-20", 99.99, 0, "2024-05-21", "2024-06-20"),
        ],
        "services": {
            "gas": {
                "service": gas_service_payload(),
                # Newest first, as the API pages them.
                "readings": [
                    gas_reading(5, "2026-07-30", 320),
                    # Same day reported twice: keep the higher index.
                    gas_reading(4, "2026-07-30", 310),
                    # A cancelled reading must be ignored entirely.
                    gas_reading(6, "2026-07-20", 999, cancelled=True),
                    gas_reading(3, "2026-07-11", 300),
                    gas_reading(2, "2026-04-13", 200),
                    gas_reading(1, "2025-10-21", 100),
                ],
            },
            "electricity": {
                "service": electricity_service_payload(),
                "readings": [
                    electricity_reading(8, "2026-07-30", 1100, 560),
                    electricity_reading(7, "2026-06-30", 1000, 500),
                ],
            },
        },
    }


@pytest.fixture
def mock_coordinator():
    """Mock GoldenergyCoordinator with normalised data."""
    coordinator = MagicMock()
    coordinator.data = {
        "available": True,
        "conversion_factor": 11.2,
        "account_status": 1,
        "next_reading_date": None,
        "balance": 12.34,
        "direct_debit": True,
        "electronic_invoice": True,
        "contract_start": None,
        "referral_code": "MGM0000000",
        "referral_link": "https://amigo.goldenergy.pt/MGM0000000",
        "referral_friends": 2,
        "referral_earnings": 20.0,
        "last_invoice_total": 12.34,
        "last_invoice_number": "FT 2026-07-23",
        "amount_due": 12.34,
        "billed_12m": 38.01,
        "invoice_pending": True,
        "services": {
            "gas": {
                "service_no": TEST_GAS_SERVICE,
                "delivery_point": TEST_CUI,
                "tier": "1",
                "meter_serial": "00000000000000",
                "meter_number": "CNTGAS0000000",
                "meter_digits": 5,
                "smart_meter": False,
                "campaigns": "DIGITAL_01/26",
                "meter_index": 320.0,
                "meter_index_energy": 3584.0,
                "conversion_factor": 11.2,
                "last_reading_iso": "2026-07-30",
                "last_consumption": 20.0,
                "last_consumption_energy": 224.0,
                "last_consumption_from": "2026-07-11",
                "last_consumption_days": 19,
            },
            "electricity": {
                "service_no": TEST_ELECTRICITY_SERVICE,
                "meter_index": 1660.0,
                "last_reading_iso": "2026-07-30",
                "last_consumption": 160.0,
                "last_consumption_from": "2026-06-30",
                "last_consumption_days": 30,
            },
        },
    }
    coordinator.billing_account = TEST_ACCOUNT
    coordinator.energies = {"gas", "electricity"}
    coordinator.last_update_success = True
    coordinator.client = MagicMock()
    coordinator.client.close = AsyncMock()
    return coordinator


@pytest.fixture
def mock_config_entry():
    """Mock ConfigEntry for one billing account with both energies enabled."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: "secret",
        CONF_BILLING_ACCOUNT: TEST_ACCOUNT,
        CONF_ADDRESS: TEST_ADDRESS,
    }
    entry.options = {CONF_ENABLE_GAS: True, CONF_ENABLE_ELECTRICITY: True}
    return entry
