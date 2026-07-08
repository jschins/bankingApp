# Single-person executable workflow

This folder is a **standalone, one-person variant** of the bank connector in
[`psd2-api`](../psd2-api/README.md). It packages the same Enable Banking /
PSD2 flow into a single executable that one person runs on their own machine:
authorize once in their banking app, then download and categorize transactions
locally — no `bankingApp-server`, no admin `collect` step.

For the multi-person household setup (family executables that upload consent to
`bankingApp-server`, admin refresh in `bankingApp-admin`), see
[`psd2-api/packaging/README.md`](../psd2-api/packaging/README.md).

## What it does

- loads a person-specific configuration JSON
- authenticates to the bank via the [Enable Banking API](https://enablebanking.com/docs/api/reference/) (PSD2 / Open Banking)
- downloads transactions for linked account(s)
- applies keyword-based categorization rules
- writes categorized + raw JSON output files

## Banking credential flow

Access to your bank account is regulated under PSD2. A human must approve access
in their banking app (Strong Customer Authentication, SCA) roughly once per
consent period (~90 days). Everything around that step is automated.

Three local files carry the credentials and the resulting bank session. They
live together in this folder and are git-ignored.

### Overview

```
ONE-TIME SETUP                         RE-AUTHORIZE (~every 90 days)              FETCH (any time while valid)
────────────────                       ─────────────────────────────              ────────────────────────────
Enable Banking Control Panel      →    workflow prints a bank URL          →    workflow reads consent.json
  • create application                   you approve in your banking app          fetches transactions
  • download .pem (private key)          paste redirect URL with --redirect-code  writes output/*.json
  • note Application ID                  writes consent.json
write profile.json
place <app-id>.pem beside it (application id = PEM filename)
```

### 1. The `.pem` file (Enable Banking private key)

When you create an Enable Banking **application** in the
[Control Panel](https://enablebanking.com/cp/applications), you generate an RSA
key pair in the browser and **download the private key once** as a `.pem` file.
Enable Banking never offers that download again — keep a safe backup.

The file is typically named after the Application ID, e.g.
`317e65d7-9fdb-48d3-862b-58f0357bf152.pem`.

**What it is used for:** every API call to Enable Banking is authenticated with
a short-lived JWT signed **RS256** using this private key. The Application ID is
sent as the JWT `kid` header. The workflow never uploads the key anywhere; it
stays on disk next to `profile.json`.

**Security:** treat the `.pem` like a password. Do not commit it, email it, or
bake it into a shared repo. If it leaks, revoke the application in the Control
Panel and create a new one.

### 2. `profile.json` (static application identity)

`profile.json` tells the workflow *which* Enable Banking application and bank to
use. It is set up once and changes only if you create a new application or
switch banks.

Example (see also [`profile.json`](profile.json) in this folder):

```json
{
  "person": "js",
  "app_id": "317e65d7-9fdb-48d3-862b-58f0357bf152",
  "key_file": "317e65d7-9fdb-48d3-862b-58f0357bf152.pem",
  "country": "NL",
  "aspsp": "ING",
  "redirect_url": "https://deoudegracht.nl/"
}
```

| field | meaning |
| --- | --- |
| `person` | short id for this person (used in output metadata and `consent.json`) |
| `app_id` | Enable Banking **Application ID** from the Control Panel |
| `key_file` | filename of the `.pem` in this same folder (not a full path) |
| `country` | ISO country code for the bank market (e.g. `NL`) |
| `aspsp` | bank identifier as Enable Banking names it (ING NL = `ING`) |
| `redirect_url` | must **exactly** match the redirect URL registered on the application |

`workflow.py` reads `profile.json` at startup and merges these values into the
`bank` section of your config. Values in `config.json` / `config.example.json`
can still override individual fields, but in practice `profile.json` is the
canonical place for credentials.

**One-time Enable Banking setup** (before writing `profile.json`):

1. Register at [enablebanking.com](https://enablebanking.com/).
2. Control Panel → Applications → *Add new application*:
   - Environment: `Production`
   - Generate the key in the browser → download the `.pem`
   - Redirect URL: your registered HTTPS URL (must match `redirect_url` above)
   - Privacy / Terms URLs: required for production
3. Note the **Application ID**.
4. **Activate** the app by linking your own account (Control Panel → *Activate
   by linking accounts* → your bank → approve in your banking app).

Drop the `.pem` and `profile.json` into this folder. That completes the static
setup.

### 3. `consent.json` (the bank session handle)

After you approve access in your banking app, Enable Banking returns a
**session** that links your account(s) for a limited time. The workflow saves
the essential parts locally as `consent.json`.

Example (see also [`consent.json`](consent.json)):

```json
{
  "person": "js",
  "aspsp": "ING",
  "country": "NL",
  "session_id": "c7477297-15a0-4351-b315-fe9e5b471d08",
  "valid_until": "2026-09-28T10:45:51.364884+00:00",
  "created_at": "2026-06-30T10:46:48.955972+00:00",
  "accounts": [
    {
      "uid": "d55727d9-00f5-4628-93a6-c56bd9d4844c",
      "iban": "NL52INGB0668979275",
      "identification_hash": "WwpbCiJhY2NvdW50IiwK...",
      "name": "Hr dr JM Schins",
      "currency": "EUR"
    }
  ]
}
```

| field | meaning |
| --- | --- |
| `session_id` | Enable Banking session id — required to call the API on your behalf |
| `valid_until` | when this consent expires (PSD2 caps this; typically up to ~90 days) |
| `created_at` | when the consent record was written |
| `accounts[].uid` | per-session account identifier used in transaction API URLs |
| `accounts[].iban` | the linked IBAN (human-readable) |
| `accounts[].identification_hash` | stable cross-session identifier for the same physical account |
| `accounts[].name` / `currency` | account label and currency from the bank |

**Important:** each re-authorization mints **new** `uid` values. The
`identification_hash` is the stable handle across sessions; the `uid` is what
you must use for API calls until consent expires.

**What it does *not* contain:** transactions, passwords, or the private key. It
is only enough for the workflow (running on the same machine with the same
`.pem`) to fetch data.

In the full **bankingApp** household flow, an equivalent record is uploaded to
`bankingApp-server` so an administrator can `collect` transactions. Here it stays
**local** in this folder — this project is self-contained.

### Step-by-step: first run and re-authorization

**First run** (no `consent.json` yet):

```powershell
uv run --project single-person python single-person/workflow.py --config single-person/config.example.json
```

The script calls Enable Banking `POST /auth`, prints a URL, and stops. Open the
URL, log in, and approve in your banking app. The browser lands on your redirect
URL with `?code=...`. Re-run with that code:

```powershell
uv run --project single-person python single-person/workflow.py `
  --config single-person/config.example.json `
  --redirect-code "https://deoudegracht.nl/?code=...&state=..." `
  --date-from 2026-03-01
```

This exchanges the code for a session (`POST /sessions`), writes
`consent.json`, then fetches transactions for every linked account and writes
the output files.

**Routine fetch** (consent still valid):

```powershell
uv run --project single-person python single-person/workflow.py `
  --config single-person/config.example.json `
  --date-from 2026-03-01
```

No bank login needed — the workflow reads `consent.json`, signs requests with
the `.pem`, and calls `GET /accounts/{uid}/transactions`.

**When consent expires** (`401`/`403` from the API, or `valid_until` in the
past): delete or ignore the old `consent.json`, run without `--redirect-code`
to get a fresh URL, approve again, and pass the new `--redirect-code`.

### How the pieces fit together at runtime

```
profile.json + <app-id>.pem
        │
        ▼
EnableBankingClient  ── signs JWT (RS256) ──▶  Enable Banking API
        │
        ├─ no consent yet ──▶ POST /auth ──▶ print URL ──▶ (human SCA)
        │                              │
        │                              ▼
        │                    POST /sessions?code=...
        │                              │
        │                              ▼
        └─ consent.json ◀────  session_id + accounts[]
                 │
                 ▼
        GET /accounts/{uid}/transactions  ──▶  output/*.json
```

Code paths live in [`workflow.py`](workflow.py) (`download_transactions`) and
[`client.py`](client.py) (`EnableBankingClient`).

## Run locally

```powershell
uv run --project single-person python single-person/workflow.py --config single-person/config.example.json --date-from 2026-03-01
```

Optional flags:

| flag | purpose |
| --- | --- |
| `--redirect-code` | full redirect URL or `code` value after bank approval |
| `--date-from` / `--date-to` | ISO dates (`YYYY-MM-DD`); pass `--date-from` — without a range most banks return only the last day or two |
| `--output` | override the categorized output file path |

## Build an executable

```powershell
powershell -ExecutionPolicy Bypass -File .\single-person\build_exe.ps1
```

The build bundles `config.example.json` and [`entry.py`](entry.py) via
PyInstaller. For a real deployment, place `profile.json`, the `.pem`, and
(optionally) an existing `consent.json` next to the `.exe`, or bake them in by
extending `build_exe.ps1` with `--add-data` lines (same pattern as
[`psd2-api/packaging/build_exe.ps1`](../psd2-api/packaging/build_exe.ps1)).

## Config format

[`config.example.json`](config.example.json) holds categorization rules and
output paths. The `bank` block can duplicate profile fields but is normally
left to defaults merged from `profile.json`:

```json
{
  "person": "demo-person",
  "bank": {
    "provider": "enable_banking",
    "app_id": "...",
    "key_path": "...",
    "aspsp": "ING",
    "country": "NL",
    "redirect_url": "https://deoudegracht.nl/"
  },
  "categories": [ ... ],
  "output": {
    "dir": "output",
    "file": "categorized_transactions.json",
    "raw_file": "downloaded_transactions.json"
  }
}
```

## Files to keep secret

These are git-ignored and must not be committed:

| file | contains |
| --- | --- |
| `*.pem` | Enable Banking private signing key |
| `profile.json` | application id + bank settings |
| `consent.json` | live bank session + account uids |
| `output/*.json` | downloaded transaction data |

Only `config.example.json` (no secrets) is safe to commit as a template.

## Troubleshooting

- **`Private key file not found`** — `key_file` in `profile.json` must name a
  `.pem` that sits in this folder.
- **`Redirect code is required to continue`** — run again with `--redirect-code`
  after approving in your banking app.
- **`401` / `403` on fetch** — consent expired; re-authorize and refresh
  `consent.json`.
- **`422 WRONG_TRANSACTIONS_PERIOD`** — `--date-from` is older than ~90 days;
  PSD2 only exposes recent history per consent. Use a date within the last 90
  days, or re-authorize for a fresh window.

See [`psd2-api/README.md`](../psd2-api/README.md) for more on Enable Banking
setup, SCA limitations, and API behaviour.
