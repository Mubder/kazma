"""``kazma update`` installs a release wheel only with a Sigstore signature from
Kazma's release workflow (2026-11, supply chain; kazma_cli.release_signature).

The updater checked a wheel against the release's ``SHA256SUMS`` and GitHub's
asset digest -- both in the same release as the wheel, so an account able to
change the release's files could change all three. Now, from
``release_signature.SIGNED_FROM`` (0.21.2, the first release after v0.21.1)
on, the wheel's ``.sigstore.json`` bundle must verify: chained to Sigstore, in
its transparency log, over the wheel's SHA-256, and naming
``.github/workflows/release.yml`` of ``Mubder/kazma``. An older release is
installed on its checksums, and the updater says so; a bundle an older release
does carry is verified all the same. The cutoff is not v0.11.0, the first
release the workflow signed: v0.12.0 to v0.21.1 were published by hand with no
bundles, and that cutoff refused every one of them (the control below).

Recorded, real material (``tests/fixtures/release_signature/``, no network):
Kazma v0.11.0's ``SHA256SUMS`` and its bundle (signed by the release workflow
like the wheel beside it), a bundle another repository's workflow made
(trailofbits/rfc8785.py v0.1.4), and Sigstore's production root of trust as
sigstore-python 4.5.1 ships it. The checks needing sigstore skip where it is
not installed; it is a base dependency, so CI has it.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_signature"
SUMS = FIXTURES / "kazma-0.11.0-SHA256SUMS"
SUMS_BUNDLE = FIXTURES / "kazma-0.11.0-SHA256SUMS.sigstore.json"
OTHER_BUNDLE = FIXTURES / "rfc8785-0.1.4-py3-none-any.whl.sigstore.json"
#: SHA-256 of rfc8785-0.1.4-py3-none-any.whl, the file OTHER_BUNDLE signs.
OTHER_SHA256 = "520d690b448ecf0703691c76e1a34a24ddcd4fc5bc41d589cb7c58ec651bcd48"
KAZMA_IDENTITY = "https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/heads/main"


def _recorded_verifier() -> Any:
    pytest.importorskip("sigstore")
    from sigstore.models import TrustedRoot
    from sigstore.verify import Verifier

    return Verifier(trusted_root=TrustedRoot.from_file(str(FIXTURES / "trusted_root.json")))


# ── the check itself, on real bundles ─────────────────────────────────────


def test_a_release_the_workflow_signed_verifies():
    from kazma_cli.release_signature import check_bundle

    data = SUMS.read_bytes()
    assert hashlib.sha256(data).hexdigest() == "51b74d50fcd6eee5ced73b20f5d41b9727ef68d952168839aa16ab1376b3a440"
    signer = check_bundle(hashlib.sha256(data).digest(), SUMS_BUNDLE.read_bytes(), verifier=_recorded_verifier())
    assert signer == KAZMA_IDENTITY


def test_a_changed_file_is_refused():
    from kazma_cli.release_signature import SignatureRefused, check_bundle

    data = bytearray(SUMS.read_bytes())
    data[0] ^= 1
    with pytest.raises(SignatureRefused, match="digest mismatch"):
        check_bundle(hashlib.sha256(bytes(data)).digest(), SUMS_BUNDLE.read_bytes(), verifier=_recorded_verifier())


def test_another_repositorys_valid_signature_is_refused():
    """The bundle is good -- sigstore verifies it with no identity asked -- but
    it names trailofbits/rfc8785.py's workflow, not Kazma's."""
    from kazma_cli.release_signature import SignatureRefused, check_bundle

    verifier = _recorded_verifier()
    from sigstore.hashes import Hashed
    from sigstore.models import Bundle
    from sigstore.verify import policy
    from sigstore_models.common.v1 import HashAlgorithm

    digest = bytes.fromhex(OTHER_SHA256)
    verifier.verify_artifact(Hashed(algorithm=HashAlgorithm.SHA2_256, digest=digest),
                             Bundle.from_json(OTHER_BUNDLE.read_bytes()), policy.UnsafeNoOp())
    with pytest.raises(SignatureRefused, match="rfc8785"):
        check_bundle(digest, OTHER_BUNDLE.read_bytes(), verifier=verifier)


def test_a_bundle_for_another_file_and_a_broken_bundle_are_refused():
    from kazma_cli.release_signature import SignatureRefused, check_bundle

    verifier = _recorded_verifier()
    with pytest.raises(SignatureRefused):
        check_bundle(bytes.fromhex(OTHER_SHA256), SUMS_BUNDLE.read_bytes(), verifier=verifier)
    with pytest.raises(SignatureRefused):
        check_bundle(hashlib.sha256(SUMS.read_bytes()).digest(), b"{not a bundle", verifier=verifier)
    with pytest.raises(SignatureRefused, match="larger"):
        check_bundle(b"\0" * 32, b" " * 1_000_001, verifier=verifier)


def _certificate(uri: str) -> Any:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.UTC)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(minutes=10))
            .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), critical=False)
            .sign(key, hashes.SHA256()))


@pytest.mark.parametrize("uri, accepted", [
    (KAZMA_IDENTITY, True),
    ("https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/tags/v0.22.0", True),
    ("https://github.com/Mubder/kazma/.github/workflows/ci.yml@refs/heads/main", False),
    ("https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/heads/feature", False),
    ("https://github.com/Mubder/kazma/.github/workflows/release.yml@refs/pull/7/merge", False),
    ("https://github.com/Mubder/kazma-fork/.github/workflows/release.yml@refs/heads/main", False),
    ("https://github.com/evil/kazma/.github/workflows/release.yml@refs/heads/main", False),
    (KAZMA_IDENTITY + "x", False),
])
def test_only_kazmas_release_workflow_is_the_signer(uri, accepted):
    pytest.importorskip("sigstore")
    from sigstore.errors import VerificationError

    from kazma_cli.release_signature import _ReleaseWorkflow

    cert = _certificate(uri)
    if accepted:
        _ReleaseWorkflow().verify(cert)
    else:
        with pytest.raises(VerificationError):
            _ReleaseWorkflow().verify(cert)


def test_without_the_network_the_copy_on_this_machine_checks(monkeypatch):
    pytest.importorskip("sigstore")
    from sigstore.errors import TUFError
    from sigstore.verify import Verifier

    from kazma_cli import release_signature

    asked: list[bool] = []

    def production(offline: bool = False) -> str:
        asked.append(offline)
        if not offline:
            raise TUFError("Failed to refresh TUF metadata")
        return "offline-verifier"

    monkeypatch.setattr(Verifier, "production", staticmethod(production))
    assert release_signature._production_verifier() == "offline-verifier"
    assert asked == [False, True]


def test_no_sigstore_package_refuses(monkeypatch):
    import sys

    from kazma_cli.release_signature import SignatureRefused, check_bundle

    monkeypatch.setitem(sys.modules, "sigstore.hashes", None)
    with pytest.raises(SignatureRefused, match="not installed"):
        check_bundle(b"\0" * 32, b"{}")


@pytest.mark.parametrize("version, signed", [
    ("0.10.9", False), ("0.11.0", False), ("0.12.0", False), ("v0.21.1", False),
    ("0.21.2", True), ("v0.21.2+gdeadbee", True), ("0.22.0", True), ("1.0", True),
    # Not MAJOR.MINOR.PATCH: never a way around the signature (2026-11-15).
    ("0.21.1+gabc1234", True), ("0.21+9.9", True), ("0+1.99.0", True), ("0.21.1rc1", True), ("", True),
    ("0.21.١", True),
])
def test_which_releases_must_be_signed(version, signed):
    from kazma_cli.release_signature import must_be_signed

    assert must_be_signed(version) is signed


# ── one reading of a release's version (2026-11-15) ──────────────────────

#: Tags crafted so the updater called them newer than 0.21.2 while the
#: signature rule called them older than SIGNED_FROM.
CRAFTED_TAGS = ("0.21+9.9", "0+1.99.0", "0.21+0.30", "0+1.30.0")


def _old_parse_version(version: str) -> tuple[int, ...]:
    """update.parse_version as it was: each dot piece's leading digits."""
    parts: list[int] = []
    for part in version.split("."):
        numeric = ""
        for ch in part:
            if ch.isdigit():
                numeric += ch
            else:
                break
        parts.append(int(numeric) if numeric else 0)
    return tuple(parts)


