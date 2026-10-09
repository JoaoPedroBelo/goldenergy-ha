"""Constants for the Goldenergy integration."""

from typing import Final

DOMAIN: Final = "goldenergy"

# --- Configuration keys ---
# The login form accepts either the customer number (C0000000) or the NIF.
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
# The billing account this entry tracks. A Goldenergy customer can hold several
# (one per supply address), so each is configured as its own entry.
CONF_BILLING_ACCOUNT: Final = "billing_account"
CONF_ADDRESS: Final = "address"
# Which energies of the account this entry exposes. Chosen at setup and editable
# in the options, so a dual-fuel account can track only one of them.
CONF_ENABLE_GAS: Final = "enable_gas"
CONF_ENABLE_ELECTRICITY: Final = "enable_electricity"

# --- Gas volume to energy conversion ---
# The gas meter counts m³ but the supplier bills kWh, using the PCS of the
# distribution network printed on the invoice next to the reading. No endpoint
# observed so far returns it (see docs/API.md), so it is an editable option.
# Default and guard rails are the ones already validated in the CUR Gás Natural
# integration for Portuguese networks (~11-12 kWh/m³).
CONF_CONVERSION_FACTOR: Final = "conversion_factor"
DEFAULT_CONVERSION_FACTOR: Final = 11.2
MIN_CONVERSION_FACTOR: Final = 8.0
MAX_CONVERSION_FACTOR: Final = 14.0

# --- Energies ---
ENERGY_GAS: Final = "gas"
ENERGY_ELECTRICITY: Final = "electricity"
ENERGIES: Final = (ENERGY_GAS, ENERGY_ELECTRICITY)
# The API's ``energyType`` / ``EnergyType`` enumeration (see docs/API.md). Gas is
# verified live; electricity is what the front end sends for its smart-meter view.
API_ENERGY_TYPE: Final = {ENERGY_GAS: 0, ENERGY_ELECTRICITY: 1}

# --- Endpoints (verified live; see docs/API.md) ---
API_BASE_URL: Final = "https://api-clientes-bc.goldenergy.pt"
PORTAL_ORIGIN: Final = "https://clientes.goldenergy.pt"
# The customer area's own "share your referral code" buttons link here, followed
# by the account's ``mgmVoucherCode``.
REFERRAL_LINK_BASE: Final = "https://amigo.goldenergy.pt/"

# The legacy login: same credentials as the web form, but no reCAPTCHA. The web
# form's own ``/rtoken`` validates a reCAPTCHA v3 token server-side, which a
# headless client cannot produce.
EP_LOGIN: Final = "/api/user-auth/token"
EP_ACCOUNTS: Final = "/api/billingAccounts/groupedservices-list"
EP_ACCOUNT: Final = "/api/billingAccounts/single"
EP_SERVICE: Final = "/api/services/single"
EP_READINGS: Final = "/api/readings/pagelist"
EP_INVOICES: Final = "/api/account-documents/calc-docs"
# Communicating a meter reading — the integration's only write.
EP_SUBMIT_READING: Final = "/api/readings/communication"
# ``errorCode`` values a reading submission answers with (verified live).
READING_ERROR_ABOVE_AVERAGE: Final = "1"
READING_ERROR_BELOW_LAST: Final = "2"

# Page size the front end itself uses for the paginated lists.
PAGE_SIZE: Final = 10
# Upper bound on pages walked per list and poll. Readings are roughly monthly and
# invoices monthly too, so 10 pages of 10 is several years of history while still
# bounding the request count should ``totalItems`` ever misreport.
MAX_PAGES: Final = 10

# --- Polling schedule ---
# Readings are monthly at best and invoices monthly, so there is nothing to gain
# from a tight loop: poll twice a day at fixed hours (02:00 / 14:00 local).
POLL_HOURS: Final = (2, 14)
POLL_MINUTE: Final = 0

# --- Entity unique ID suffixes ---
# Per-energy sensors are keyed ``<energy>_<suffix>``, e.g. ``gas_meter_index``.
SENSOR_METER_INDEX: Final = "meter_index"
SENSOR_LAST_CONSUMPTION: Final = "last_consumption"
SENSOR_LAST_READING_DATE: Final = "last_reading_date"
# Supply details, as diagnostic sensors.
SENSOR_DELIVERY_POINT: Final = "delivery_point"
SENSOR_METER_SERIAL: Final = "meter_serial"
SENSOR_TIER: Final = "tier"
SENSOR_CAMPAIGN: Final = "campaign"
# Gas only: the same figures in kWh, at the conversion factor.
SENSOR_METER_INDEX_ENERGY: Final = "meter_index_energy"
SENSOR_LAST_CONSUMPTION_ENERGY: Final = "last_consumption_energy"

# Account-level sensors.
SENSOR_NEXT_READING_DATE: Final = "next_reading_date"
SENSOR_BALANCE: Final = "balance"
SENSOR_LAST_INVOICE_TOTAL: Final = "last_invoice_total"
SENSOR_LAST_INVOICE_DUE: Final = "last_invoice_due"
SENSOR_AMOUNT_DUE: Final = "amount_due"
SENSOR_BILLED_12M: Final = "billed_12m"
SENSOR_CONTRACT_START: Final = "contract_start"
SENSOR_REFERRAL_CODE: Final = "referral_code"
SENSOR_REFERRAL_FRIENDS: Final = "referral_friends"

