---
id: chaos-testing
title: Chaos testing (fault injection)
sidebar_label: Chaos testing
description: Inject latency, errors and timeouts into a running Kazma to watch its retries, failover and error handling work
---

# Chaos testing (fault injection)

Kazma can inject failures into itself: slow or failing model calls, failing
tool calls, failing database writes. It exists to prove that the retry,
failover and error paths work, on a test server, before a real outage tests
them. The code is `kazma_core/chaos/__init__.py`; the HTTP routes are
`kazma_ui/routes_chaos.py`.

## Off unless you turn it on

`KAZMA_CHAOS_ENABLED=true` (or `1`, `yes`, `on`) turns the feature on. Without it the
`/api/chaos` routes are not mounted, and every injection point is inert even if
something registers an injection, so a stray call can never cause a real
failure. The routes are admin-only. Do not enable it on a server people depend
on.

## Where failures actually land

An injection names a **target**. Three targets have an injection point in the
product's code; the others exist in the list but nothing checks them, so an
injection aimed at them changes nothing.

| Target | Where it lands |
|---|---|
| `llm_provider` | Every attempt inside `resilient_chat`: a model call. An injected 408, 429 or 5xx is marked transient, so the real retry and failover logic runs on it |
| `tool_executor` | `UnifiedToolExecutor.execute` (`kazma_core/mcp/manager.py`) |
| `database` | `reply_sink.upsert_reply`: saving a chat reply |
| `message_bus`, `swarm_engine`, `gateway_adapter`, `webhook_handler`, `cache`, `external_api` | No injection point: nothing happens |

Failure types: `latency`, `error`, `timeout`, `circuit_breaker_open`,
`resource_exhaustion`, `network_partition`, `data_corruption`,
`partial_degradation`.

## Ready-made experiments

Each runs for its duration, then its injection expires.

| Experiment | Target | Failure | Chance per call | Runs for |
|---|---|---|---|---|
| `llm_high_latency` | llm_provider | 7.5 s latency | 30 % | 60 s |
| `llm_intermittent_errors` | llm_provider | error 500 | 10 % | 120 s |
| `llm_timeout` | llm_provider | timeout | 5 % | 60 s |
| `circuit_breaker_force_open` | llm_provider | breaker open | 50 % | 30 s |
| `database_slow` | database | 3 s latency | 20 % | 60 s |
| `database_errors` | database | error 503 | 5 % | 120 s |
| `resource_exhaustion` | database | resource exhaustion | 10 % | 60 s |
| `tool_executor_failures` | tool_executor | error 500 | 10 % | 60 s |
| `message_bus_partition` | message_bus | partition | 10 % | 30 s — no effect (no injection point) |
| `swarm_engine_degradation` | swarm_engine | partial degradation | 20 % | 120 s — no effect |
| `gateway_adapter_errors` | gateway_adapter | error 502 | 5 % | 60 s — no effect |

## HTTP routes (admin, only when enabled)

| Route | What it does |
|---|---|
| `GET /api/chaos/experiments` | The ready-made experiments |
| `POST /api/chaos/experiments/{name}/run` | Start one |
| `GET /api/chaos/injections` | Injections now active |
| `DELETE /api/chaos/injections/{injection_id}` | Stop one |
| `DELETE /api/chaos/injections` | Stop all |
| `POST /api/chaos/injections/custom` | Start your own |

A custom injection takes `failure_type`, `target`, `probability` (0 to 1) and
`duration_seconds` (above 0), plus optional `params`: `latency_ms`,
`error_code`, `error_message`, `severity`, `metadata`. Anything else is
refused with a 400.

## From Python

`chaos_experiment(...)` is an async context manager that injects for the
duration of a block, `chaos_injection(target)` is the decorator that makes a
function an injection point, and `get_injector()` gives the process's
injector. `run_predefined_experiment(name)`, `list_predefined_experiments()`
and `list_active_injections()` are what the routes call.
