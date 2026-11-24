<!-- AGENTS.md, piece 28-guards-need-controls. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 28. A guard nobody has seen fail (2026-08-30)

Every guard test needs a **negative control**: assert it FAILS on a
synthetic violation in the same file, or "passing" means nothing. Enumerate
inputs from the **same SoT the system uses** (CSS gate parses `base.html`
stylesheet list — `kazma.v5.css` loads second and last-wins). Prefer
measuring behaviour to grepping source. A guard can be wrong in the
direction of blocking good work (`ast.walk` flagged a correct lazy
`import fitz`). Runtime corollary: `start_stall_watchdog` must be
stoppable — cancelling the heartbeat must not leave a thread reporting
stalls forever. HITL card CSS: do not duplicate selectors in `kazma.css`
and `kazma.v5.css` (Wave 8 L-2: standalone `.metric-card` lives in v5).


