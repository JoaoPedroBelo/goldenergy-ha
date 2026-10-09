"""Data update coordinator for the Goldenergy integration."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GoldenergyAuthError, GoldenergyClient, GoldenergyError
from .const import (
    CONF_BILLING_ACCOUNT,
    CONF_CONVERSION_FACTOR,
    CONF_ENABLE_ELECTRICITY,
    CONF_ENABLE_GAS,
    CONF_PASSWORD,
    CONF_USERNAME,
    DATA_ACCOUNT_STATUS,
    DATA_AMOUNT_DUE,
    DATA_AVAILABLE,
    DATA_BALANCE,
    DATA_BILLED_12M,
    DATA_CAMPAIGNS,
    DATA_CONTRACT_START,
    DATA_CONVERSION_FACTOR,
    DATA_DELIVERY_POINT,
    DATA_DIRECT_DEBIT,
    DATA_ELECTRONIC_INVOICE,
    DATA_INVOICE_PENDING,
    DATA_INVOICE_SERIES,
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
    DATA_READINGS,
    DATA_REFERRAL_CODE,
    DATA_REFERRAL_EARNINGS,
    DATA_REFERRAL_FRIENDS,
    DATA_REFERRAL_LINK,
    DATA_SERVICE_NO,
    DATA_SERVICE_STATUS,
    DATA_SERVICES,
    DATA_SMART_METER,
    DATA_TIER,
    DEFAULT_CONVERSION_FACTOR,
    DOMAIN,
    ENERGY_ELECTRICITY,
    ENERGY_GAS,
    POLL_HOURS,
    POLL_MINUTE,
    REFERRAL_LINK_BASE,
)
from .statistics import async_import_statistics

_LOGGER = logging.getLogger(__name__)

# Window used for the "billed in the last 12 months" total.
BILLING_YEAR_DAYS = 365


def enabled_energies(config: dict[str, Any]) -> set[str]:
    """Return the energies a merged entry config enables.

    Both default to on: an entry created before the toggles existed tracked
    everything the account had.
    """
    energies = set()
    if config.get(CONF_ENABLE_GAS, True):
        energies.add(ENERGY_GAS)
    if config.get(CONF_ENABLE_ELECTRICITY, True):
        energies.add(ENERGY_ELECTRICITY)
    return energies


class GoldenergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the Goldenergy API and expose normalised data to entities.

    All entity state lives here in ``self.data``; entities only ever read it and
    return ``None`` for missing values.
    """

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialise the coordinator from the merged config entry data."""
        self.billing_account: str = config[CONF_BILLING_ACCOUNT]
        self.energies: set[str] = enabled_energies(config)
        # kWh/m³ used to bill the gas supply (see CONF_CONVERSION_FACTOR).
        self.conversion_factor: float = _to_positive_float(
            config.get(CONF_CONVERSION_FACTOR), DEFAULT_CONVERSION_FACTOR
        )

        self.client = GoldenergyClient(
            username=config[CONF_USERNAME],
            password=config[CONF_PASSWORD],
        )
        self._unsub_schedule: list[CALLBACK_TYPE] = []

        # No periodic ``update_interval``: we poll on a fixed twice-daily
        # schedule instead (see ``async_setup_schedule``).
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)

    @callback
    def async_setup_schedule(self) -> None:
        """Register the fixed twice-daily refreshes (see ``POLL_HOURS``)."""

        async def _scheduled_refresh(_now: Any) -> None:
            await self.async_request_refresh()

        for hour in POLL_HOURS:
            self._unsub_schedule.append(
                async_track_time_change(
                    self.hass,
                    _scheduled_refresh,
                    hour=hour,
                    minute=POLL_MINUTE,
                    second=0,
                )
            )

    def async_teardown_schedule(self) -> None:
        """Cancel all scheduled refreshes."""
        while self._unsub_schedule:
            self._unsub_schedule.pop()()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and normalise the latest data for this billing account."""
        try:
            raw = await self.client.async_get_data(self.billing_account, self.energies)
        except GoldenergyAuthError as err:
            # Auth errors are not transient - surface as a reauth so HA prompts
            # the user rather than retrying forever.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except GoldenergyError as err:
            raise UpdateFailed(f"Error communicating with Goldenergy: {err}") from err

        data = self._normalise(raw, dt_util.now().date(), self.conversion_factor)
        for energy in self.energies - set(data[DATA_SERVICES]):
            _LOGGER.warning(
                "%s is enabled but billing account %s has no such supply",
                energy,
                self.billing_account,
            )
        await self._async_import_statistics(data)
        return data

    async def _async_import_statistics(self, data: dict[str, Any]) -> None:
        """Feed readings and invoices into the dashboards' long-term statistics.

        A statistics hiccup (recorder not ready, etc.) must never fail the poll,
        so failures are logged and swallowed — the sensors still update.
        """
        services = data.get(DATA_SERVICES) or {}
        readings = {
            energy: service.get(DATA_READINGS) or []
            for energy, service in services.items()
        }
        try:
            await async_import_statistics(
                self.hass,
                self.billing_account,
                readings,
                self.conversion_factor,
                data.get(DATA_INVOICE_SERIES) or [],
            )
        except Exception:  # a stats failure must never fail the poll
            _LOGGER.warning("Failed to import Goldenergy statistics", exc_info=True)

    @classmethod
    def _normalise(
        cls,
        raw: dict[str, Any],
        today: date,
        conversion_factor: float = DEFAULT_CONVERSION_FACTOR,
    ) -> dict[str, Any]:
        """Map the raw API payloads into flat coordinator data."""
        data: dict[str, Any] = {
            DATA_AVAILABLE: True,
            DATA_CONVERSION_FACTOR: conversion_factor,
            DATA_SERVICES: {},
        }
        cls._add_account(data, raw.get("account"))
        cls._add_invoices(data, raw.get("invoices"), today)

        services = raw.get("services")
        if isinstance(services, dict):
            for energy, payload in services.items():
                if isinstance(payload, dict):
                    data[DATA_SERVICES][energy] = _normalise_service(
                        energy, payload, conversion_factor
                    )
        return data

    @staticmethod
    def _add_account(data: dict[str, Any], account: Any) -> None:
        """Normalise the billing account's own fields."""
        if not isinstance(account, dict):
            return
        data[DATA_ACCOUNT_STATUS] = account.get("status")
        data[DATA_NEXT_READING_DATE] = _parse_date(account.get("nextReadingDate"))
        data[DATA_BALANCE] = _to_float(account.get("balanceAmount"))
        data[DATA_DIRECT_DEBIT] = isinstance(account.get("directDebit"), dict)
        data[DATA_CONTRACT_START] = _parse_date(account.get("initDate"))

        invoice = account.get("electronicInvoice")
        data[DATA_ELECTRONIC_INVOICE] = isinstance(invoice, dict) and bool(
            invoice.get("active")
        )

        code = account.get("mgmVoucherCode")
        if isinstance(code, str) and code.strip():
            data[DATA_REFERRAL_CODE] = code.strip()
            data[DATA_REFERRAL_LINK] = f"{REFERRAL_LINK_BASE}{code.strip()}"
        member = account.get("memberGetMemberInfo")
        if isinstance(member, dict):
            friends = member.get("totalCollectedFriends")
            data[DATA_REFERRAL_FRIENDS] = (
                friends
                if isinstance(friends, int) and not isinstance(friends, bool)
                else None
            )
            data[DATA_REFERRAL_EARNINGS] = _to_float(member.get("totalProfit"))

    @staticmethod
    def _add_invoices(data: dict[str, Any], invoices: Any, today: date) -> None:
        """Normalise the latest invoice plus the aggregates over all invoices.

        Field names come from the front end that renders this list; the account
        captured live had no invoice yet (see docs/API.md).
        """
        if not isinstance(invoices, list):
            return
        parsed = [_parse_invoice(inv) for inv in invoices if isinstance(inv, dict)]

        dated = [inv for inv in parsed if inv["posted"] is not None]
        if dated:
            last = max(dated, key=lambda inv: inv["posted"])
            data[DATA_LAST_INVOICE_TOTAL] = last["amount"]
            data[DATA_LAST_INVOICE_DUE] = last["due"]
            data[DATA_LAST_INVOICE_POSTED] = last["posted"]
            data[DATA_LAST_INVOICE_PERIOD_START] = last["period_start"]
            data[DATA_LAST_INVOICE_PERIOD_END] = last["period_end"]
            data[DATA_LAST_INVOICE_NUMBER] = last["number"]

        amount_due = round(sum(max(inv["remaining"] or 0.0, 0.0) for inv in parsed), 2)
        data[DATA_AMOUNT_DUE] = amount_due
        data[DATA_INVOICE_PENDING] = amount_due > 0

        cutoff = today - timedelta(days=BILLING_YEAR_DAYS)
        data[DATA_BILLED_12M] = round(
            sum(
                inv["amount"] or 0.0
                for inv in dated
                if inv["posted"] is not None and inv["posted"] >= cutoff
            ),
            2,
        )
        data[DATA_INVOICE_SERIES] = _invoice_series(parsed)


