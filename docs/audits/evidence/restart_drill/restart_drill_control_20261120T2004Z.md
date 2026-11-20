# Restart drill: control

- Verdict: **unclean**
- Commit: `9b96c2b8c61b`
- Windows held the restart for: 0.9 s
- Finished: 2026-11-20 20:04 UTC

## What was read

- no stop stamp from this restart: the server never ran its stop (stamp None, boot before 1795204914.0919926)
- the server logged no stop of its own before Windows stopped
- the KazmaEngineShutdown service said: nothing

Evidence: `restart_drill_control_20261120T2004Z.json`.
