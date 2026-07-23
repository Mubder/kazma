---
id: wsl-fixed-access
title: Fixed access to WSL Kazma (Windows)
sidebar_label: WSL fixed access
description: Stable localhost / hostname for Kazma running inside WSL2
---

# Fixed access to Kazma in WSL2

WSL2 assigns a **changing** virtual IP (e.g. `172.28.225.216`). That breaks bookmarks like `http://172.28.x.x:9090/` after reboot.

You cannot easily freeze Hyper-V’s WSL IP forever. Instead, pin a **stable Windows-side address**:

| Stable URL | How |
|------------|-----|
| `http://127.0.0.1:9090/` | Windows **portproxy** → current WSL IP |
| `http://localhost:9090/` | Same |
| `http://kazma.wsl:9090/` | **hosts** file → current WSL IP |

---

## After reboot (short checklist)

Do this after **Windows reboot** or **`wsl --shutdown`**:

### 1. Windows — pin localhost (Admin PowerShell, once per boot)

```powershell
cd G:\GitHubRepos\kazma
.\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
```

Optional: Task Scheduler → **Run with highest privileges** → At log on → same command, so you never type it again.

### 2. WSL — start Kazma (every boot)

```bash
cd ~/kazma          # or your WSL clone path
./scripts/start-web.sh
```

Wait for:

```text
Uvicorn running on http://0.0.0.0:9090
```

### 3. Browser

- **http://127.0.0.1:9090/**  
- or **http://localhost:9090/**  
- or **http://kazma.wsl:9090/**

If APIs return **401**, open **/login** and enter `KAZMA_SECRET`, or ensure `KAZMA_TRUST_LAN=1` in `.env`.

| You do **not** need every time | Only when |
|--------------------------------|-----------|
| Re-type secrets | First setup (use `.env`) |
| `git pull` | You want code updates |
| Manual `export KAZMA_HOST=…` | Missing from `.env` / start script |

---

## One-shot portproxy script

From an **Administrator** PowerShell (Windows clone path):

```powershell
cd G:\GitHubRepos\kazma
.\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
```

What it does:

1. Reads current IPv4 of the WSL distro  
2. Sets `portproxy`: `127.0.0.1:9090` → `<wsl-ip>:9090`  
3. Sets hosts: `kazma.wsl` → `<wsl-ip>` (skipped if hosts file is locked)  
4. Prints fixed URLs  

**Re-run after every reboot or `wsl --shutdown`** unless you automated it (see above).

---

## Start script (`scripts/start-web.sh`)

Checked into the repo for WSL (and any Linux) use:

```bash
chmod +x scripts/start-web.sh   # once
./scripts/start-web.sh          # default port 9090
./scripts/start-web.sh 9091     # custom port
```

### Recommended `.env` (repo root, once)

```bash
# ~/kazma/.env  (do not commit real secrets)
KAZMA_HOST=0.0.0.0
KAZMA_SECRET=your-strong-secret-here
KAZMA_TRUST_LAN=1
```

- **`KAZMA_HOST=0.0.0.0`** — required so Windows → WSL eth IP / portproxy can connect  
- **`KAZMA_SECRET`** — required for non-loopback bind  
- **`KAZMA_TRUST_LAN=1`** — optional single-operator auto-cookie for private clients (e.g. Windows host `172.28.224.1`)

The start script sources `.env` if present, then runs `.venv/bin/kazma serve`.

Manual equivalent without the script:

```bash
cd ~/kazma
export KAZMA_HOST=0.0.0.0
export KAZMA_SECRET='your-strong-secret'
export KAZMA_TRUST_LAN=1
.venv/bin/kazma serve 9090
```

---

## Why `localhost:9090` was broken

A **stale** portproxy row is common with Docker/WSL:

```text
127.0.0.1:9090  →  old-or-wrong-WSL-IP:9090
```

Symptoms: `ERR_CONNECTION_RESET` on localhost while `http://172.28.x.x:9090/` works.

```powershell
netsh interface portproxy show all
# fix with wsl_fixed_access.ps1, or:
# netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=9090
```

Also: Kazma must listen on **`0.0.0.0`**, not only `127.0.0.1` inside WSL — portproxy connects to the distro’s eth IP.

---

## Optional automations

### Windows login → pin portproxy

Task Scheduler → Create Task → **Run with highest privileges** → At log on → Action:

```text
powershell.exe -ExecutionPolicy Bypass -File G:\GitHubRepos\kazma\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
```

### Windows shortcut → start WSL Kazma

```text
wsl -d Hermes_API_1 -- bash -lc 'cd ~/kazma && ./scripts/start-web.sh'
```

---

## Alternative: WSL mirrored networking (Windows 11)

In `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
localhostForwarding=true
```

Then `wsl --shutdown` and restart. Mirrored mode can make localhost work without portproxy on some builds — test on yours. Stale portproxy rules can still conflict; remove them if needed.

---

## “True” static WSL IP

Not officially supported for normal WSL2. Prefer **portproxy + hosts + start-web.sh**. Avoid manual Hyper-V static IPs unless you accept breakage on WSL upgrades.

---

## Full checklist

1. **Once:** `.env` with `KAZMA_HOST` / `KAZMA_SECRET` / `KAZMA_TRUST_LAN`  
2. **Once:** `chmod +x scripts/start-web.sh`  
3. **Each boot (Windows Admin):** `wsl_fixed_access.ps1` (or Task Scheduler)  
4. **Each boot (WSL):** `./scripts/start-web.sh`  
5. **Browser:** **http://127.0.0.1:9090/**  
6. If 401s: **/login** with secret, or confirm `KAZMA_TRUST_LAN=1`  