def _old_must_be_signed(version: str) -> bool:
    """release_signature.must_be_signed as it was: three pieces, after "+" cut."""
    import re

    parts: list[int] = []
    for piece in version.split("+", 1)[0].removeprefix("v").split(".")[:3]:
        digits = re.match(r"\d*", piece).group(0)
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts) >= (0, 21, 2)


@pytest.mark.parametrize("tag", CRAFTED_TAGS)
def test_a_crafted_tag_is_neither_newer_nor_unsigned(tag):
    from kazma_cli.release_signature import must_be_signed, release_version
    from kazma_cli.update import is_newer, parse_version

    assert is_newer("0.21.2", tag) is False
    assert must_be_signed(tag) is True
    for read in (parse_version, release_version):
        with pytest.raises(ValueError):
            read(tag)
    # The control: the two old readings disagreed about this very tag.
    assert _old_parse_version(tag) > _old_parse_version("0.21.2") and not _old_must_be_signed(tag)


def test_one_reading_of_versions() -> None:
    from kazma_cli.update import is_newer, parse_version

    assert parse_version("0.21.2") == parse_version("v0.21.2") == (0, 21, 2)
    assert is_newer("0.21.2+g4d37b2c", "0.22.0") and not is_newer("0.22.0", "0.21.9")
    assert not is_newer("0.21.2", "0.21.2") and not is_newer("not-a-version", "9.9.9")
    for bad in ("01.2.3", "1.2", "1.2.3.4", " 1.2.3", "1.2.3\n", "１.2.3"):
        with pytest.raises(ValueError):
            parse_version(bad)


