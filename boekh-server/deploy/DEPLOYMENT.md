# Deployment — running this on a laptop/server

This guide turns a laptop (or any spare machine) into an always-on server for the
minimal JSON receive/transmit service described in the project
[`README.md`](../README.md).

It covers three ways to run it, from simplest to most production-like:

1. **Windows, as a background service** (recommended for a Windows laptop)
2. **Docker / Docker Compose** (portable, works on Windows or Linux)
3. **Linux, with `systemd`** (most "server-like")

> **Prerequisite — the app entrypoint.**
> Every option below starts the app via `uvicorn app.main:app`. That means a
> Python module `app/main.py` must expose a FastAPI instance named `app`:
>
> ```python
> # app/main.py
> from fastapi import FastAPI
> app = FastAPI()
> ```
>
> If your entrypoint differs, change `app.main:app` accordingly in the scripts
> and `.env`.

---

## 0. One-time machine prep (Windows laptop)

A laptop sleeps when idle or when the lid closes — fatal for a server. Run the
prep script **once** from an **elevated** PowerShell (Run as Administrator):

```powershell
cd deploy
./setup-server.ps1
```

It will:

- Disable sleep / hibernate while on AC power
- Set "closing the lid" to **do nothing** (on AC power)
- Open the app's inbound port in Windows Defender Firewall (default 8000)

Then **set a static LAN IP** so clients can reliably find the machine. Easiest:
reserve the laptop's IP in your router's DHCP settings (look for "DHCP
reservation" / "static lease" and bind it to the laptop's MAC address).

Find your current address with:

```powershell
ipconfig
```

---

## 1. Windows — run as a background service (NSSM)

This auto-starts the app on boot and restarts it if it crashes, with no terminal
window kept open.

### 1a. Install dependencies

```powershell
# from the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### 1b. Configure

```powershell
cd deploy
copy .env.example .env
# edit .env: set HOST, PORT, APP_MODULE, STORAGE_DIR, API_KEY
```

### 1c. Install NSSM (the service wrapper)

Download from <https://nssm.cc/download>, unzip, and put `nssm.exe` on your PATH
(or pass its path with `-NssmPath` below). With Chocolatey:

```powershell
choco install nssm
```

### 1d. Install & start the service

Run from an **elevated** PowerShell:

```powershell
cd deploy
./install-service.ps1            # uses values from .env
# ./install-service.ps1 -Port 9000 -ServiceName bankingAppd-storage   # overrides
```

Manage it later:

```powershell
nssm status bankingApp-server
nssm restart bankingApp-server
nssm stop bankingApp-server
./uninstall-service.ps1          # removes the service
```

Logs are written to `deploy\logs\` (stdout + stderr).

---

## 2. Docker / Docker Compose

Portable and isolated; great if you later move off the laptop.

```powershell
cd deploy
copy .env.example .env           # edit as needed
docker compose up -d --build
docker compose logs -f
docker compose down
```

`restart: unless-stopped` in `docker-compose.yml` keeps it running across reboots
(as long as Docker starts on boot).

---

## 3. Linux — systemd

On Ubuntu/Debian:

```bash
# install
sudo cp deploy/bankingApp-server.service /etc/systemd/system/
sudo nano /etc/systemd/system/bankingApp-server.service   # fix paths/user
sudo systemctl daemon-reload
sudo systemctl enable --now bankingApp-server

# manage
systemctl status bankingApp-server
journalctl -u bankingApp-server -f
```

---

## 4. Verify it works

From the server itself:

```powershell
curl http://127.0.0.1:8000/
```

From another device on the LAN (replace with the static IP):

```
http://192.168.1.50:8000/
```

---

## 5. Exposing it to the internet (optional)

Only needed if devices **outside** your network must reach it. This server is
guarded only by a single shared API key sent over (by default) plain HTTP, so
prefer a private tunnel/VPN over a public port-forward, and add TLS.

**Recommended: a tunnel (no router changes, no exposed home IP, free TLS):**

- **Cloudflare Tunnel** — `cloudflared tunnel ...`
- **Tailscale** — private mesh VPN; reach the laptop by its Tailscale name

**Alternative: router port-forward + reverse proxy** for HTTPS (Caddy gives you
automatic certificates). Avoid serving plain HTTP across the internet.

---

## 6. Security checklist

- [ ] HTTPS/TLS in front of the app (tunnel or reverse proxy) for any public access
- [ ] Keep it on a trusted LAN/VPN — access is gated only by one shared API key
- [ ] Strong `API_KEY` set in `.env` (never commit `.env`)
- [ ] Only the app port is open in the firewall
- [ ] OS kept patched; service runs without admin rights where possible
- [ ] Regular backups of the JSON storage directory
