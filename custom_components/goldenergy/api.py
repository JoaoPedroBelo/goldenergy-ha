"""HTTP client for the Goldenergy customer area.

The customer area at https://clientes.goldenergy.pt is a WordPress site whose
pages are filled by jQuery calls to a separate ASP.NET REST API. This client calls
that API directly — the same endpoints, the same bearer token.

Auth model::

    POST /api/user-auth/token {"code": <customer number or NIF>, "password": ...}
        -> {"result": {"token": <JWT, 120 s>, "refreshToken": <JWT, 30 min>}}

The web form logs in through ``/api/user-auth/rtoken`` instead, which validates a
reCAPTCHA v3 token server-side; ``/token`` is the captcha-free path the front end
keeps as a fallback, and the only one a headless client can use.

The access token lives 120 s and the refresh token 30 min, so with a twice-daily
poll there is nothing worth refreshing: each poll logs in once and makes its burst
of calls well inside the token's lifetime. A token that expires mid-poll anyway is
caught by its ``exp`` claim, or by a 401, and replaced with one fresh login.

See docs/API.md for the captured requests this client is based on.
"""

from __future__ import annotations

import base64
from datetime import date
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    API_ENERGY_TYPE,
    ENERGY_ELECTRICITY,
    ENERGY_GAS,
    EP_ACCOUNT,
    EP_ACCOUNTS,
    EP_INVOICES,
    EP_LOGIN,
    EP_READINGS,
    EP_SERVICE,
    EP_SUBMIT_READING,
    MAX_PAGES,
    PAGE_SIZE,
    READING_ERROR_ABOVE_AVERAGE,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

# Log in again this many seconds before the access token's ``exp``, so a call
# never races the expiry. The token only lives 120 s.
TOKEN_EXPIRY_MARGIN = 15

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}


class GoldenergyError(Exception):
    """Base error for the Goldenergy client."""


class GoldenergyAuthError(GoldenergyError):
    """Raised when authentication fails (bad credentials or a rejected token)."""


class GoldenergyConnectionError(GoldenergyError):
    """Raised when the API cannot be reached or answers with a server error."""


class GoldenergyReadingRejected(GoldenergyError):
    """Raised when Goldenergy refuses a submitted meter reading.

    Carries the API's own ``errorCode`` and human-readable ``message`` (in
    Portuguese), e.g. ``"2"`` / "Erro - Leitura inferior à última registada!".
    """

    def __init__(self, code: str | None, message: str) -> None:
        """Keep the API's code and message."""
        super().__init__(f"Reading rejected ({code}): {message}")
        self.code = code
        self.message = message


class GoldenergyAboveAverage(GoldenergyReadingRejected):
    """Raised when a reading implies above-average consumption (``errorCode`` 1).

    The web form treats this as a question, not a refusal: it asks the customer
    to confirm and resends with ``ignoreAboveAverageConsumptionValidation``.
    """


def service_energy(service: Any) -> str | None:
    """Return which energy a ``services/single`` payload supplies, if any.

    The account's service list carries ``gas``/``electricity`` as ``null`` for
    every service; only ``services/single`` fills in the one that applies. A
    service with neither (a value-added service, e.g. insurance) is not an energy
    supply and yields ``None``.
    """
    if not isinstance(service, dict):
        return None
    if isinstance(service.get(ENERGY_GAS), dict):
        return ENERGY_GAS
    if isinstance(service.get(ENERGY_ELECTRICITY), dict):
        return ENERGY_ELECTRICITY
    return None