# ── the updater, end to end, with a stand-in GitHub ──────────────────────


class _Resp:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"") -> None:
        self.status_code, self._body, self.content = status, body, content
        self.text = body if isinstance(body, str) else ""

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class SignedRelease:
    """A release whose "wheel" is real signed bytes (v0.11.0's SHA256SUMS)."""

    def __init__(self, version: str = "0.22.0", *, bundle: bytes | None = None, payload: bytes | None = None) -> None:
        self.version = version
        self.wheel_name = f"kazma-{version}-py3-none-any.whl"
        self.base = f"https://github.com/Mubder/kazma/releases/download/v{version}/"
        self.payload = SUMS.read_bytes() if payload is None else payload
        self.bundle = bundle
        self.seen: list[str] = []

    def release(self) -> dict[str, Any]:
        assets = [{"name": self.wheel_name, "browser_download_url": self.base + self.wheel_name,
                   "digest": f"sha256:{hashlib.sha256(self.payload).hexdigest()}"},
                  {"name": "SHA256SUMS", "browser_download_url": self.base + "SHA256SUMS"}]
        if self.bundle is not None:
            assets.append({"name": self.wheel_name + ".sigstore.json",
                           "browser_download_url": self.base + self.wheel_name + ".sigstore.json"})
        return {"tag_name": f"v{self.version}", "assets": assets}

    def get(self, url: str, **_kw: Any) -> _Resp:
        self.seen.append(url)
        if url.endswith("/releases/latest"):
            return _Resp(200, self.release())
        if url == self.base + "SHA256SUMS":
            return _Resp(200, f"{hashlib.sha256(self.payload).hexdigest()}  {self.wheel_name}\n")
        return _Resp(404)

    def stream(self, method: str, url: str, **_kw: Any) -> _Resp:
        self.seen.append(url)
        if url == self.base + self.wheel_name:
            return _Resp(200, content=self.payload)
        if self.bundle is not None and url == self.base + self.wheel_name + ".sigstore.json":
            return _Resp(200, content=self.bundle)
        return _Resp(404)

    def __call__(self, *_a: Any, **_kw: Any) -> SignedRelease:
        return self

    def __enter__(self) -> SignedRelease:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def updater(monkeypatch):
    """The updater with a stand-in GitHub, pip recorded, and the Sigstore root
    of trust the recorded one (no network)."""
    import httpx

    from kazma_cli import release_signature, update

    monkeypatch.setattr(update, "_is_server_running", lambda port=9090: False)
    monkeypatch.setattr(release_signature, "_production_verifier", _recorded_verifier)
    installed: list[list[str]] = []
    printed: list[str] = []

    def fake_pip(args: list[str], timeout: float = 0) -> subprocess.CompletedProcess[str]:
        installed.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update, "_run_pip", fake_pip)
    monkeypatch.setattr(update.console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))

    real_wheel_version = update._wheel_version

    def serve(fake: SignedRelease, *, stand_in: bool = True) -> tuple[SignedRelease, list[list[str]], list[str]]:
        monkeypatch.setattr(httpx, "Client", fake)
        if stand_in:
            # The signed material is v0.11.0's SHA256SUMS standing in for a
            # wheel: no METADATA to read, so its version is the release's.
            monkeypatch.setattr(update, "_wheel_version", lambda wheel: fake.version)
        else:
            monkeypatch.setattr(update, "_wheel_version", real_wheel_version)
        return fake, installed, printed

    return serve


