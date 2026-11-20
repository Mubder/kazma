# Restart drill: fix

- Verdict: **clean**
- Commit: `97528993e1d8`
- Windows held the restart for: 2.1 s
- Finished: 2026-11-20 20:12 UTC

## What was read

- the server recorded its stop 1.9 s after the restart was asked
- the server logged its stop: [shutdown] the stop took 0.0 s of its 50 s budget (every step under 0.1 s)
- the KazmaEngineShutdown service said: Kazma stopped after 0.2 s

Evidence: `restart_drill_fix_20261120T2012Z.json`.
