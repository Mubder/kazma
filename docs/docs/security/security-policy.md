---
sidebar_position: 1
---

# Security Policy

## Overview

Kazma takes security seriously. This document outlines our security practices and policies.

## Security features

### Sandboxing

All skill execution runs in a sandboxed environment:

- File system access controlled by permissions
- Network access requires explicit permission
- No arbitrary code execution (eval/exec blocked)
- Resource limits enforced

### Permission system

Skills must declare required permissions:

```yaml
permissions:
  - file_read           # Read files
  - file_write          # Write files
  - network_outbound    # Outbound HTTP
  - network_inbound     # Inbound connections
  - camera_access       # Camera hardware
  - mqtt_broker         # MQTT messaging
  - database_read       # Database access
  - database_write      # Database writes
```

### Audit trail

All actions are logged in an immutable audit trail.

### Certification

Skills undergo security certification:

- Basic: Manifest validation
- Standard: Security audit + documentation
- Premium: Full security review + performance benchmarks
