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

## One-shot script (recommended)

From an **Administrator** PowerShell (repo root on Windows):

```powershell
cd G:\GitHubRepos\kazma   # your Windows clone path
.\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
```

What it does:

1. Reads current IPv4 of the WSL distro  
2. Sets `portproxy`: `127.0.0.1:9090` → `<wsl-ip>:9090`  
3. Sets hosts: `kazma.wsl` → `<wsl-ip>`  
4. Prints fixed URLs  

**Re-run after every reboot or `wsl --shutdown`** (IP changes; the script is fast).

### Kazma must listen on all interfaces

Inside WSL:

```bash
cd ~/kazma
export KAZMA_HOST=0.0.0.0
export KAZMA_SECRET='your-strong-secret'
export KAZMA_TRUST_LAN=1   # optional: auto cookie for private LAN / WSL host
.venv/bin/kazma serve 9090
```

- Binding only `127.0.0.1` inside WSL makes portproxy fail (Windows connects to the eth IP).  
- Non-loopback bind requires `KAZMA_SECRET`.  
- Without `KAZMA_TRUST_LAN=1`, open **http://127.0.0.1:9090/login** and enter the secret once.

### Optional: auto-run on Windows login

Task Scheduler → Create Task → Run with highest privileges → At log on → Action:

```text
powershell.exe -ExecutionPolicy Bypass -File G:\GitHubRepos\kazma\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090
```

## Why `localhost:9090` was broken before

A **stale** portproxy row is common with Docker/WSL:

```text
127.0.0.1:9090  →  old-or-wrong-WSL-IP:9090
```

Symptoms: `ERR_CONNECTION_RESET` on localhost while `http://172.28.x.x:9090/` works.

```powershell
netsh interface portproxy show all
# fix with the script, or:
# netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=9090
```

## Alternative: WSL mirrored networking (Windows 11)

In `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
localhostForwarding=true
```

Then `wsl --shutdown` and restart. Mirrored mode makes many localhost cases work without portproxy — test with your Windows build. Portproxy can still conflict; remove stale rules if needed.

## “True” static WSL IP

Not officially supported for normal WSL2. Scripts + portproxy/hosts are the maintainable approach. Avoid manual Hyper-V static IPs unless you accept breakage on WSL upgrades.

## Checklist

1. Admin: `.\scripts\wsl_fixed_access.ps1 -Distro Hermes_API_1 -Port 9090`  
2. WSL: `KAZMA_HOST=0.0.0.0` + `KAZMA_SECRET` + `kazma serve 9090`  
3. Browser: **http://127.0.0.1:9090/** or **http://kazma.wsl:9090/**  
4. If 401s: **/login** with secret, or `KAZMA_TRUST_LAN=1`  
