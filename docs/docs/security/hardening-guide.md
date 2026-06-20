---
sidebar_position: 3
---

# Hardening Guide

## Production deployment

### 1. Enable all security features

```yaml
security:
  sandbox_enabled: true
  audit_trail: true
  certification_required: true
  min_certification_level: standard
```

### 2. Restrict permissions

Only enable permissions your agent actually needs:

```yaml
security:
  allowed_permissions:
    - file_read
    - network_outbound
  denied_permissions:
    - file_write
    - os_system
```

### 3. Rate limiting

```yaml
security:
  rate_limit:
    enabled: true
    max_requests_per_minute: 60
    max_tokens_per_hour: 100000
```

### 4. Monitoring

```yaml
security:
  audit_trail: true
  audit_log_path: ~/.kazma/audit.jsonl
  alert_on:
    - eval_usage
    - exec_usage
    - network_anomaly
    - permission_violation
```
