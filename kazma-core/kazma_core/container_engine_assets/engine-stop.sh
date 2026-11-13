#!/bin/bash
# Installs the engine's two shutdown helpers in the kazma-engine distro; run as
# root by setup (kazma_core/container_engine_setup.py: the engine:docker and
# engine:shutdown steps). Idempotent.
#
# kazma-engine-hold is what keep-engine.ps1 holds the distro up with. Run as
# `kazma-engine-hold piped`, its stdin and stdout are the KazmaEngineShutdown
# service's pipes: when Windows shuts down, the service writes "stop" -- through
# the wsl.exe already running, because Windows starts nothing new then --; it
# runs kazma-engine-stop and answers with its last line. Then "poweroff" powers
# the distro off. They are two lines so the answer reaches Windows before the
# distro ends. When the service goes away (stdin ends) the piped holder ends,
# and keep-engine.ps1 starts one for the service that replaces it.
#
# kazma-engine-stop stops Docker the way a daemon shutdown does: each container
# gets its stop signal and its own stop timeout, and its restart policy brings
# it back at the next boot (a `docker stop` would keep an unless-stopped
# container down). Docker first, then containerd: masking them first reloads
# them as empty units with no order, and containerd stopped at once under a
# Docker still stopping its containers ("Force shutdown daemon", 36 s, the
# drill VM, 2026-11-13). Then nothing starts them again before the distro
# ends, and the distro's own disks are written out (syncfs, bounded). Never a
# plain `sync`: it also flushes the Windows drives mounted at /c, which
# Windows itself serves, and during a Windows shutdown it never returned (the
# drill VM, 2026-11-13). Those writes are Windows' to keep.
set -euo pipefail
cat > /usr/local/sbin/kazma-engine-stop <<'STOP'
#!/bin/bash
t0=$(cut -d' ' -f1 /proc/uptime)
systemctl stop docker.socket docker.service
code=$?
systemctl stop containerd.service
systemctl mask --runtime docker.service docker.socket containerd.service >/dev/null 2>&1 || true
pids=()
while IFS= read -r fs; do
  sync -f "$(printf '%b' "$fs")" & pids+=("$!")
done < <(findmnt -rn -t ext4,xfs,btrfs -o TARGET)
disks="disks written"
for _ in $(seq 200); do
  alive=0
  for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && alive=1; done
  [ "$alive" = 0 ] && break
  sleep 0.1
done
[ "$alive" = 1 ] && disks="disks still being written after 20 s"
t1=$(cut -d' ' -f1 /proc/uptime)
echo "ENGINE_STOPPED in $(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }') s (docker stop exit $code, $disks)"
STOP
cat > /usr/local/sbin/kazma-engine-hold <<'HOLD'
#!/bin/bash
while IFS= read -r line; do
  case "${line%$'\r'}" in
    stop) /usr/local/sbin/kazma-engine-stop 2>&1 | tail -n 1 ;;
    poweroff) exec systemctl poweroff ;;
  esac
done
[ "${1:-}" = piped ] && exit 0
exec sleep infinity
HOLD
chmod 755 /usr/local/sbin/kazma-engine-stop /usr/local/sbin/kazma-engine-hold
echo STOP_READY
