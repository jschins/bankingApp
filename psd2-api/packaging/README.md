# Family-member executable (re-authorization)

This builds a **single `.exe` per family member**. When they double-click it, it:

1. Opens their bank's login/approval page (SCA in their banking app).
2. Asks them to paste back the redirect URL.
3. Creates an Enable Banking **session** and reports a tiny **consent record**
   (session id + account `uid`s + `identification_hash` + `valid_until`) to
   bankingApp-server.

It does **not** download or upload any transactions. The administrator pulls the
consent record and fetches the data themselves (see below). This is necessary
because each re-authorization mints **new** account `uid`s that only exist on the
family member's machine.

## Adding a new person (short)

End-to-end recipe to onboard a new family member. Replace `short` with their id
(e.g. `eg`); use the **same `short` everywhere** (folder name, `profile.json`'s
`person` field, the bankingApp-admin storage folder).

Prerequisite (once): `packaging/server.json` is filled in (see below).

1. **Create their Enable Banking application.** At
   <https://enablebanking.com/cp/applications> → *Add new application*:
   - Environment `Production`; generate the key in the browser and **download the
     `.pem`** (offered only once); Redirect URL `https://example.com/`; reuse your
     shared Privacy/Terms URLs.
   - Note the **Application ID**.
   - **Activate by linking accounts** → ING / personal → *short* approves in
     their banking app (their SCA — this is when their phone/presence is needed).

2. **Create their profile folder** `packaging/profiles/short/` and put their
   `.pem` in it.

3. **Write** `packaging/profiles/short/profile.json` (copy
   `profile.json.example`). `person` must equal `short`; `key_file` must equal the
   `.pem`'s filename:

   ```json
   {
     "person": "short",
     "app_id": "<Application ID>",
     "key_file": "<Application ID>.pem",
     "country": "NL",
     "aspsp": "ING",
     "redirect_url": "https://example.com/"
   }
   ```

4. **Build their executable** (from the `psd2-api` folder):

   ```powershell
   .\packaging\build_exe.ps1 -ProfileDir .\packaging\profiles\short
   # -> dist\bankingApp-reauthorize-short.exe
   ```

5. **Copy** `dist\bankingApp-reauthorize-short.exe` to *short*'s own laptop.

6. **Have *short* run it** on their laptop: log in + approve in their bank app,
   paste back the redirect URL. A `consent.json` for `short` lands on bankingApp-server.

That's it — **no further admin setup is required.** The next **Refresh data** in
bankingApp-admin runs `collect --all`, which now includes `short` (it enumerates
`packaging/profiles/`), fetches their transactions, and bankingApp-admin
auto-creates `storage/short/` with their distilled `short_transactions.json`. The
general categories apply automatically; a per-person `short_categories.json` is
optional.

> The build machine needs the `psd2-api` project set up (`uv`), and steps 1 and 6
> each require *short*'s SCA. Everything in between is just files on your machine.

## Central server settings (set once)

The bankingApp-server destination is shared by everyone and lives in a single file,
`packaging/server.json` (copy `server.json.example`):

```json
{
  "server_url": "http://bankingApp-SERVER-HOSTNAME:8000",
  "server_api_passphrase": "bankingAppouding dkg"
}
```

The `server_api_passphrase` is automatically hashed (SHA-256) when computing the
Bearer token.

Use the server machine's **hostname** (not its IP) so a DHCP-reassigned address
doesn't break already-built executables. Print the right value by running, on the
server box:

```powershell
uv run python -m psd2_api server-url     # e.g. http://DESKTOP-SB23T6S:8000
```

`server.json` is merged into every person's profile at build time. Change the
server address here once and rebuild each `.exe`; you never edit it per person.
This file holds the shared passphrase and is git-ignored. (Per family = its own
`server.json`.)

## One profile per person

Create a folder per person containing a `profile.json` (copy
`profile.json.example`) and that person's Enable Banking private key:

```
packaging/profiles/js/
  profile.json        # person id, app id, bank, redirect url, key_file name
  <app-id>.pem        # that person's Enable Banking private key
```

`profile.json` fields (person-specific only; server settings come from
`server.json`):

| field          | meaning                                              |
| -------------- | ---------------------------------------------------- |
| `person`       | server folder / short id (e.g. `js`, `as`)           |
| `app_id`       | Enable Banking Application ID                        |
| `key_file`     | filename of the `.pem` in this same folder           |
| `country`      | ISO country (e.g. `NL`)                              |
| `aspsp`        | bank name (e.g. `ING`)                               |
| `redirect_url` | must match the application's registered redirect URL |

> Profiles hold secrets and are git-ignored (`packaging/profiles/`, `*.pem`,
> `*.json`, `packaging/server.json`). Only the `*.example` files are committed.

## Build

```powershell
# from the psd2-api folder
.\packaging\build_exe.ps1 -ProfileDir .\packaging\profiles\js
# -> dist\bankingApp-reauthorize-js.exe
```

Hand the resulting `dist\bankingApp-reauthorize-<person>.exe` to that person. It is
self-contained (their credentials + server destination are baked in).

## Administrator: collect after someone re-authorizes

Normally `bankingApp-admin`'s **Refresh data** button does this. Under the hood it
runs `collect`, which reads each person's consent from bankingApp-server, fetches with
that person's credentials (from `packaging/profiles/<person>`), and writes the
raw files into the given directory — for everyone at once:

```powershell
# from the psd2-api folder
uv run python -m psd2_api collect --all --date-from <YYYY-MM-DD> --output-dir <dir>
```

People with no / expired consent are skipped and reported. For a single person
manually (`--person` selects credentials straight from the profile — no `.env`
editing):

```powershell
uv run python -m psd2_api fetch --person js --date-from <YYYY-MM-DD>
```

`fetch --person` reads consent from bankingApp-server directly. Use `pull-consent`
first only if you want a local `consent.json` copy and then run `fetch` without
`--person`.
