# Goldenergy Integration

Monitor your **Goldenergy** electricity and gas supply from the customer area
(`clientes.goldenergy.pt`) in Home Assistant.

Goldenergy publishes no API, but its customer area is filled in by a REST API
behind it; this integration logs in and reads that same API. No HTML scraping.

## Features

- **Electricity and gas** — track either or both, chosen at setup and changeable
  in the options
- **Meter readings** — cumulative index, consumption since the previous reading,
  reading date
- **Gas in both units** — m³ mirrored in kWh at a configurable conversion factor
- **Energy dashboard** — long-term statistics for electricity, gas (m³ and kWh)
  and **cost built from the totals actually invoiced**
- **Billing** — last invoice and due date, amount owed, 12-month total, balance,
  unpaid-invoice flag
- **Multi-account** — one entry per billing account
- **Cloud polling** — twice a day (02:00 & 14:00)
- English 🇬🇧 and Portuguese 🇵🇹

## Quick Start

1. Install via HACS and restart Home Assistant
2. **Settings → Devices & Services → Add Integration → Goldenergy**
3. Enter your **customer number or NIF** and **password**, pick the billing
   account, and choose gas, electricity or both
4. If you track gas, open **Configure** and set the **conversion factor** (kWh/m³)
   printed on your invoice
5. Wire the statistics into **Settings → Dashboards → Energy**

## Author

**João Belo** — independent, open-source project. Not affiliated with Goldenergy.
