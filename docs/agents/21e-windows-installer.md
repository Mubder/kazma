<!-- AGENTS.md, piece 21e-windows-installer. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 21e. The Windows installer and release installs (REMAINING 2.7, 2026-11-16)

Kazma installs on Windows with no git and no terminal, as a third install
type beside a git checkout and a wheel install.
- **The installer** (`scripts/windows_installer/`: `kazma.iss`, Inno Setup
  6.7.4 pinned by SHA-256 in `build.py`) is built in the release workflow's
  `windows-installer` job from the source archive the `build` job made
  (`kazma-<version>.tar.gz`, the archive every release signs). `build.py`
  reads it with the updater's own rules (`release_tree.shipped_files`) and
  writes the release marker beside the files. Installed to
  `%LOCALAPPDATA%\Programs\Kazma` (admin, asked once), it runs the same
  `setup.cmd -Mode express|full -Yes -NoElevate` a clone runs; setup's
  `EXIT_RESTART` (3) leaves a RunOnce entry that runs setup again at the
  next sign-in. Shortcuts: Kazma (`kazma open`, the configured port), Update,
  Finish or repair setup. Uninstall runs `kazma uninstall --yes` (data kept).
- **A release install** is that folder: `.kazma-release.json` at its root
  names the release and the files it shipped (`kazma_cli/release_tree.py`).
  `update.detect_install_type` answers `"release"` for it first (it is
  installed editable, so it looked like a checkout); `update._install_root`
  is the one answer to "this install's folder" (a checkout's or a release
  install's), used wherever the updater located itself by `.git`.
  `kazma update` there (`do_release_update`) downloads the next release's
  source archive under the wheel's rules (`_source_archive`: named for the
  version, in SHA256SUMS and equal to GitHub's digest, Sigstore-signed from
  SIGNED_FROM on), lays it over the tree (`release_tree.apply_archive`:
  replaces and removes shipped files only, never `release_tree.KEPT` or the
  owner's own files, every moved file backed up first), installs the locked
  packages held (`_reinstall_via_subprocess`), and on a refused or failed
  install rolls the tree back and reinstalls its packages.
- **Signing** is one shared step, `.github/actions/sign-windows-installer`
  (2026-11-22), with the service the repository variable
  `KAZMA_WINDOWS_SIGNING` names: `signpath` (SignPath Foundation), `ssl-com`
  (SSL.com eSigner) or `artifact-signing` (Microsoft, OIDC). Changing service
  is that variable and the new service's secrets (`docs/SUPPLY_CHAIN.md` "The
  Windows installer's signature"); any other value fails before anything is
  sent; a signed installer must read Valid and timestamped or the job fails;
  unset, the run says it is unsigned. The manual workflow
  `windows-signing-test.yml` signs a program it builds, never a release file,
  through the same step in its test environment (SSL.com's sandbox,
  SignPath's test-signing policy) and publishes nothing. No workflow names a
  signing action outside the shared step. The installer is also checksummed,
  attested and Sigstore-signed with the rest.
- Gates: `tests/test_release_install.py` (a failure half way and each refusal
  leave the tree as it was; without `KEPT` an archive replaces `.env`, the
  control), `tests/test_windows_installer.py`, the release gates reading the
  three jobs in order (`tests/test_release_supply_chain.py`). Drill:
  `scripts/windows_installer_drill.py` (a clean VM: install, restart, health,
  `update --check`, shortcuts, uninstall keeping the data).

