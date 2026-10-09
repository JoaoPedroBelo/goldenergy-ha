# Goldenergy — Home Assistant Integration

Home Assistant custom integration for the **Goldenergy** customer area
(`clientes.goldenergy.pt`) — a Portuguese electricity and natural-gas supplier.

**Key concept:** the customer area is a WordPress site whose pages are filled by
jQuery calls to a separate **ASP.NET REST API** (`api-clientes-bc.goldenergy.pt`).
Home Assistant logs in through the API's **captcha-free** `/api/user-auth/token`
endpoint and calls the same endpoints the front end calls. No HTML scraping.

## Documentation-first

Read the relevant doc in `custom_components/goldenergy/docs/` **before** changing
the API client, coordinator, or entities:

| Doc | Read when |
|-----|-----------|
| `ARCHITECTURE.md` | Component relationships, data flow, token lifecycle |
| `API.md` | The reverse-engineered requests, response shapes, what is verified and what is not |

## Core files

| File | Purpose |
|------|---------|
| `api.py` | **All API network logic.** Private session, login, bearer calls, pagination, gas/electricity service classification. |
| `coordinator.py` | `DataUpdateCoordinator`; normalises the raw payloads into `self.data` (account keys + one dict per energy under `services`). Twice-daily schedule. |
| `statistics.py` | Imports up to four external statistics per account: gas volume (m³), gas energy (kWh, derived), electricity (kWh), cost. Readings are spread over the days they cover and the window is rewritten each poll. |
| `entity.py` | Shared entity base: unique ids, the per-account device, `_source(energy)`. |
| `const.py` | `Final`-typed constants: config keys, endpoints, enumerations, entity keys, `coordinator.data` keys, `POLL_HOURS`. |
| `config_flow.py` / `__init__.py` | Config UI (login → account → energy toggles; options flow) / entry point (schedule, reload on options, stale-entity cleanup). |
| `sensor.py` / `binary_sensor.py` | Entities, declared as description tables; `energy=None` means account level. |

## Critical rules

1. **All API network logic in `api.py`.** The coordinator never talks HTTP
   directly.
2. **All state in the coordinator** — entities read `self.coordinator.data` and
   return `None` for missing values.
3. **Never switch the login to `/api/user-auth/rtoken`.** It validates a
   reCAPTCHA v3 token server-side, which a headless client cannot produce.
   `/token` is the only usable login; if Goldenergy removes it, the integration
   breaks and must surface re-authentication, not retry in a loop.
4. **Log in per poll; do not add refresh-token handling.** The access token lives
   120 s and the refresh token 30 min, so with a twice-daily poll a refresh never
   applies.
5. **Per-energy entity keys must start with `<energy>_`** — the cleanup that removes
   a disabled energy's entities matches on that prefix.
6. **Poll twice a day (02:00 / 14:00)** via `async_track_time_change` — no tight
   periodic loop.
7. **Never commit or log credentials, tokens, or the NIF/IBAN/CUI** the payloads
   carry. Use placeholders in tests and docs. Raw captures live in `.capture/`,
   which is git-ignored.
8. **Log via `_LOGGER`** — never `print()` (ruff `T20`).
9. Log in, retry once on 401, then fail.
10. A statistics failure must never fail the poll.

## Data notes

- Reading `records[].value` is the **cumulative meter index**, a JSON number: m³
  for gas, kWh for electricity. A reading may carry several registers; their sum is
  the index (verified for gas's single register only).
- Dates are ISO 8601 **without an offset** (`2026-03-02T00:00:00`), local midnight.
- Every response is wrapped in `{result, hasError, errorCode, message}`; an
  application error can answer **200 with `hasError: true`**.
- `billingAccounts/single` lists services with `gas`/`electricity` both `null`;
  only `services/single` says which energy a service supplies.
- **Unverified shapes** (the captured account was new and gas-only): invoice
  items (`calc-docs`), and everything electricity. They are coded from the front
  end's field names; re-verify against a populated account and update `API.md`
  and `tests/conftest.py` together.

## Development

Prefer the project venvs directly:

```bash
.venv/bin/ruff check custom_components/ tests/ scripts/    # lint (blocks CI)
.venv/bin/ruff format custom_components/ tests/ scripts/   # format
.venv/bin/mypy custom_components/goldenergy        # types (advisory in CI)
.venv/bin/pytest tests/ -q                         # tests, HA 2024.3 / py3.11
.venv-ci/bin/pytest tests/ -q                      # tests, HA 2025.1 / py3.12
```

Or via Make: `make lint`, `make format`, `make test`, `make coverage`, `make check`, `make brand`.

Test dependencies are **pinned**, in two sets:

| File | Home Assistant | Python | Why |
|------|----------------|--------|-----|
| `requirements-test.txt` | 2025.1.4 | 3.12 | what users run |
| `requirements-test-min.txt` | 2024.3.3 | 3.11 | proves the floor in `hacs.json` |

CI runs both. Never loosen them back to `>=`. Installing `requirements-test.txt`
with `uv` needs `--prerelease=allow` (Home Assistant pins a pre-release
dependency); plain `pip` accepts it.

`mypy` reports one known false positive on `config_flow.py`
(`Unexpected keyword argument "domain"`): it cannot see HA's
`__init_subclass__(domain=...)`. This is why types are advisory in CI.

## Testing

**Tests are mandatory** for every new sensor, endpoint, or normalisation change —
use the `raw_payload` / `mock_coordinator` / `mock_config_entry` fixtures in
`tests/conftest.py`.

- **`tests/test_init.py`** boots a **real Home Assistant**, adds a config entry and
  asserts the entities and the long-term statistics actually materialise, and that
  disabling an energy removes its entities. Keep it passing.
- **`tests/test_entity_descriptions.py`** validates every description against HA's
  own `DEVICE_CLASS_STATE_CLASSES` / `DEVICE_CLASS_UNITS` tables, so an illegal
  combination fails in CI instead of at runtime.

Gotchas carried over from the CUR integration: `recorder_mock` must be built
before anything pulls in `hass` (one autouse fixture requests them in order), and
the recorder needs `psutil-home-assistant`, `SQLAlchemy` and `fnv-hash-fast`,
pinned in the requirements files for that reason.

## Brand assets

`custom_components/goldenergy/brand/` holds the **official Goldenergy logo**, used
nominatively to identify the service this integration talks to.

- `goldenergy-logo.svg` is the **source of truth**, taken verbatim from
  `https://clientes.goldenergy.pt/sws-content/uploads/2023/06/logo-roxo.svg`.
- The four PNGs are **generated**: run `make brand`
  (`scripts/generate_brand_assets.py`, needs Pillow + cairosvg). Never hand-edit them.
- `icon.png` is the ring mark (the "o" of "gold", also Goldenergy's favicon), cut
  out by `MARK_BOX` and centred on a transparent square. `logo.png` is the full
  lockup.

The generator lives outside `custom_components/` on purpose: the HACS release zip
only packs the component directory. The logo is the property of its owner and is
**not** covered by this project's MIT licence; the README disclaimer says so.

## Git & releases

- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`,
  `perf:`, `ci:`).
- **No AI attribution** anywhere. **No commits unless explicitly asked.**
- Releases: bump `custom_components/goldenergy/manifest.json`, add a
  `CHANGELOG.md` entry, tag `vX.Y.Z` (the tag must match the manifest version —
  enforced by `release.yml`).