def _normalise_service(
    energy: str, payload: dict[str, Any], conversion_factor: float
) -> dict[str, Any]:
    """Map one service and its reading history into a flat dict."""
    service = payload.get("service")
    service = service if isinstance(service, dict) else {}
    supply = service.get(energy)
    supply = supply if isinstance(supply, dict) else {}
    meter = supply.get("meter")
    meter = meter if isinstance(meter, dict) else {}

    data: dict[str, Any] = {
        DATA_SERVICE_NO: service.get("no"),
        DATA_SERVICE_STATUS: service.get("status"),
        # ``cui`` is verified on gas. Electricity's delivery point is unobserved;
        # nothing is guessed for it, so the attribute is simply absent there.
        DATA_DELIVERY_POINT: supply.get("cui"),
        DATA_TIER: supply.get("escalao"),
        DATA_METER_SERIAL: meter.get("serialNo"),
        DATA_METER_NUMBER: meter.get("meterNo"),
        DATA_METER_DIGITS: meter.get("digits"),
        DATA_SMART_METER: meter.get("smartMeter"),
        DATA_CAMPAIGNS: _active_campaigns(service.get("campaignList")),
    }

    readings = _parse_readings(payload.get("readings"))
    if not readings:
        return data

    data[DATA_READINGS] = readings
    latest = readings[-1]
    data[DATA_METER_INDEX] = latest["index"]
    data[DATA_LAST_READING_ISO] = latest["iso"]
    if energy == ENERGY_GAS:
        # The supplier bills gas in kWh, so mirror every volume in energy too.
        data[DATA_METER_INDEX_ENERGY] = round(latest["index"] * conversion_factor, 2)
        data[DATA_CONVERSION_FACTOR] = conversion_factor

    if len(readings) >= 2:
        previous = readings[-2]
        delta = round(max(latest["index"] - previous["index"], 0.0), 3)
        data[DATA_LAST_CONSUMPTION] = delta
        data[DATA_LAST_CONSUMPTION_FROM] = previous["iso"]
        data[DATA_LAST_CONSUMPTION_DAYS] = (
            date.fromisoformat(latest["iso"]) - date.fromisoformat(previous["iso"])
        ).days
        if energy == ENERGY_GAS:
            data[DATA_LAST_CONSUMPTION_ENERGY] = round(delta * conversion_factor, 2)
    return data