BINARY_SENSOR_AVAILABLE: Final = "available"
BINARY_SENSOR_INVOICE_PENDING: Final = "invoice_pending"
BINARY_SENSOR_ELECTRONIC_INVOICE: Final = "electronic_invoice"
BINARY_SENSOR_DIRECT_DEBIT: Final = "direct_debit"

# --- coordinator.data keys ---
DATA_AVAILABLE: Final = "available"
DATA_CONVERSION_FACTOR: Final = "conversion_factor"
# ``{"gas": {...}, "electricity": {...}}``, one normalised dict per enabled
# energy the account actually has (see the DATA_SERVICE_* keys below).
DATA_SERVICES: Final = "services"

DATA_ACCOUNT_STATUS: Final = "account_status"
DATA_NEXT_READING_DATE: Final = "next_reading_date"
DATA_BALANCE: Final = "balance"
DATA_DIRECT_DEBIT: Final = "direct_debit"
DATA_ELECTRONIC_INVOICE: Final = "electronic_invoice"
DATA_CONTRACT_START: Final = "contract_start"
# The member-get-member programme: the account's code, the shareable link built
# from it, and what it has earned so far.
DATA_REFERRAL_CODE: Final = "referral_code"
DATA_REFERRAL_LINK: Final = "referral_link"
DATA_REFERRAL_FRIENDS: Final = "referral_friends"
DATA_REFERRAL_EARNINGS: Final = "referral_earnings"

DATA_LAST_INVOICE_TOTAL: Final = "last_invoice_total"
DATA_LAST_INVOICE_DUE: Final = "last_invoice_due"
DATA_LAST_INVOICE_POSTED: Final = "last_invoice_posted"
DATA_LAST_INVOICE_PERIOD_START: Final = "last_invoice_period_start"
DATA_LAST_INVOICE_PERIOD_END: Final = "last_invoice_period_end"
DATA_LAST_INVOICE_NUMBER: Final = "last_invoice_number"
DATA_AMOUNT_DUE: Final = "amount_due"
DATA_INVOICE_PENDING: Final = "invoice_pending"
DATA_BILLED_12M: Final = "billed_12m"
# [{"iso": "2026-07-20", "total": 12.34}], oldest first: one entry per invoice,
# keyed by the end of the period it bills. Feeds the cost statistic.
DATA_INVOICE_SERIES: Final = "invoice_series"

# Keys inside each ``DATA_SERVICES[energy]`` dict.
DATA_SERVICE_NO: Final = "service_no"
DATA_SERVICE_STATUS: Final = "service_status"
DATA_DELIVERY_POINT: Final = "delivery_point"
DATA_TIER: Final = "tier"
DATA_METER_SERIAL: Final = "meter_serial"
DATA_METER_NUMBER: Final = "meter_number"
DATA_METER_DIGITS: Final = "meter_digits"
# The meter's register types (``[0]`` for gas), in the order a submitted
# reading must list its values.
DATA_METER_RECORD_TYPES: Final = "meter_record_types"
# Codes of the service's active campaigns, comma-joined (e.g. "DIGITAL_01/26").
DATA_CAMPAIGNS: Final = "campaigns"
DATA_SMART_METER: Final = "smart_meter"
# [{"iso": "2026-07-30", "index": 320.0}], oldest first. ``index`` is the
# cumulative meter reading: m³ for gas, kWh for electricity.
DATA_READINGS: Final = "readings"
DATA_METER_INDEX: Final = "meter_index"
DATA_METER_INDEX_ENERGY: Final = "meter_index_energy"
DATA_LAST_READING_ISO: Final = "last_reading_iso"
DATA_LAST_CONSUMPTION: Final = "last_consumption"
DATA_LAST_CONSUMPTION_ENERGY: Final = "last_consumption_energy"
DATA_LAST_CONSUMPTION_DAYS: Final = "last_consumption_days"
DATA_LAST_CONSUMPTION_FROM: Final = "last_consumption_from"

# --- Attributes ---
ATTR_BILLING_ACCOUNT: Final = "billing_account"
ATTR_SERVICE_NO: Final = "service_no"
ATTR_DELIVERY_POINT: Final = "delivery_point"
ATTR_METER_SERIAL: Final = "meter_serial"
ATTR_TIER: Final = "tier"
ATTR_READING_DATE: Final = "reading_date"
ATTR_PERIOD_START: Final = "period_start"
ATTR_PERIOD_END: Final = "period_end"
ATTR_DAYS: Final = "days"
ATTR_INVOICE_NUMBER: Final = "invoice_number"
ATTR_POSTING_DATE: Final = "posting_date"
ATTR_CONVERSION_FACTOR: Final = "conversion_factor"
ATTR_DIRECT_DEBIT: Final = "direct_debit"
ATTR_REFERRAL_LINK: Final = "referral_link"
ATTR_REFERRAL_EARNINGS: Final = "referral_earnings"
ATTR_METER_NUMBER: Final = "meter_number"
ATTR_METER_DIGITS: Final = "meter_digits"
ATTR_SMART_METER: Final = "smart_meter"
