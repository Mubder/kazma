<!-- AGENTS.md, piece 58-signals. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 58. Signals people can use (resilience plan Part G)

- **Quiet logs** (G1, `observability/log_noise.py`). A live day (2026-11-05)
  wrote 32,090 lines: 12,459 "Batch set" lines and 11,200 access lines of
  polls. The settings store's batch line, the X mentions poll and a reply's
  save while it streams are DEBUG; `QuietPolls` on `uvicorn.access` keeps the
  answered requests of the polled routes (`POLLED_ROUTES`: the bell, pending
  approvals, a chat's status, `/health/*`) out, never an error status. Gate:
  `tests/test_quiet_logs.py` replays that day (`tests/fixtures/log_noise/live_day.json`,
  shapes and counts only) against the code: no routine line more than 30
  times an hour (the recorded day without the change as the negative control).
- **One list of open problems** (G2, `observability/open_problems.py`): the
  bell's open alerts, run incidents, research and crawl alerts, unmet install
  requirements, open advisories and the observer's open incident, built each
  time from where each lives, so a resolved problem leaves by itself. The
  Dashboard's card (`GET /api/problems`, `static/js/problems_panel.js`) words
  each in the page's language (`problems.source.*`, `problems.resolved.*`;
  titles and details are the problems' own data); Acknowledge (until it
  changes: a changed problem has another id) and Snooze (a day) are kept in
  `system.problems.seen`, admin-only. A source that cannot be read is named.
  The daily digest reads the same list (`_open_problems_line`). Gates:
  `tests/test_open_problems.py`, `tests/e2e/test_problems_panel.py` (EN/AR,
  reached by the sidebar). **Every problem says what to do** (2026-11-20):
  a problem with no command of its own shows its kind's "what to do" line
  (`problems.todo.<source>`, EN/AR) beside when it closes; a failed run's
  bell text carries its reason; the observer's problem opens Settings →
  Alerts. Gate: `tests/test_problems_say_how_to_fix.py` (every kind the card
  knows has its line in both languages).
- **The weekly report against targets** (G3, `observability/weekly_targets.py`):
  uptime (the observer's), turns that succeed, restarts not asked for, the
  median time to recover, backups fresh every day, stops over budget and
  alerts sent, each met or missed with the week before, measured from the
  app's and the guard's logs (only the lines a target reads are kept). A
  target with nothing to measure is "unknown", never met. It follows "Needs
  a look" in the weekly message. Gate: `tests/test_weekly_targets.py` (a
  recorded fortnight with a missed target).