def _active_campaigns(campaigns: Any) -> str | None:
    """Return the active campaign codes, comma-joined, or ``None`` if there is none.

    Campaigns arrive as ``[{"no": "DIGITAL_01/26", "name": "", "active": true}]``;
    ``name`` was empty live, so the code is what identifies them.
    """
    if not isinstance(campaigns, list):
        return None
    codes = [
        str(c.get("name") or c.get("no")).strip()
        for c in campaigns
        if isinstance(c, dict) and c.get("active") and (c.get("name") or c.get("no"))
    ]
    return ", ".join(codes) or None


def _parse_readings(items: Any) -> list[dict[str, Any]]:
    """Build ``[{"iso", "index"}]`` from ``readings/pagelist`` items.

    Each reading carries one ``records`` entry per meter register. Gas has a
    single register (verified), whose ``value`` is the cumulative index in m³.
    A multi-rate electricity meter presumably has one per tariff period; their
    sum is the meter's total kWh, which is what the statistics need — but that
    shape is unverified (see docs/API.md).

    Cancelled readings are dropped, duplicated days collapse to the highest index,
    and the result is sorted oldest first.
    """
    if not isinstance(items, list):
        return []

    by_iso: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("cancelled"):
            continue
        reading_date = _parse_date(item.get("date"))
        index = _sum_records(item.get("records"))
        if reading_date is None or index is None:
            continue
        iso = reading_date.isoformat()
        existing = by_iso.get(iso)
        if existing is None or index > existing["index"]:
            by_iso[iso] = {"iso": iso, "index": index}

    return [by_iso[iso] for iso in sorted(by_iso)]


def _sum_records(records: Any) -> float | None:
    """Return the total across a reading's registers, or ``None`` if there is none."""
    if not isinstance(records, list):
        return None
    values = [_to_float(r.get("value")) for r in records if isinstance(r, dict)]
    numbers = [v for v in values if v is not None]
    return round(sum(numbers), 3) if numbers else None


def _parse_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields of one ``calc-docs`` item this integration uses."""
    return {
        "number": invoice.get("documentNo"),
        "posted": _parse_date(invoice.get("postingDate")),
        "due": _parse_date(invoice.get("dueDate")),
        "period_start": _parse_date(invoice.get("billingPeriodInitDate")),
        "period_end": _parse_date(invoice.get("billingPeriodEndDate")),
        "amount": _to_float(invoice.get("amount")),
        "remaining": _to_float(invoice.get("remainingAmount")),
    }


def _invoice_series(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build ``[{"iso", "total"}]`` from parsed invoices, oldest first.

    Each invoice is keyed by the **end of the period it bills**, not its posting
    date: that is when the money was actually incurred. Invoices closing on the
    same day are merged, which is what makes the series safe to accumulate.
    """
    by_iso: dict[str, float] = {}
    for inv in invoices:
        closes = inv["period_end"] or inv["posted"]
        if closes is None or inv["amount"] is None:
            continue
        iso = closes.isoformat()
        by_iso[iso] = round(by_iso.get(iso, 0.0) + inv["amount"], 2)

    return [{"iso": iso, "total": by_iso[iso]} for iso in sorted(by_iso)]


def _parse_date(raw: Any) -> date | None:
    """Convert an API timestamp (``2026-03-02T00:00:00``) to a ``date``.

    The API sends local midnight without an offset; only the date part means
    anything.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = dt_util.parse_datetime(raw.strip())
    if parsed is not None:
        return parsed.date()
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _to_positive_float(raw: Any, default: float) -> float:
    """Coerce a configured number, falling back when absent or nonsensical."""
    value = _to_float(raw)
    if value is None or value <= 0:
        return default
    return value


def _to_float(raw: Any) -> float | None:
    """Coerce an API value into a float (numbers here are JSON numbers)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip().replace(",", "."))
        except ValueError:
            return None
    return None