def _wheel(version_inside: str) -> bytes:
    """A wheel's bytes whose METADATA says *version_inside*."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"kazma-{version_inside}.dist-info/METADATA",
                    f"Metadata-Version: 2.4\nName: kazma\nVersion: {version_inside}\n\nKazma.\n")
        zf.writestr("kazma_cli/__init__.py", "")
    return buf.getvalue()


def test_the_updater_installs_a_wheel_its_signature_proves(updater):
    pytest.importorskip("sigstore")
    from kazma_cli.update import do_pip_update, get_latest_release

    fake, installed, printed = updater(SignedRelease(bundle=SUMS_BUNDLE.read_bytes()))
    release = get_latest_release()
    assert release.problem == "" and release.bundle_url.endswith(".sigstore.json")
    assert do_pip_update(release) is True
    assert len(installed) == 1
    assert any(KAZMA_IDENTITY in line for line in printed)
    assert any(u.endswith(".sigstore.json") for u in fake.seen)


def test_the_updater_refuses_another_workflows_signature(updater):
    pytest.importorskip("sigstore")
    from kazma_cli.update import do_pip_update, get_latest_release

    # A wheel whose listed hash matches, with a valid signature from elsewhere.
    _fake, installed, printed = updater(SignedRelease(bundle=OTHER_BUNDLE.read_bytes()))
    assert do_pip_update(get_latest_release()) is False
    assert installed == []
    assert any("signature does not hold" in line for line in printed)


def test_the_updater_refuses_a_changed_wheel_even_when_the_release_lists_its_hash(updater):
    """SHA256SUMS and GitHub's digest describe the changed file (whoever changed
    the wheel changed them too); the signature does not."""
    pytest.importorskip("sigstore")
    from kazma_cli.update import do_pip_update, get_latest_release

    changed = SUMS.read_bytes() + b"\n"
    _fake, installed, _printed = updater(SignedRelease(bundle=SUMS_BUNDLE.read_bytes(), payload=changed))
    release = get_latest_release()
    assert release.problem == ""
    assert do_pip_update(release) is False
    assert installed == []


def test_a_new_release_without_a_signature_is_refused_and_downloads_nothing(updater):
    from kazma_cli.update import do_pip_update, get_latest_release

    fake, installed, _printed = updater(SignedRelease(bundle=None))
    release = get_latest_release()
    assert release.problem == "the release carries no Sigstore signature for its wheel"
    fake.seen.clear()
    assert do_pip_update(release) is False
    assert installed == [] and fake.seen == []


@pytest.mark.parametrize("version", ["0.10.0", "0.21.0", "0.21.1"])
def test_an_older_release_is_checked_by_its_hash_and_says_so(updater, version):
    """The releases published by hand, with no bundle, install on their checksums."""
    from kazma_cli.update import do_pip_update, get_latest_release

    _fake, installed, printed = updater(SignedRelease(version=version, bundle=None))
    release = get_latest_release()
    assert release.problem == ""
    assert do_pip_update(release) is True
    assert len(installed) == 1
    assert any("older than Kazma's signed releases" in line for line in printed)


def test_a_bundle_an_older_release_carries_is_still_verified(updater):
    """Before the cutoff a bundle is not required, but one that is there is
    checked: a valid one installs as signed, another workflow's is refused."""
    pytest.importorskip("sigstore")
    from kazma_cli.update import do_pip_update, get_latest_release

    _fake, installed, printed = updater(SignedRelease(version="0.21.1", bundle=SUMS_BUNDLE.read_bytes()))
    assert do_pip_update(get_latest_release()) is True
    assert any(KAZMA_IDENTITY in line for line in printed)
    _fake, installed, printed = updater(SignedRelease(version="0.21.1", bundle=OTHER_BUNDLE.read_bytes()))
    assert do_pip_update(get_latest_release()) is False
    assert len(installed) == 1
    assert any("signature does not hold" in line for line in printed)


def test_negative_control_the_v0_11_cutoff_refuses_every_release_published_by_hand(updater, monkeypatch):
    """With the cutoff at v0.11.0 (the first release the workflow signed),
    v0.21.1 -- like every release from v0.12.0, published without a bundle --
    is refused, and a wheel install could not update at all."""
    from kazma_cli import release_signature, update

    monkeypatch.setattr(release_signature, "SIGNED_FROM", (0, 11, 0))
    _fake, installed, _printed = updater(SignedRelease(version="0.21.1", bundle=None))
    release = update.get_latest_release()
    assert release.problem == "the release carries no Sigstore signature for its wheel"
    assert update.do_pip_update(release) is False
    assert installed == []


