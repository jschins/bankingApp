# psd2api

Download **raw transaction JSON** from ING (and other EU banks) via the
[Enable Banking API](https://enablebanking.com/docs/api/reference/) — a PSD2 /
Open Banking aggregator that is **free for personal use** and supports
[all major Dutch banks incl. ING](https://enablebanking.com/docs/markets/nl/).

It uses the bank's sanctioned PSD2 API. It does **not** scrape the ING website
and does **not** download official PDF statements (there is no API for those).

---

## Sequence of operations

```
SETUP (once)                CONSENT (repeat ~yearly)        FETCH (repeat any time)
─────────────               ───────────────────────         ──────────────────────
register Enable Banking  →  link    (POST /auth)        →   fetch  (GET /transactions)
create application           open URL + approve in ING       writes raw JSON to data/
download private key         app (SCA on your phone)
host privacy/terms pages     session (POST /sessions)
install + configure .env     (links account, opens a
                              time-limited session)
```

1. **`aspsps`** — list banks and confirm the ASPSP name (ING NL = `ING`). *(rarely needed)*
2. **`link`** — start authorization; prints a URL to open in the browser.
3. *(in browser)* approve access in your **ING Bankieren app** (SCA). You are
   redirected to `https://example.com/?code=...`.
4. **`session --code "<that URL>"`** — exchanges the code for a session and
   records your linked account(s).
5. **`fetch`** — downloads the raw transactions JSON for each linked account.

---

## Which operations are one-time vs repeated

| Operation | Frequency | Why |
| --- | --- | --- |
| Register Enable Banking account | **Once** | Account creation |
| Create application (get App ID + private key) | **Once** | The App ID + key are reused forever |
| Host Privacy / Terms pages | **Once** | Required only at application registration |
| Activate app by linking your account (Control Panel) | **Once** | Restricted-production activation |
| Install project + configure `.env` | **Once** (per machine) | Local setup |
| `aspsps` | **Rarely** | Only to look up a bank name |
| `link` → approve in ING app → `session` | **Repeatedly** — when consent expires (PSD2 caps consent validity; in practice up to ~90 days, sometimes longer) | A new bank consent + session is required |
| `fetch` | **Repeatedly** — as often as you like *while the consent is valid* | Pulls the latest data; no bank login needed |

**Rule of thumb:**
- **Set it up once** (account, application, key, `.env`).
- **Re-authorize occasionally** (`link` + `session`) — roughly once per consent
  period (up to ~yearly depending on what the bank grants); this is the only step
  that needs your phone / ING app.
- **Fetch as often as you want** in between, with no further bank login, until the
  consent expires (then you get a `401`/`403` and just re-run `link` + `session`).

---

## One-time setup

### 1. Register and create an Enable Banking application
At <https://enablebanking.com/> → sign up → **Control Panel → Applications →
Add new application**:
- **Environment**: `Production`
- **Key**: *Generate in the browser (SubtleCrypto) and export private key* →
  **download the `.pem`** (offered only once).
- **Redirect URL**: `https://example.com/` (production requires HTTPS).
- **Privacy / Terms URLs**: required for production — host two simple pages and
  paste their URLs (e.g. via GitHub raw/Pages).

Note the **Application ID** shown after creation.

### 2. Activate by linking your own account
On the Applications page choose **Activate by linking accounts** → **ING** /
**personal** → approve in your ING app. The app flips from *Inactive* to active
for your linked account.

### 3. Install and configure
```powershell
# from this folder (C:\coding\psd2-api)
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -e .          # requests, python-dotenv, PyJWT, cryptography

copy .env.example .env
```
Edit `.env`:
```dotenv
ENABLEBANKING_APP_ID=<your Application ID>
ENABLEBANKING_KEY_PATH=<path to your .pem>
PSD2_COUNTRY=NL
PSD2_ASPSP=ING
PSD2_REDIRECT_URL=https://example.com/   # must match the app's redirect URL exactly
```

---

## Repeated use

### Re-authorize (only when consent has expired)
```powershell
python -m psd2_api link --aspsp ING
# open the printed URL, approve in the ING app, copy the resulting
# https://example.com/?code=... URL from the address bar, then:
python -m psd2_api session --code "https://example.com/?code=...&state=..."
```

### Fetch data (any time the consent is valid)
```powershell
python -m psd2_api fetch --date-from 2025-06-25
```
> Pass `--date-from`! Without a date range ING returns only the most recent
> day or two. With a date range it returns history (a full year is typically fine).

Output (written to `data/`):
- `transactions_<accountUid>.json` — **raw** transaction objects, all pages merged
- `details_<accountUid>.json`, `balances_<accountUid>.json` — unless `--transactions-only`

---

## Automating downloads for several people

For multiple people, give each person their **own Enable Banking application**
(each authorizes their own ING account from their own phone), then describe them
once in **per-person profiles** so a single checkout can fetch for everyone — no
per-person working directories or `.env` files.

> Where applications are created: the Enable Banking **Control Panel** →
> **Applications** → **Add new application**:
> **<https://enablebanking.com/cp/applications>**

### Why one application per person
- Restricted-production access is granted per **linked account**, and linking
  requires that person's own Strong Customer Authentication (their ING app).
- Separate applications keep each person's **private key** and consent isolated.
- The **Privacy/Terms URLs can be the same** for every application.

### Per-person profiles
Each person gets `packaging/profiles/<person>/profile.json` (app id, bank,
redirect URL, key filename) plus their `.pem`. The shared bankingApp-server
destination (URL + API key) lives once in `packaging/server.json`. Full format
and the executable build live in [`packaging/README.md`](packaging/README.md).

### The family member re-authorizes (needs their phone)
The person runs their **executable** (`bankingApp-reauthorize-<person>.exe`), approves
in their bank app, and pastes back the redirect URL. This uploads a small
**consent record** to bankingApp-server. This step **cannot be automated** — it
requires their SCA approval. (Equivalent CLI: `python -m psd2_api consent`.)

### The administrator collects (automated, no human interaction)
With consents in place, one command fetches everyone and writes the raw files
into a target directory (bankingApp-admin's storage), reading each person's consent
from bankingApp-server:

```powershell
uv run python -m psd2_api collect --all --date-from <YYYY-MM-DD> --output-dir <dir>
```

People with no / expired consent are skipped and reported (re-run that person's
executable). In practice `bankingApp-admin`'s **Refresh data** button runs this for
you. The older manual path is `fetch --person <p>` (reads consent straight from
bankingApp-server) or `pull-consent --person <p>` then `fetch` (uses a local
`consent.json` copy).

> Note: serving many *unrelated* people as an ongoing service can exceed Enable
> Banking's free Restricted-Production scope. For yourself and a few consenting
> family members linking their own accounts, per-person applications are the
> practical pattern.

---

## Command reference

```powershell
python -m psd2_api aspsps [--country NL]
python -m psd2_api link  [--aspsp ING] [--country NL] [--redirect URL] [--valid-days 90]
python -m psd2_api session --code "<code or full redirect URL>"
python -m psd2_api status
python -m psd2_api fetch [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD]
                         [--transactions-only] [--output-dir DIR] [--person js]

# Family-member executable + admin collection (see "Distributable executable")
python -m psd2_api consent [--valid-days 90]     # guided re-auth -> consent record
python -m psd2_api pull-consent [--person js]    # admin: server consent -> local consent.json
python -m psd2_api collect (--all | --person js) # admin: fetch everyone into a dir
           [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] [--output-dir DIR] [--json]
python -m psd2_api server-url [--port 8000]      # print this host's bankingApp-server URL
```

---

## Can the consent / SCA be automated?

**No — the actual SCA approval cannot be fully automated.** It deliberately
requires *you* (the human) to authenticate with your bank, normally via your
banking app (ING Bankieren app on mobile, QR on desktop). But everything
*around* it can be automated, so in practice you only touch your phone roughly
once per consent period.

**Why SCA can't be headless.** Strong Customer Authentication is a regulatory
requirement (PSD2 RTS). It mandates two of three independent factors —
possession (your phone / card reader), knowledge (PIN/password), inherence
(fingerprint/face). The whole point is that a third party (this script) *cannot*
complete it for you. There is no API where you pass credentials and receive a
consent without the bank's own authentication step.

**What is automated:**
- Building the authorization URL (`link`) and exchanging the code (`session`).
- All data fetching (`fetch`) for the lifetime of the session.

**Session lifetime (the key point).** Once an account is authorized you get a
session valid for up to ~90 days (each account session has a `valid_until`).
While it is valid you can `fetch` as often as you want with **no new SCA**, so a
scheduled/cron job runs unattended. When it expires (or you request data older
than the bank allows) you must re-run `link` + `session`, and *that* step needs
your phone again.

**Decoupled vs redirect.** Some banks support "decoupled" SCA (approve a push in
the bank app, no browser); others use the redirect flow (browser → bank login →
app/QR approval). Either way a human action on your authenticated device is
required; only the trigger and aftermath are scriptable.

**Development.** Enable Banking's sandbox/test ASPSPs provide dummy SCA you can
complete without a real phone — useful for testing the full flow before hitting
the live ING connection.

Realistic setup: a scheduled job fetches automatically (e.g. daily); about once
every ~90 days it fails with an expired-consent error, you re-authorize via your
phone once, and automation resumes.

---

## Distributable executable for family members

For relatives who just need to (re-)grant access without touching a terminal,
build them a **single `.exe`** with their credentials baked in. Double-clicking it:

1. opens their bank approval page (SCA in their banking app),
2. asks them to paste back the redirect URL, and
3. reports a small **consent record** to bankingApp-server — *not* their transactions.

The administrator then fetches the data themselves, because each
re-authorization mints **new** account `uid`s that only exist on the family
member's machine (the cross-session-stable identifier is `identification_hash`).
Normally this is the `bankingApp-admin` **Refresh data** button, which runs:

```powershell
# fetch everyone with valid consent into bankingApp-admin's storage
uv run python -m psd2_api collect --all --date-from <YYYY-MM-DD> --output-dir <dir>
```

Or, for a single person manually:

```powershell
uv run python -m psd2_api fetch --person js --date-from <YYYY-MM-DD>
```

(`fetch --person` reads consent from bankingApp-server. Use `pull-consent` first only
if you want a local `consent.json` copy, then run `fetch` without `--person`.)

Build instructions and the per-person `profile.json` format live in
[`packaging/README.md`](packaging/README.md).

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'jwt'`
You ran the tool with the wrong Python — the virtualenv wasn't active, so the
dependencies (incl. **PyJWT**, imported as `jwt`) weren't on the path. Activate
the venv first:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned   # allow Activate.ps1 in this shell
.\.venv\Scripts\Activate.ps1                                       # prompt should show (psd2-api)
```
`Set-ExecutionPolicy` is only so PowerShell may run the activation script in the
current session (the default policy blocks unsigned scripts). Note: the package
is **PyJWT** (`pip install PyJWT`), *not* the unrelated `jwt` package — `import
jwt` is correct, but install PyJWT.

### `422 ... WRONG_TRANSACTIONS_PERIOD` ("more than 90 days in the past")
This is not a bug — the bank/aggregator rejects the request by policy: PSD2 only
lets you read transactions from roughly the **last 90 days** under an existing
consent. A `422` means the request was authenticated and well-formed, but the
parameters violate that rule. If your `--date-from` is older than ~90 days ago,
you get this error.

Fixes:
- Use a `--date-from` within the last 90 days (e.g. today − 90 days).
- For older history you must re-authorize with a fresh consent/SCA (`link` +
  `session`); even then most banks only expose ~90 days per consent.

---

## Notes
- `.env`, `*.pem`/`*.key`, `consent.json`, and `data/` are git-ignored.
- The redirect URL in `.env` must exactly match one registered on the application.
- `fetch` overwrites the per-account files each run; use `--output-dir` to keep
  separate pulls.
- A `401`/`403` on `fetch` usually means the consent expired → re-run `link` +
  `session`.
- ING NL uses the ING Bankieren app for SCA (app switch on mobile, QR on desktop).