def _token_expiry(token: str) -> float | None:
    """Return the JWT's ``exp`` claim (epoch seconds), or ``None`` if unreadable.

    The signature is not verified: the server does that. The claim is only used
    to decide when to log in again.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return float(exp) if isinstance(exp, (int, float)) else None


class GoldenergyClient:
    """Client for the Goldenergy customer-area REST API."""

    def __init__(self, username: str, password: str) -> None:
        """Store credentials. The session is created lazily on first use."""
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        # Wall-clock deadline: it comes from the JWT's ``exp``, which is epoch time.
        self._expires_at = 0.0

    # --- session lifecycle -------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the private session on first use (never the shared HA one)."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=_DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying session (call on unload)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._forget_token()

    def _forget_token(self) -> None:
        """Drop the cached token so the next call logs in again."""
        self._token = None
        self._expires_at = 0.0

    # --- authentication ----------------------------------------------------

    async def async_login(self) -> None:
        """Log in with the captcha-free endpoint. Raises on failure."""
        session = await self._ensure_session()
        self._forget_token()
        try:
            async with session.post(
                f"{API_BASE_URL}{EP_LOGIN}",
                json={"code": self._username, "password": self._password},
            ) as resp:
                # A 5xx is the API being unavailable; only a 4xx says anything
                # about the credentials. Reporting an outage as an auth failure
                # makes Home Assistant demand a password the user never got wrong,
                # and that path has no automatic retry.
                if resp.status >= 500:
                    raise GoldenergyConnectionError(
                        f"Login returned HTTP {resp.status}"
                    )
                if resp.status >= 400:
                    # Wrong password and unknown customer both answer a bare 401.
                    raise GoldenergyAuthError(f"Login rejected (HTTP {resp.status})")
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GoldenergyConnectionError(f"Login request failed: {err}") from err

        result = body.get("result") if isinstance(body, dict) else None
        token = result.get("token") if isinstance(result, dict) else None
        if not token:
            code = body.get("errorCode") if isinstance(body, dict) else None
            raise GoldenergyAuthError(f"Login returned no token ({code or 'no code'})")

        self._token = str(token)
        expiry = _token_expiry(self._token)
        # An unreadable ``exp`` means "use it once": the next call logs in again.
        self._expires_at = (expiry - TOKEN_EXPIRY_MARGIN) if expiry else 0.0
        # Deliberately without the username: debug logs get pasted into bug reports.
        _LOGGER.debug("Goldenergy login successful")

    async def _ensure_token(self) -> str:
        """Return a usable access token, logging in when there is none left."""
        if self._token and time.time() < self._expires_at:
            return self._token
        await self.async_login()
        return self._token or ""

    # --- requests ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> dict[str, Any]:
        """Call an endpoint with the bearer token and return the whole envelope.

        A 401 means the token went stale despite the bookkeeping, so it is dropped
        and the call retried once with a fresh login. The envelope's ``hasError``
        is left to the caller: a read treats it as a failure, a reading submission
        needs its ``errorCode``.
        """
        token = await self._ensure_token()
        session = await self._ensure_session()
        query = {k: str(v) for k, v in (params or {}).items()}
        try:
            async with session.request(
                method,
                f"{API_BASE_URL}{path}",
                params=query,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status in {401, 403}:
                    raise GoldenergyAuthError(f"{path} returned {resp.status}")
                if resp.status >= 500:
                    raise GoldenergyConnectionError(
                        f"{path} returned HTTP {resp.status}"
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise GoldenergyError(
                        f"{path} returned HTTP {resp.status}: {text[:200]}"
                    )
                body = await resp.json(content_type=None)
        except GoldenergyAuthError:
            if not allow_retry:
                raise
            _LOGGER.debug("Token rejected on %s; logging in again once", path)
            self._forget_token()
            return await self._request(
                method, path, params=params, json_body=json_body, allow_retry=False
            )
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GoldenergyConnectionError(f"{path} request failed: {err}") from err

        if not isinstance(body, dict):
            raise GoldenergyError(f"{path} returned an unexpected payload")
        return body

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an endpoint and return the envelope's ``result``."""
        body = await self._request("GET", path, params=params)
        if body.get("hasError"):
            # Application errors answer 200 with the reason inside the envelope.
            raise GoldenergyError(
                f"{path} failed: {body.get('errorCode') or body.get('message')}"
            )
        return body.get("result")

    async def _get_all_pages(
        self, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Walk a paginated list and return every item, in the API's order.

        Bounded by ``MAX_PAGES`` so a misreported ``totalItems`` cannot turn one
        poll into an unbounded crawl.
        """
        items: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            result = await self._get(
                path, {**params, "PageIndex": page, "PageSize": PAGE_SIZE}
            )
            page_items = result.get("items") if isinstance(result, dict) else None
            if not isinstance(page_items, list) or not page_items:
                break
            items.extend(i for i in page_items if isinstance(i, dict))
            total = result.get("totalItems") if isinstance(result, dict) else None
            if not isinstance(total, int) or len(items) >= total:
                break
        return items

    # --- public API --------------------------------------------------------

    async def async_list_accounts(self) -> list[dict[str, Any]]:
        """Return the customer's billing accounts, as the config flow needs them.

        Shape (verified)::

            [{"billingAccountNo": "CG0000000", "status": 1,
              "consumptionPointAddresses": [
                {"consumptionPointAddress": {...}, "services": [...]}]}]
        """
        result = await self._get(EP_ACCOUNTS)
        accounts = result.get("billingAccounts") if isinstance(result, dict) else None
        if not isinstance(accounts, list):
            return []
        return [
            a for a in accounts if isinstance(a, dict) and a.get("billingAccountNo")
        ]

    async def async_get_services(
        self, billing_account: str, service_nos: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return ``{energy: services/single payload}`` for the given services.

        Only ``services/single`` says which energy a service supplies. When an
        account lists two services of the same energy (an ended contract next to
        its replacement), the active one wins.
        """
        found: dict[str, dict[str, Any]] = {}
        for service_no in service_nos:
            service = await self._get(
                EP_SERVICE,
                {"BillingAccountNo": billing_account, "ServiceNo": service_no},
            )
            energy = service_energy(service)
            if energy is None:
                continue
            current = found.get(energy)
            if current is None or (
                current.get("endDate") is not None and service.get("endDate") is None
            ):
                found[energy] = service
        return found

    async def async_discover_energies(self, billing_account: str) -> set[str]:
        """Return the energies (``gas``/``electricity``) the account supplies."""
        account = await self._get(EP_ACCOUNT, {"BillingAccountNo": billing_account})
        services = await self.async_get_services(
            billing_account, _service_numbers(account)
        )
        return set(services)

    async def async_get_data(
        self, billing_account: str, energies: set[str]
    ) -> dict[str, Any]:
        """Fetch every payload the integration needs for one billing account.

        ``energies`` restricts which services' readings are fetched; the account
        and its invoices are always read since both energies share them.
        """
        account = await self._get(EP_ACCOUNT, {"BillingAccountNo": billing_account})
        raw: dict[str, Any] = {
            "account": account,
            "invoices": await self._get_all_pages(
                EP_INVOICES, {"BillingAccountNo": billing_account}
            ),
            "services": {},
        }

        services = await self.async_get_services(
            billing_account, _service_numbers(account)
        )
        for energy, service in services.items():
            if energy not in energies:
                continue
            readings = await self._get_all_pages(
                EP_READINGS,
                {
                    "BillingAccountNo": billing_account,
                    "ServiceNo": service.get("no"),
                    "EnergyType": API_ENERGY_TYPE[energy],
                },
            )
            raw["services"][energy] = {"service": service, "readings": readings}
        return raw

    async def async_submit_reading(
        self,
        *,
        billing_account: str,
        service_no: str,
        energy: str,
        meter_no: str,
        reading_date: date,
        records: list[dict[str, int]],
        confirm_above_average: bool = False,
    ) -> None:
        """Communicate a meter reading, exactly as the web form does.

        ``records`` is one ``{"type", "value"}`` per meter register, in the
        meter's ``recordTypes``. The body is the format the production front end
        sends (its ``QM_FASE2`` flag is off): ``bcServiceType`` at the top and
        the meter's ``meterNo``, not its serial number.

        A refusal answers **200 with ``hasError``**: ``errorCode`` ``"1"`` asks to
        confirm above-average consumption, ``"2"`` means the value is below the
        last registered reading (verified live).
        """
        energy_type = API_ENERGY_TYPE[energy]
        body: dict[str, Any] = {
            "billingAccountNo": billing_account,
            "ServiceNo": service_no,
            "bcServiceType": energy_type,
            "reading": {
                "energyType": energy_type,
                "meterNo": meter_no,
                "date": reading_date.isoformat(),
                "records": records,
            },
        }
        if confirm_above_average:
            body["ignoreAboveAverageConsumptionValidation"] = True

        envelope = await self._request("POST", EP_SUBMIT_READING, json_body=body)
        if not envelope.get("hasError"):
            return
        code = envelope.get("errorCode")
        code = str(code) if code is not None else None
        # The API pads its Portuguese messages with non-breaking spaces.
        message = " ".join(str(envelope.get("message") or "").split())
        if code == READING_ERROR_ABOVE_AVERAGE:
            raise GoldenergyAboveAverage(code, message)
        raise GoldenergyReadingRejected(code, message or "no reason given")


def _service_numbers(account: Any) -> list[str]:
    """Return the service numbers listed on a ``billingAccounts/single`` payload."""
    services = account.get("services") if isinstance(account, dict) else None
    if not isinstance(services, list):
        return []
    return [str(s["no"]) for s in services if isinstance(s, dict) and s.get("no")]