def test_negative_control_the_old_updater_installs_what_the_signature_refuses(updater, monkeypatch):
    """The updater before the check: no release needed a signature, and a
    bundle was never read. It installs both the unsigned release and the one
    another workflow signed."""
    from kazma_cli import update

    monkeypatch.setattr(update, "must_be_signed", lambda version: False)
    monkeypatch.setattr(update, "check_bundle", lambda sha256, bundle, verifier=None: "not checked")
    _fake, installed, _printed = updater(SignedRelease(bundle=None))
    assert update.do_pip_update(update.get_latest_release()) is True
    _fake, installed, _printed = updater(SignedRelease(bundle=OTHER_BUNDLE.read_bytes()))
    assert update.do_pip_update(update.get_latest_release()) is True
    assert len(installed) == 2


@pytest.mark.parametrize("tag", CRAFTED_TAGS)
def test_a_release_with_a_crafted_tag_is_refused_and_downloads_nothing(updater, tag):
    from kazma_cli.update import do_pip_update, get_latest_release

    fake, installed, _printed = updater(SignedRelease(version=tag, bundle=None))
    release = get_latest_release()
    assert release.version == "" and release.problem == "the release's tag is not a version (vMAJOR.MINOR.PATCH)"
    fake.seen.clear()
    assert do_pip_update(release) is False
    assert installed == [] and fake.seen == []


def test_negative_control_the_old_readings_installed_a_crafted_tag_unsigned(updater, monkeypatch):
    """The updater as it was: no tag check, the old "newer" and the old
    signature rule. A release tagged v0.21+9.9 with no signature installed."""
    from kazma_cli import update

    monkeypatch.setattr(update, "release_version", lambda version: (0, 0, 0))
    monkeypatch.setattr(update, "must_be_signed", _old_must_be_signed)
    monkeypatch.setattr(update, "parse_version", _old_parse_version)
    _fake, installed, printed = updater(SignedRelease(version="0.21+9.9", bundle=None))
    release = update.get_latest_release()
    assert release.problem == "" and update.is_newer("0.21.2", release.version)
    assert update.do_pip_update(release) is True and len(installed) == 1
    assert any("checked by its SHA-256 only" in line for line in printed)


def test_the_wheel_must_be_the_version_its_release_says(updater):
    """An older wheel served under a newer tag (a downgrade): its own METADATA
    says what it is. v0.21.1 needs no signature, so only the version holds it."""
    from kazma_cli.update import do_pip_update, get_latest_release

    _fake, installed, printed = updater(SignedRelease(version="0.21.1", bundle=None, payload=_wheel("0.11.0")),
                                        stand_in=False)
    assert do_pip_update(get_latest_release()) is False
    assert installed == [] and any("The wheel is version 0.11.0, not v0.21.1" in line for line in printed)
    _fake, installed, _printed = updater(SignedRelease(version="0.21.1", bundle=None, payload=_wheel("0.21.1")),
                                         stand_in=False)
    assert do_pip_update(get_latest_release()) is True and len(installed) == 1


def test_negative_control_without_the_version_check_the_old_wheel_installs(updater, monkeypatch):
    from kazma_cli import update

    _fake, installed, _printed = updater(SignedRelease(version="0.21.1", bundle=None, payload=_wheel("0.11.0")))
    # The stand-in reads the release's version, as no check at all would.
    assert update.do_pip_update(update.get_latest_release()) is True and len(installed) == 1


def test_a_wheel_not_named_for_its_tag_is_refused(updater):
    from kazma_cli.update import get_latest_release

    fake = SignedRelease(version="0.22.0", bundle=None)
    fake.wheel_name = "kazma-0.11.0-py3-none-any.whl"
    updater(fake)
    assert get_latest_release().problem == "the wheel is not named for v0.22.0"


def test_the_recorded_bundle_is_the_one_the_release_carries():
    """The fixture is v0.11.0's own: GitHub's digest of SHA256SUMS.sigstore.json."""
    data = SUMS_BUNDLE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == "2ba31ee8db85a247de4f4879dc97e0543607741fc1e2b900c2ec56c3a9957b99"
    assert json.loads(data)["mediaType"].startswith("application/vnd.dev.sigstore.bundle")
