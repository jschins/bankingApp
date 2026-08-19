# Public HTTPS upload via Tailscale Funnel

The hub serves `/upload` on plain HTTP `:8200`. Tailscale Funnel exposes **only** that path to the public internet over HTTPS — no router changes, no port forwarding, automatic TLS. The rest of the hub stays LAN/Tailscale-only.

```text
Browser  --HTTPS-->  <machine>.<tailnet>.ts.net/upload
                         |  Tailscale Funnel (only /upload/…)
                         v
                    hub :8200 (HTTP)
```

## Setup

On the hub PC (Tailscale must be installed):

```powershell
tailscale funnel --bg --set-path=/upload http://127.0.0.1:8200/upload
```

The `/upload` in the target URL is required — Funnel strips the mount-point prefix before forwarding, so without it the hub would see `GET /` instead of `GET /upload`.

Check config: `tailscale funnel status`
Stop: `tailscale funnel --https=443 off`

First-time use will prompt you to enable Funnel in the Tailscale admin console.

## The upload link

```
https://<machine>.<tailnet>.ts.net/upload?t=<token>
```

Replace `<token>` with the grant token from `workspaces/upload_acl.json`. The token is embedded in the URL; the upload page reads it from `?t=` automatically and remembers it in `localStorage` — after the first visit with `?t=`, the base `/upload` URL works without the token parameter.

## What Funnel exposes

Only three routes, all under `/upload`:


| Route                          | Purpose                 |
| ------------------------------ | ----------------------- |
| `GET /upload`                  | Upload page (HTML)      |
| `GET /upload/api/upload/grant` | Grant check + file list |
| `POST /upload/api/upload`      | File upload             |


The hub's IP gate (`hub_ips`) blocks everything else for unknown IPs. Funnel's IP is not in `hub_ips`, so the root page, admin UI, add-person wizard, matrix, and secrets are **not** reachable through the public URL.

## Upload page

The page has three fields after the grant loads:


| Field  | Default      | Meaning                                                                                       |
| ------ | ------------ | --------------------------------------------------------------------------------------------- |
| Year   | Current year | Target year folder                                                                            |
| Format | Excel        | `Excel` = balance check + import + categorization. `Test` = save file only, no processing. `Dry run` = run balance check and report result, nothing saved |
| File   | —            | `.xlsx` to upload                                                                             |




## Multiple machines

You can run the Funnel command on different machines (dev laptop, server laptop). Each gets its own URL:

```
https://desktop-sb23t6s.tail59b1e5.ts.net/upload?t=...  (development)
https://desktop-8bimc9l.tail59b1e5.ts.net/upload?t=...  (server)
```

They are independent. The hub must be running on `:8200` on whichever machine is funneling.

## Funnel auto-start on reboot

Funnel must be re-enabled after a reboot. Create a Windows Task Scheduler entry on the hub PC:

- **Trigger:** At log on (or At startup)
- **Action:** `tailscale funnel --bg --set-path=/upload http://127.0.0.1:8200/upload`
- **Run whether user is logged on or not** (optional)

Alternatively, add the command to a startup script or shortcut in the Startup folder.

## Client IPs

Tailscale Funnel does not currently forward `X-Forwarded-For` or similar headers. The hub reads proxy headers (`X-Forwarded-For`, `CF-Connecting-IP`, `X-Real-IP`) for upload endpoints, but with Funnel, `upload.log` records the Funnel proxy IP, not the real person IP. This is a Tailscale limitation — no action needed now.

## Year-skip behavior

If someone uploads to 2028 without ever using 2027, `ensure_year_folder` seeds 2028 from the latest existing year (e.g. 2026). This is by design — it uses the latest year folder strictly less than the target, not blindly target minus one.

## What stays off

- The hub does **not** serve HTTPS itself — TLS terminates at Tailscale.
- `hub_ips` stays LAN/Tailscale IPs only. Do **not** add Funnel's proxy IP.
- `deoudegracht.nl` is a static site (bank callback uses a client-side redirect); it cannot reverse-proxy POST requests.
- Upload files are capped at 32 MiB by the hub.

