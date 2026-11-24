<!-- AGENTS.md, piece 21b-system-dependencies. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 21b. Every system dependency is installed by Kazma (`kazma_core/system_deps.py`)

Resilience plan K2. `kazma setup system` (`kazma_cli.setup._setup_system`)
checks Tesseract with Arabic and English, LibreOffice, ClamAV and its daily
signature refresh, the Arabic fonts, Node.js (`npx`, version 18 or later:
the shipped MCP filesystem server) and Docker (`check_all`; the finders
`documents.binaries.find_soffice` and `docker_cli.find_docker_cli`), shows the
plan for the missing ones only (`install_plan`, one table per package
manager: `PACKAGES`), asks (`-y` for a script), runs it, and records the
result beside the Postgres record (`record_system`, `setup_record.json`
"system"). Windows' Tesseract brings English only: the Arabic and English
models come from a pinned tessdata_fast release, kept only when size and
SHA-256 match (`TESSDATA`, `fetch_tessdata`). The signatures get a daily
refresh of their own (a `KazmaFreshclam` scheduled task, a launchd job, the
distribution's freshclam service). `kazma setup install` (21c) runs the
system steps of the parts it installs; `kazma doctor` lists each and names
the command. A second run changes nothing. Gate:
`tests/test_setup_system.py` (every package manager faked; nothing installed
without a yes; a tampered model refused).

**A step that fails is tried again; one that would wait is given nothing**
(the drill VM, 2026-11-06: winget answered 0x80004004 "Cancelled" 59 MB into
LibreOffice, the setup stopped there with no ClamAV or Docker, its message
was winget's progress bar read in the ANSI code page, and the owner had to
press Enter for it to go on). Each step gets `_SYSTEM_ATTEMPTS` (3) tries,
15 s apart; a failed install whose package a check finds is done
(`system_deps.installs_package` / `package_is_in`); a dependency that still
fails stops only its own later steps (`Step.dep`: Tesseract's models,
ClamAV's schedule) and the end names what is not done. Output is read as
UTF-8 with the progress lines dropped, an HRESULT shown in hex. Every
program the setup modules start says its input (`stdin=DEVNULL` or the
step's own), winget gets `--disable-interactivity`, and a Postgres client
that connects to a host gets `-w`/`--no-password` (psql asks for a refused
password on the console whatever its input is). Gates: the drill tests in
`tests/test_setup_system.py` (one attempt, as before, as the negative
control; planted calls for the input and prompt gates). **The `dev` extra
holds `kazma[test]`**: pytest's addopts name `--timeout`, and without
pytest-timeout pytest refused to start on every fresh install, `fast_test.py`
too (`tests/test_setup_extras_run_the_suite.py`, the old `dev` list as the
control); setup says pytest's own last line when it cannot collect.

**A fresh Windows's winget is repaired, and a slow download is not cut off**
(the clean VM `KazmaVMs`, 2026-11-08, reached over SSH). winget answered
0x8A15000F ("Data required by the source is missing") to every install, so
Express left out OCR, LibreOffice, ClamAV and Node.js; `winget source update`
said "Done" and changed nothing. `setup.run_system_step` reads that answer
from a winget step (`_winget_source_missing`), registers the package list's
own signed MSIX (`WINGET_SOURCE_PACKAGE`, `Add-AppxPackage`) once and runs the
step again; a repair that fails says so in the step's answer. A step had 30
minutes: at about 1 MB/s Kazma's optional packages took longer, uv was killed
mid-download and the step said "could not run". A step now has
`STEP_TIMEOUT_S` (3 h: the programs keep their own network timeouts) and one
cut off says "still running after N min, stopped". **The Visual C++ runtime
is a system dependency** (`system_deps._vcruntime_version`, registry
`VC\Runtimes\x64`, at least `VCRUNTIME_MIN` 14.40; winget
`Microsoft.VCRedist.2015+.x64`; Windows only, `WINDOWS_ONLY`/`needed_here`,
checked and installed first): a fresh Windows has none, PyTorch failed to load
c10.dll and the meaning-search model and neural OCR could not install. Earlier
VMs had it only because a winget install before the model brought it along.
Meaning search and neural OCR name it (`system=("vcruntime",)`). **The service
task names its account in full** (`WindowsIdentity::GetCurrent().Name`): the
VM's account and computer were both KazmaDrill_1, and Register-ScheduledTask
refused the bare `$env:USERNAME` ("The parameter is incorrect", UserId); over
SSH `$env:USERDOMAIN` reads WORKGROUP. Gates: the 0x8A15000F tests in
`tests/test_setup_system.py` (the runner without the repair as the negative
control, failing all three attempts),
`test_a_long_step_has_time_and_a_cut_off_one_says_so`, the runtime's version
checks there,
`test_control_without_the_runtime_meaning_search_and_neural_ocr_fail`
(`tests/test_install_modes.py`, whose bare Windows now has no runtime), and
`test_the_task_names_its_account_in_full`
(`tests/test_windows_task_registration.py`). **Setup ends with the
install's exit code**: run 1 left seven parts out and `setup.ps1` still said
"Setup Complete" and exited 0; both scripts now say "Setup Stopped Short" and
exit with it (`test_setup_scripts_end_with_the_install_s_exit_code`). Run 2
on the fixed code: all 14 Express parts, Kazma up as a service ("needs
setup"), 0 errors in its log, 43 minutes.

**A re-run of setup removes nothing** (the same day, the owner: "uv sync
always removes what is there every time it is used again"). A plain `uv sync`
makes the environment exactly the extras it names; `setup.ps1`/`setup.sh`
ran one with rag, dev and tui, so a re-run removed whatever came after (the
Postgres driver among it). Both now run `uv sync --inexact` with every
extra the venv already holds (`update.extras_in_this_environment`: import
markers and the recorded file, never the settings store), and their fix-it
hints repeat that same command; the install's own extras come from 21c's
table, never from a list in a script. No shipped `.ps1`/`.sh` runs an exact
sync, every mode's extras hold the suite, and the Postgres part holds the
Postgres driver: `tests/test_setup_extras_run_the_suite.py` (planted old
lines as controls), and the 5.1 run in `tests/test_powershell_scripts_start.py`
reads the arguments the stand-in uv received (`--inexact`, each extra once,
those the venv held).

