# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- Config flow: customer number/NIF + password, billing-account picker, and a
  choice of gas, electricity or both (only energies the account supplies can be
  enabled). Re-authentication flow.
- Options flow: switch either energy on or off, and set the gas conversion factor
  (kWh/m³). Switching an energy off removes its entities.
- Login through the captcha-free `/api/user-auth/token` endpoint, once per poll.
- Per-energy sensors: meter index, consumption since the previous reading, last
  reading date; gas also in kWh.
- Account sensors: next reading date, balance, last invoice total and due date,
  amount due, billed over the last 12 months; invoice-pending and availability
  binary sensors.
- Long-term statistics: gas volume (m³), gas energy (kWh, derived from volume),
  electricity (kWh), and invoiced cost — readings spread over the days they cover.
- Twice-daily polling (02:00 / 14:00).
- English and Portuguese translations.

### Known gaps

- Invoice fields and all electricity payloads are coded from the customer area's
  front-end code; the account used to map the API was new and gas-only.
