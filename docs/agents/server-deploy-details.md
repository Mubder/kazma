<!-- AGENTS.md, piece server-deploy-details. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**A deploy whose pull changed `pyproject.toml` runs the update before the
reload** (2026-11, FA-258: live ran 26 packages below the build's floors
because a pull installs nothing): `<live python> -m kazma_cli update
--reinstall -y` (it pauses the guard when idle, installs, resumes; §50). A
pull that changed document code also rebuilds the worker image:
`<live python> scripts/document_worker_image.py build` (§19 E1).

`--reload` also picks up a tool added to PATH while Kazma ran (§38). A
new *guard* (its own code, or other OS-level variables) still needs the
`KazmaAgent` task restarted.

**`scripts/deploy_live.py` keeps each step's whole output in `.kazma/deploy/`
of the dev checkout and names the error lines (`essential`); its revert of a
`pyproject.toml` change puts the reverted code's packages back
(`put_packages_back`: the update, else `repair_packages`, every package
rewritten with uv while the guard holds Kazma, which stays held if that
fails) instead of a reload, which lifted a failed update's hold and left
live crash-looping (2026-11-13).** **`scripts/deploy_live.py` does all of the above and checks it** (resilience
plan C2): pull (refused over modified tracked files), the update and image
build when their files changed, reload when idle, then the new commit on
`/health/live`, ready and deep health, one test turn ("Reply with the word
OK", kept out of memory, the chat deleted; a card it raises is denied through
`/api/approve` first and its answer read on, so the check never leaves a turn
waiting -- live 2026-11-27 it deleted a chat under a `python_exec` card and
the run supervisor paged the turn stuck, `deny_approval`) and no
ERROR/CRITICAL line since the boot. A failed check reverts what `origin/main` gained (the live pull is
a merge, so never the live HEAD range) on `main` here, pushes, pulls the
revert into the live install, reloads and pages the owner naming the check
(`--no-revert` pages only). A reload that exits 4 (`RELOAD_WAITS_EXIT`: the
guard kept the running server, §71) is read from the guard's state
(`reload_preflight`): Windows' file check refused a file the new code needs
(`windows_check`) -> the deploy waits, is paged and is never reverted (the
code is not at fault, and the revert's own reload would wait the same way);
the new code itself failed, or no record says -> reverted as any failed
check. Gate: `tests/test_deploy_live.py` (real git repositories, a stub
server; a live checkout with its own commit; exit 4 both ways).

