"""No tracked file holds a personal address someone committed under.

The repository is public. Until 2026-11-18 the metrics generator
(``scripts/generate_metrics.py``) kept the maintainer's personal email
address in its author-alias map, beside the GitHub request it makes; the
address now comes from the maintainer's own machine (``ALIASES_ENV``). This
gate keeps every personal commit address out of the tracked files.

The addresses come from the repository's own history (``git log``: author and
committer of every commit), so this file names none. An address is public --
allowed in a file -- when its domain is a GitHub no-reply domain, the
project's own, or a placeholder domain. CI's test job clones the whole
history (``fetch-depth: 0``), so it sees every address.

The negative controls feed the scanner a text holding a personal address read
from the history, and an address on a public domain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Domains whose addresses are public: GitHub's own and its no-reply
#: addresses, the project's domain, Anthropic's no-reply (co-author lines),
#: placeholders.
PUBLIC_DOMAINS = frozenset(
    {
        "users.noreply.github.com",
        "github.com",
        "kazma.ai",
        "anthropic.com",
        "example.com",
        "example.org",
        "example.net",
        "local.dev",
        "localhost",
    }
)
_PLACEHOLDER_SUFFIXES = (".example", ".test", ".invalid", ".localhost", ".local")

#: Automation accounts that committed here: service addresses, not a person's
#: (the metrics generator names them to leave them out of the count).
AUTOMATION_ADDRESSES = frozenset({"bot@openresearch.sh", "qwenlm-intl@service.alibaba.com"})


def is_public(address: str) -> bool:
    address = address.strip().lower()
    domain = address.rsplit("@", 1)[-1]
    return (
        address in AUTOMATION_ADDRESSES
        or domain in PUBLIC_DOMAINS
        or domain.endswith(_PLACEHOLDER_SUFFIXES)
    )


def commit_addresses() -> set[str]:
    out = subprocess.run(
        ["git", "log", "--all", "--format=%ae%n%ce"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    ).stdout
    return {line.strip().lower() for line in out.splitlines() if "@" in line}


def personal_addresses() -> set[str]:
    return {a for a in commit_addresses() if not is_public(a)}


def files_naming(addresses: set[str], rev: str | None = None) -> list[str]:
    """Tracked text files that hold any of ``addresses`` (any case), in the
    working tree or, with ``rev``, in that commit's tree."""
    if not addresses:
        return []
    args = ["git", "grep", "-I", "-i", "-l", "-F"]
    for address in sorted(addresses):
        args += ["-e", address]
    if rev:
        args += [rev, "--"]
    done = subprocess.run(args, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode == 1:  # git grep: nothing found
        return []
    assert done.returncode == 0, done.stderr
    return sorted(line for line in done.stdout.splitlines() if line)


#: The last commit whose metrics generator held a personal address.
OLD_GENERATOR = "2ceb89b7f"


def test_no_tracked_file_names_a_personal_commit_address():
    addresses = personal_addresses()
    if not addresses:
        pytest.skip("this clone's history has no personal commit address to look for")
    found = files_naming(addresses)
    assert not found, (
        "These tracked files hold a personal address someone commits under; the "
        "repository is public. Put the address in a setting on that person's "
        f"machine instead (e.g. scripts/generate_metrics.py's ALIASES_ENV): {found}"
    )


def test_negative_control_the_old_generator_is_found():
    """The same scan over the tree before the fix finds the generator."""
    addresses = personal_addresses()
    known = subprocess.run(
        ["git", "cat-file", "-e", f"{OLD_GENERATOR}^{{commit}}"], cwd=REPO, capture_output=True
    )
    if not addresses or known.returncode != 0:
        pytest.skip("this clone has no personal commit address or not the old commit")
    assert f"{OLD_GENERATOR}:scripts/generate_metrics.py" in files_naming(addresses, rev=OLD_GENERATOR)


def test_public_addresses_are_allowed():
    assert is_public("46130504+someone@users.noreply.github.com")
    assert is_public("admin@kazma.ai")
    assert is_public("noreply@anthropic.com")
    assert is_public("you@example.com")
    assert is_public("bot@ci.test")
    assert not is_public("someone@mailprovider.net")


def test_the_metrics_generator_reads_personal_aliases_from_the_machine(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("gm_aliases", REPO / "scripts" / "generate_metrics.py")
    gm = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gm)

    log = "\n".join(
        [
            "46130504+someone@users.noreply.github.com\tSomeone",
            "someone@mailprovider.net\tSomeone",
            "other@mailprovider.net\tOther",
        ]
    )
    monkeypatch.setattr(gm, "_count_contributors_via_api", lambda: None)
    monkeypatch.setattr(gm, "git", lambda *a, **k: log)
    monkeypatch.setattr(gm, "_EMAIL_ALIASES", {"46130504+someone@users.noreply.github.com": "someone"})

    # Without the setting the personal address is its own contributor.
    monkeypatch.delenv(gm.ALIASES_ENV, raising=False)
    assert gm.count_human_contributors() == 3

    # With it, the two addresses are one person. A malformed pair is ignored.
    monkeypatch.setenv(gm.ALIASES_ENV, " SomeOne@MailProvider.net = someone ,broken,=x, y= ")
    assert gm.count_human_contributors() == 2
