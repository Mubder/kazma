"""A release wheel is installed only with a Sigstore signature from Kazma's
release workflow (2026-11, supply chain).

``kazma update`` on a wheel install checked the wheel against the release's
``SHA256SUMS`` and GitHub's asset digest. Both live in the same release: an
account able to change its files could change all three together. The release
workflow (``.github/workflows/release.yml``) also signs every file with
Sigstore, keyless: the signing certificate names the GitHub workflow run that
made it, and the signature is recorded in Sigstore's transparency log. Nobody
can make one for Kazma's release workflow without running it in this
repository.

:func:`check_bundle` verifies a wheel's bundle (``<wheel>.sigstore.json``)
with sigstore-python: the certificate chains to Sigstore's Fulcio at the time
the transparency log recorded it, the log entry and its inclusion proof hold,
the signature is over the wheel's SHA-256, and the certificate names

* the issuer ``https://token.actions.githubusercontent.com``,
* the source repository ``https://github.com/Mubder/kazma``, and
* the workflow ``.github/workflows/release.yml`` of that repository, run from
  ``main`` (the workflow is started by hand there: every bundle it made says
  ``@refs/heads/main``) or from a ``v*`` tag.

Releases from :data:`SIGNED_FROM` on must carry the bundle. An older one is
installed on its checksums alone, and ``kazma update`` says so -- unless it
carries a bundle, which is then verified like any other: a bundle is never
ignored.

Sigstore's own keys (its root of trust) come from its TUF repository, read
afresh at each check; when that cannot be reached the copy kept on this
machine is used (the one the last check read, else the one sigstore-python
ships). Without the network the check still runs; it is never skipped.

Standard library only at import: the updater must run while ``kazma_core``
may not import, and sigstore is imported where a check is made.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SIGNED_FROM", "SignatureRefused", "check_bundle", "must_be_signed"]

#: From this release on a release's wheel must carry a Sigstore bundle: the
#: first after v0.21.1. The workflow signed v0.11.0, but v0.12.0 to v0.21.1
#: were published by hand with none, and a cutoff before them would leave
#: every wheel install unable to update. The next release must be published
#: through .github/workflows/release.yml, which signs it.
SIGNED_FROM = (0, 21, 2)

OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SOURCE_REPOSITORY = "https://github.com/Mubder/kazma"
#: The certificate identity of a run of Kazma's release workflow.
RELEASE_WORKFLOW = re.compile(
    r"https://github\.com/Mubder/kazma/\.github/workflows/release\.yml"
    r"@refs/(?:heads/main|tags/v[0-9][0-9A-Za-z.+-]*)"
)

#: A bundle is a few kilobytes; anything larger is not one.
MAX_BUNDLE_BYTES = 1_000_000


class SignatureRefused(Exception):
    """The bundle does not prove the wheel was made by Kazma's release workflow."""


def must_be_signed(version: str) -> bool:
    """Whether release *version* must carry a Sigstore bundle for its wheel."""
    parts: list[int] = []
    for piece in version.split("+", 1)[0].removeprefix("v").split(".")[:3]:
        digits = re.match(r"\d*", piece).group(0)
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts) >= SIGNED_FROM


class _ReleaseWorkflow:
    """The certificate's identity is a run of Kazma's release workflow."""

    def verify(self, cert: Any) -> None:
        from cryptography import x509
        from sigstore.errors import VerificationError

        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound as exc:
            raise VerificationError("the certificate names no identity") from exc
        uris = san.get_values_for_type(x509.UniformResourceIdentifier)
        if len(uris) != 1 or not RELEASE_WORKFLOW.fullmatch(uris[0]):
            raise VerificationError(f"signed by {', '.join(uris) or 'no workflow'}, not Kazma's release workflow")


def _policy() -> Any:
    from sigstore.verify import policy

    return policy.AllOf([
        policy.OIDCIssuerV2(OIDC_ISSUER),
        policy.OIDCSourceRepositoryURI(SOURCE_REPOSITORY),
        _ReleaseWorkflow(),
    ])


def _production_verifier() -> Any:
    """A verifier on Sigstore's production root of trust: read afresh from its
    TUF repository, else the copy on this machine (never no check)."""
    from sigstore.errors import TUFError
    from sigstore.verify import Verifier

    try:
        return Verifier.production()
    except (TUFError, OSError) as exc:
        logger.warning("Sigstore's root of trust could not be refreshed (%s); "
                       "checking with the copy on this machine", exc)
        return Verifier.production(offline=True)


def check_bundle(sha256: bytes, bundle_json: bytes, *, verifier: Any | None = None) -> str:
    """Verify *bundle_json* for the file whose SHA-256 is *sha256*; return the
    identity that signed it. Raises :class:`SignatureRefused` with the reason.

    *verifier* is a sigstore ``Verifier``; tests pass one on a recorded root
    of trust (no network).
    """
    if len(bundle_json) > MAX_BUNDLE_BYTES:
        raise SignatureRefused("the signature bundle is larger than any Sigstore bundle")
    try:
        from sigstore.errors import Error as SigstoreError
        from sigstore.hashes import Hashed
        from sigstore.models import Bundle
        from sigstore_models.common.v1 import HashAlgorithm
    except ImportError as exc:
        raise SignatureRefused(
            f"the sigstore package is not installed here ({exc}), so the signature cannot be checked"
        ) from exc
    try:
        bundle = Bundle.from_json(bundle_json)
        checker = verifier if verifier is not None else _production_verifier()
        checker.verify_artifact(
            Hashed(algorithm=HashAlgorithm.SHA2_256, digest=sha256), bundle, _policy())
    except (SigstoreError, ValueError) as exc:
        raise SignatureRefused(str(exc) or type(exc).__name__) from exc
    from cryptography import x509

    san = bundle.signing_certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return ", ".join(san.get_values_for_type(x509.UniformResourceIdentifier))
