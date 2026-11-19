#!/bin/bash
# Kazma's container engine: Docker Engine in the kazma-engine WSL distro,
# reached from Windows over mutual TLS on 127.0.0.1:2376
# (kazma_core/container_engine_setup.py; docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md).
#
# Run as root inside the distro, read from stdin by setup:
#   bash -s -- <bridge address/prefix> <address pool base/prefix>
# Idempotent: a second run repairs, and keeps the CA and keys it made.
set -euo pipefail
BIP="${1:-10.213.0.1/24}"
POOL="${2:-10.213.128.0/17}"

# Drives at /c, /g (compose's COMPOSE_CONVERT_WINDOWS_PATHS writes /c/...),
# no Windows programs from inside, systemd for dockerd.
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[automount]
root=/

[interop]
enabled=false
appendWindowsPath=false

[user]
default=root
EOF

# Docker's apt signing key is trusted only with the fingerprint Docker
# publishes (docs.docker.com/engine/install/ubuntu). It was trusted on first
# download, whatever it was; now a key with any other fingerprint -- or more
# keys than Docker's one -- is refused before apt ever reads it, and a key a
# second run finds is checked again.
DOCKER_KEY_FINGERPRINT=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
DOCKER_KEY_URL=https://download.docker.com/linux/ubuntu/gpg

# The fingerprints of the primary keys in the key file $1, one a line.
kazma_key_fingerprints() {
  gpg --batch --show-keys --with-colons "$1" 2>/dev/null \
    | awk -F: '$1 == "pub" { want = 1; next } want && $1 == "fpr" { print $10; want = 0 }'
}

# Docker's key at $1/docker.asc, only when it is Docker's; fails otherwise.
kazma_trust_docker_key() {
  local keyrings="$1" key="$1/docker.asc" tmp
  install -m 0755 -d "$keyrings"
  if [ -f "$key" ] && [ "$(kazma_key_fingerprints "$key")" = "$DOCKER_KEY_FINGERPRINT" ]; then
    return 0
  fi
  tmp="$(mktemp)"
  if ! curl -fsSL "$DOCKER_KEY_URL" -o "$tmp"; then
    rm -f "$tmp"
    echo "KAZMA_ENGINE_ERROR Docker's signing key could not be downloaded" >&2
    return 1
  fi
  if [ "$(kazma_key_fingerprints "$tmp")" != "$DOCKER_KEY_FINGERPRINT" ]; then
    rm -f "$tmp" "$key"
    echo "KAZMA_ENGINE_ERROR Docker's signing key is not the one Docker publishes" \
      "($DOCKER_KEY_FINGERPRINT): not trusted" >&2
    return 1
  fi
  install -m 0644 "$tmp" "$key"
  rm -f "$tmp"
}

# Ubuntu's archive over HTTPS (2026-11-19). archive.ubuntu.com and
# security.ubuntu.com are served through a CDN, and over plain HTTP the
# network on the way (Kuwait, 2026-11-19) handed back a corrupted copy of one
# package list -- the same wrong bytes on every try, a day's cache and an
# uncached fetch alike -- so apt refused it and the engine could not be set
# up; over HTTPS the same list came back right. apt checks every list against
# Ubuntu's signature either way: HTTPS keeps anything on the way from changing
# the bytes. Every http:// source in apt's lists under $1 becomes https://,
# once the distro has the CA certificates ($2) apt needs to reach it; without
# them nothing is changed.
kazma_https_sources() {
  local apt_dir="$1" ca="${2:-/etc/ssl/certs/ca-certificates.crt}" f
  if [ ! -s "$ca" ]; then
    echo "no CA certificates in the distro: apt's sources are left as they are" >&2
    return 0
  fi
  for f in "$apt_dir/sources.list" "$apt_dir"/sources.list.d/*.list "$apt_dir"/sources.list.d/*.sources; do
    [ -f "$f" ] || continue
    sed -i -E '/^[[:space:]]*(URIs:|deb[[:space:]]|deb-src[[:space:]])/ s#http://#https://#g' "$f"
  done
}

export DEBIAN_FRONTEND=noninteractive
kazma_https_sources /etc/apt
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q ca-certificates curl gnupg openssl unattended-upgrades
kazma_trust_docker_key /etc/apt/keyrings
if ! command -v dockerd >/dev/null 2>&1; then
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
# Docker restarts every container when it is upgraded: only `kazma update`
# upgrades it, in a maintenance window, never an unattended upgrade.
apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null

# Mutual TLS: a CA kept here (root only), a server certificate for
# 127.0.0.1, and a client certificate for Windows. Made once.
ca=/root/kazma-engine-ca
tls=/etc/docker/tls
client=/root/kazma-engine-client
install -d -m 700 "$ca" "$client"
install -d -m 755 "$tls"
if [ ! -f "$ca/ca-key.pem" ]; then
  openssl genrsa -out "$ca/ca-key.pem" 4096 2>/dev/null
  openssl req -x509 -new -key "$ca/ca-key.pem" -sha256 -days 3650 \
    -subj "/CN=Kazma container engine CA" -out "$ca/ca.pem"
fi
if [ ! -f "$tls/server-cert.pem" ]; then
  openssl genrsa -out "$tls/server-key.pem" 4096 2>/dev/null
  openssl req -new -key "$tls/server-key.pem" -subj "/CN=127.0.0.1" -out /tmp/server.csr
  printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n' > /tmp/server.ext
  openssl x509 -req -in /tmp/server.csr -CA "$ca/ca.pem" -CAkey "$ca/ca-key.pem" -CAcreateserial \
    -days 3650 -sha256 -extfile /tmp/server.ext -out "$tls/server-cert.pem" 2>/dev/null
  rm -f /tmp/server.csr /tmp/server.ext
fi
cp "$ca/ca.pem" "$tls/ca.pem"
chmod 600 "$tls/server-key.pem"
if [ ! -f "$client/cert.pem" ]; then
  openssl genrsa -out "$client/key.pem" 4096 2>/dev/null
  openssl req -new -key "$client/key.pem" -subj "/CN=kazma-engine-client" -out /tmp/client.csr
  printf 'extendedKeyUsage=clientAuth\n' > /tmp/client.ext
  openssl x509 -req -in /tmp/client.csr -CA "$ca/ca.pem" -CAkey "$ca/ca-key.pem" -CAcreateserial \
    -days 3650 -sha256 -extfile /tmp/client.ext -out "$client/cert.pem" 2>/dev/null
  rm -f /tmp/client.csr /tmp/client.ext
fi
cp "$ca/ca.pem" "$client/ca.pem"
chmod 600 "$client/key.pem"

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "hosts": ["unix:///var/run/docker.sock", "tcp://127.0.0.1:2376"],
  "tls": true,
  "tlsverify": true,
  "tlscacert": "/etc/docker/tls/ca.pem",
  "tlscert": "/etc/docker/tls/server-cert.pem",
  "tlskey": "/etc/docker/tls/server-key.pem",
  "bip": "${BIP}",
  "default-address-pools": [{"base": "${POOL}", "size": 24}],
  "labels": ["io.kazma.engine=wsl", "io.kazma.engine.drive-root=/"],
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
EOF
# daemon.json names the sockets, so systemd must not pass -H fd://.
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/kazma.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/dockerd --containerd=/run/containerd/containerd.sock
EOF
systemctl enable docker.service containerd.service >/dev/null 2>&1 || true
echo "ENGINE_READY $(dockerd --version)"
