---
sidebar_position: 1
title: Security Overview
---

# Security Overview

## Security Model

Kazma implements a multi-layered security model:

1. **Sandboxing** — All skill execution is sandboxed
2. **Permissions** — Explicit permission grants required
3. **Audit Trail** — All actions are logged
4. **Certification** — Skills undergo security review

## Permission Types

| Permission | Description |
|---|---|
| `file_read` | Read files on the system |
| `file_write` | Write files on the system |
| `network_outbound` | Make outbound HTTP requests |
| `network_inbound` | Accept inbound connections |
| `camera_access` | Access camera hardware |
| `mqtt_broker` | Connect to MQTT brokers |
| `database_read` | Read from databases |
| `database_write` | Write to databases |

## Security Auditing

Skills are automatically scanned for:

- Dangerous code patterns (eval, exec, os.system)
- Hardcoded secrets
- Suspicious imports
- Permission violations

## Certification Levels

- **Basic**: Manifest validation
- **Standard**: Security audit + documentation
- **Premium**: Full security review + performance benchmarks
