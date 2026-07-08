# Connectivity troubleshooting — final record

## Goal
Get the **client laptop** (running the PyInstaller-packaged `bankingApp-editor` app) to
upload files to the **server laptop** (running the `bankingApp-server` FastAPI server).
In the app, pressing **"e"** exports and uploads.

## Final outcome (TL;DR)
Two stacked problems were found. The **first was fixed**; the **second is a
network-level block that cannot be fixed on the laptops**:

1. **[FIXED] Hyper-V External virtual switch bound to the client's Wi-Fi NIC** —
   broke host-to-LAN connectivity. Resolved by removing the switch.
2. **[BLOCKING] Wi-Fi "client isolation" on the (hotel) `Zonnewende` network** —
   the access point allows internet access but blocks device-to-device traffic.
   The two laptops literally cannot talk to each other on this Wi-Fi.

Because this is a **hotel Wi-Fi**, client isolation is expected and the router
admin is not accessible. Use a **phone hotspot** or a **wired Ethernet**
connection instead (see "Resolution" below).

---

## Addresses / setup at time of diagnosis
- **Server laptop** (FastAPI/uvicorn): SSID `Zonnewende`, IPv4 `192.168.7.75/24`,
  gateway `192.168.7.1`, Wi-Fi adapter, AP BSSID `b8:ec:a3:1d:51:fa`. API port `8000`.
- **Client laptop**: SSID `Zonnewende`, IPv4 `192.168.7.172/24`, gateway
  `192.168.7.1`, AP BSSID `b8:ec:a3:1d:51:fa` (SAME access point as the server).
- The app reads the server URL from `bank_categories.json` -> `server.url`, which
  must be `http://<server-ip>:8000` (NOT `localhost`).

---

## SERVER side — CONFIRMED WORKING (do not re-debug)
1. uvicorn listening on **`0.0.0.0:8000`** (verified via
   `Get-NetTCPConnection -LocalPort 8000 -State Listen` -> `LocalAddress 0.0.0.0`).
   NOTE: must be started with `--host 0.0.0.0`; the default `127.0.0.1` is
   localhost-only. With `--reload`, editing a file does NOT rebind the host — you
   must fully stop (Ctrl+C) and relaunch.
2. Windows Firewall inbound rule for TCP 8000 created (`New-NetFirewallRule ...
   -LocalPort 8000 ... -Profile Any`). NOTE: this opens TCP 8000 only, NOT ICMP,
   so `ping <server>` can fail even when the API is reachable — use
   `Test-NetConnection <server> -Port 8000` as the real test.
3. Server confirmed on SSID `Zonnewende`, IPv4 `192.168.7.75`.

---

## PROBLEM 1 — Hyper-V External switch over Wi-Fi  [RESOLVED]

### Symptom
Client's physical Wi-Fi (WLAN) was not connected directly; only a
`Hyper-V Extern` (External vSwitch) adapter and `vEthernet (Default Switch)` were
active. `ping <server>` returned `Destination host unreachable` sourced from the
client's own Hyper-V Extern address.

### Root cause
Hyper-V **External** virtual switches do not work correctly over Wi-Fi (a wireless
NIC can associate with only one MAC at the AP, so bridging/MAC-translation fails
for host-to-peer traffic).

### Fix that was applied (elevated PowerShell, on the client)
The switch (exact name `HyperV Extern`, no hyphen) was held by a VM
(`ubuntu_sv_spot`). Steps used:
```powershell
Get-VMSwitch | Format-Table Name, SwitchType
Get-VMNetworkAdapter -All | Format-Table VMName, Name, SwitchName, IsManagementOs
Connect-VMNetworkAdapter -VMName "ubuntu_sv_spot" -SwitchName "Default Switch"
Remove-VMSwitch -Name "HyperV Extern" -Force
```
Then a **reboot** of the client. After reboot the physical `WLAN` adapter
(Intel Wi-Fi 6 AX201) connected natively to `Zonnewende` and received a real DHCP
lease (`192.168.7.172`, `PrefixOrigin : Dhcp`), gateway `192.168.7.1`.

VM note: `ubuntu_sv_spot` is now on the NAT-based `Default Switch` (internet, no
LAN IP). Recreate an Internal/NAT switch for it later if needed — do NOT recreate
an External switch on Wi-Fi.

---

## PROBLEM 2 — Wi-Fi client isolation on the hotel network  [BLOCKING]

### Evidence (conclusive)
- Both laptops connected to the SAME access point: identical
  `AP BSSID b8:ec:a3:1d:51:fa`, same SSID `Zonnewende`, same subnet/gateway.
- From the client: `ping 8.8.8.8` **succeeds** (internet works), but
  `Test-NetConnection 192.168.7.75 -Port 8000` **fails**, and `ping 192.168.7.75`
  returns `Destination host unreachable` (ARP for the peer gets no reply).

### Root cause
The access point has **client/station isolation** enabled: each device can reach
the internet/gateway but is blocked from communicating with other devices on the
same Wi-Fi. This is standard on **hotel / guest / public** Wi-Fi. It is NOT a
laptop or application problem, and it cannot be changed without router admin
access (not available on hotel Wi-Fi).

---

## RESOLUTION (what to actually do)
Pick one. Options 1 and 2 work in a hotel; option 3 needs router admin (not
available here).

1. **Phone hotspot (recommended in a hotel).**
   - Enable a phone's hotspot; connect BOTH laptops to it.
   - On the server: `ipconfig` -> note its new hotspot IPv4 (e.g. `192.168.x.x`).
   - Keep uvicorn running with `--host 0.0.0.0 --port 8000`.
   - Set `bank_categories.json` -> `server.url` to `http://<new-server-ip>:8000`.
   - Verify from client: `Test-NetConnection <new-server-ip> -Port 8000`
     (`TcpTestSucceeded : True`), then upload.
   - Most phone hotspots allow client-to-client traffic.

2. **Wired Ethernet.** Connect both laptops (or at least the server) to a wired
   switch/router that does not isolate ports. Use the wired IP.

3. **Disable AP/client isolation on the router** (only if you control it — e.g. at
   home, not at the hotel): router admin -> Wireless -> turn off
   "AP Isolation" / "Client Isolation" / "Guest mode".

### Verify success (from the client)
```powershell
Test-NetConnection <server-ip> -Port 8000   # TcpTestSucceeded : True
```

---

## AFTER CONNECTIVITY WORKS — known application-level item
Login will currently fail with HTTP 401 unless the config username exists on the
server. The server only has demo accounts (in `app/auth.py`): `alice`, `bob`
(role holder), `admin` (role admin); password for all is `password`.
- `bank_categories.json` -> `server.username` currently uses `antonschins`, which
  does NOT exist on the server -> 401 until that user is added server-side (edit
  `_USERS` in `app/auth.py`) or the config is changed to an existing account.

---

## ONE-LINE SUMMARY
Server is correct (`0.0.0.0:8000`, firewall open). The Hyper-V External-switch-
over-Wi-Fi blocker was removed. The remaining block is **hotel Wi-Fi client
isolation** (both laptops on the same AP BSSID, internet works, peer traffic
blocked) — use a **phone hotspot** or **wired Ethernet** to connect them, then
verify with `Test-NetConnection <server-ip> -Port 8000`.
