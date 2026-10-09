# Goldenergy — Home Assistant Integration

[![Tests](https://img.shields.io/github/actions/workflow/status/JoaoPedroBelo/goldenergy-ha/tests.yml?style=for-the-badge&label=Tests)](https://github.com/JoaoPedroBelo/goldenergy-ha/actions/workflows/tests.yml)
[![HACS Validation](https://img.shields.io/github/actions/workflow/status/JoaoPedroBelo/goldenergy-ha/validate.yml?style=for-the-badge&label=HACS)](https://github.com/JoaoPedroBelo/goldenergy-ha/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/JoaoPedroBelo/goldenergy-ha?style=for-the-badge)](LICENSE)

---

A Home Assistant custom integration for the **Goldenergy** customer area
(`clientes.goldenergy.pt`) — a Portuguese electricity and natural-gas supplier.

Goldenergy publishes no API, but its customer area is filled in by a REST API
behind it. This integration logs in and reads that same API — no HTML scraping.

## ✨ Features

- **⚡🔥 Electricity and gas** — track either or both energies of a billing
  account; chosen at setup, changeable later in the options
- **📟 Meter readings** — cumulative meter index, consumption since the previous
  reading, and the reading date, per energy
- **⚖️ Gas in both units** — the gas meter counts m³ but Goldenergy bills kWh, so
  gas volumes are mirrored in energy at a configurable conversion factor
- **📊 Energy dashboard ready** — readings imported as long-term statistics,
  spread over the days they cover, plus a **cost statistic** built from the totals
  actually invoiced
- **🧾 Billing** — last invoice total and due date, amount still owed, total billed
  over the last 12 months, account balance, and an unpaid-invoice flag
- **📅 Next reading date** — when the next meter reading is expected
- **🏠 Multi-account** — one entry per billing account, each with its own device
- **🕐 Low-profile polling** — twice a day (02:00 & 14:00), no tight loops
- **🇬🇧🇵🇹 Localised** — English and Portuguese

## 🚀 Quick Start

### Installation via HACS

1. HACS → ⋮ → **Custom repositories** → add this repository as an *Integration*
2. Install **Goldenergy** and restart Home Assistant

### Configuration

1. **Settings → Devices & Services → Add Integration → Goldenergy**
2. Enter your **customer number or NIF** and your customer area **password**
3. If you have several billing accounts, pick one (add the integration again for
   the others)
4. Choose what to track: **Gas**, **Electricity**, or both. Only the energies the
   account actually supplies can be enabled
5. If you track gas, open **Configure** and set the **gas conversion factor**
   (kWh/m³) printed on your invoice next to the reading — the default of 11.2 is
   typical but not exact

**Configure** also lets you switch an energy on or off later. Switching one off
removes its entities.

## 🎯 Entities

### Per energy

| Entity | Unit | Notes |
|--------|------|-------|
| Gas meter index | m³ | cumulative; attributes: reading date, CUI, meter serial, tier |
| Gas meter index (energy) | kWh | index × conversion factor |
| Gas consumption since previous reading | m³ | attributes: period start/end, days |
| Gas consumption since previous reading (energy) | kWh | |
| Gas last reading date | date | |
| Electricity meter index | kWh | cumulative, all tariff periods summed |
| Electricity consumption since previous reading | kWh | |
| Electricity last reading date | date | |

### Account

| Entity | Unit |
|--------|------|
| Next reading date | date |
| Account balance | € |
| Last invoice total | € |
| Last invoice due date | date |
| Amount due | € |
| Billed last 12 months | € |
| Invoice pending payment *(binary)* | on/off |
| Service available *(binary, diagnostic, disabled by default)* | on/off |

## 📊 Adding it to the Energy dashboard

The sensors above are informative. The Energy dashboard should be fed by the
**statistics** this integration imports, because readings are backdated and
sparse — a live sensor would put a month of consumption on the poll hour.

| Statistic | Unit | Use for |
|-----------|------|---------|
| `goldenergy:electricity_energy_<account>` | kWh | Electricity grid consumption |
| `goldenergy:gas_volume_<account>` | m³ | Gas consumption, in volume |
| `goldenergy:gas_energy_<account>` | kWh | Gas consumption, in energy |
| `goldenergy:cost_<account>` | € | Cost (*use an entity tracking the total costs*) |

Wire **only one** of the two gas series, or gas is counted twice. The cost series
covers the whole account — every energy on the invoice — so attach it to one
source only.

## 🏷️ Entity IDs

Entities are named after the device, and the device after the **supply address**,
so entity IDs look like `sensor.rua_example_1_apt_2a_1000_000_lisboa_gas_meter_index`.
Rename the device before writing automations if you want shorter IDs.

## 🛠️ Technical Details

- **API**: `api-clientes-bc.goldenergy.pt` (ASP.NET, bearer JWT)
- **Login**: the captcha-free `/api/user-auth/token` endpoint. The web form uses a
  reCAPTCHA-protected endpoint instead; this one is the front end's fallback. If
  Goldenergy ever removes it, the integration will ask you to re-authenticate and
  stop there rather than retrying in a loop
- **Polling**: twice a day; each poll logs in once (the access token lives two
  minutes)
- **Home Assistant**: 2024.3.0 or newer

See [`docs/API.md`](custom_components/goldenergy/docs/API.md) and
[`docs/ARCHITECTURE.md`](custom_components/goldenergy/docs/ARCHITECTURE.md).

### Known gaps

The API was mapped on a brand-new, gas-only account, so two things are coded
from the customer area's own front-end code rather than from observed responses:
the **invoice** fields and everything **electricity**. If a value looks wrong on
your account, please open an issue.

## 🤝 Contributing

```bash
make install   # dev dependencies
make check     # lint + tests
make brand     # regenerate the brand PNGs from the source SVG
```

## 📝 License

MIT — see [LICENSE](LICENSE).

## 👤 Author

**João Belo** ([@JoaoPedroBelo](https://github.com/JoaoPedroBelo))

## ⚠️ Disclaimer

This is an independent, open-source integration for the Goldenergy customer area.
It is not affiliated with, endorsed by, or sponsored by Goldenergy. Use it in
accordance with the customer area's terms of service.

The Goldenergy logo in `custom_components/goldenergy/brand/` is the property of its
owner and is reproduced here nominatively, solely to identify the service this
integration connects to. It is not covered by this project's MIT licence.

## 🔒 Privacy

Your **customer number/NIF** and **password** are stored only in your local Home
Assistant config entry and are sent solely to `goldenergy.pt`. The account
payloads also carry your NIF, IBAN, phone and e-mail: these are never logged and
never exposed as entity state.

Two identifiers *are* surfaced locally, by design: the gas **CUI** and the meter
serial as sensor attributes, the **billing account number** as the device serial
number, and the **supply address** as the device name — which means it also
appears in every entity ID. Rename the device if you would rather it did not.

Never commit real credentials, account numbers or captured payloads to this
repository.
