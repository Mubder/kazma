"""``kazma doctor`` — answer "what model will actually be used, and will it work?"

Written because that question had no answer short of reading kazma.log.

On 2026-09-16 a Telegram turn failed with Z.AI's
``{"code":"1211","message":"Unknown Model, please check the model code."}``.
Nothing in the product said which model had gone to which endpoint, so the only
route to the cause was grepping the log and finding this three-line sequence:

    Profile provider=deepseek model=deepseek-flash has no usable API key;
    using Z.AI/glm-5.3-flash which has one
    LLMProvider initialized: base_url=https://api.z.ai/... model=glm-5.3-flash
    [Supervisor] live client model=deepseek-flash

Three facts, each individually visible, that nobody could see together. That is
what this command prints.

Read-only: it opens no sockets and writes no settings.
"""

from __future__ import annotations

import json

from kazma_core.diagnostic_scope import read_only_diagnostic

OK = "ok"
WARN = "warn"
BAD = "bad"

_MARK = {OK: "[ ok ]", WARN: "[warn]", BAD: "[FAIL]"}


def _host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or url
    except Exception:  # noqa: BLE001
        return url


def _line(status: str, label: str, detail: str = "") -> tuple[str, str]:
    return status, f"{_MARK[status]} {label}" + (f"\n         {detail}" if detail else "")


def _diagnose_vault_pointer(ptr: str) -> str:
    """Say why a ``vault://`` pointer resolved to nothing, by asking the vault.

    Three different failures look identical from ConfigStore, which returns
    the default for all of them: the vault is off, the row is missing, or the
    row exists but this caller cannot see its scope.
    """
    name = ptr[len("vault://"):]
    from kazma_core.security.vault import get_vault

    vault = get_vault()
    if vault is None:
        return (
            f"the key is stored as {ptr} but the vault is DISABLED here "
            "(KAZMA_VAULT_KEY is not set), so the pointer resolves to nothing. "
            "Set KAZMA_VAULT_KEY in this install's .env and restart."
        )
    rows = vault.describe_secret(name)
    if not rows:
        return (
            f"the key is stored as {ptr} but this install's vault has NO row "
            "under that name — the secret was written into a different "
            "install's vault. Re-enter the key here (Settings -> Providers)."
        )
    scopes = ", ".join(sorted(str(r.get("tenant_id") or "global") for r in rows))
    if any(r.get("decrypts") is False for r in rows):
        return (
            f"the key is stored as {ptr} and the row EXISTS (scope: {scopes}) "
            "but will not decrypt with this install's KAZMA_VAULT_KEY — the "
            "vault key changed since it was written. Re-enter the key here "
            "(Settings -> Providers)."
        )
    return (
        f"the key is stored as {ptr}; the row exists and decrypts (scope: "
        f"{scopes}) yet the registry still read nothing. That is a Kazma bug, "
        "not a misconfiguration — please report it with this output."
    )


def _shared_store_lines(cs: object) -> list[tuple[str, str]]:
    """Name every other install recently booted against this Postgres store.

    The 2026-09-16 outage was two installs on one settings store, and this
    command gave different answers on the two boxes without saying why.
    Read-only: the server records boots (``check_shared_store_peers``); this
    only reads them, and never mints an install id.
    """
    from kazma_core.db import shared_store_peers as ssp

    try:
        me = ssp.install_id(create=False)
        peers: list[ssp.InstallRecord] = ssp.recent_peers(cs, me)
        _known, unknown = ssp.split_acknowledged(cs, peers)
    except ssp.store_errors() as exc:
        return [_line(WARN, "could not read which installs share this store", str(exc)[:160])]
    if not peers:
        return [_line(OK, "no other install has booted against this store recently")]
    if not unknown:
        return [_line(OK, f"shared with {len(peers)} acknowledged install(s)",
                      "; ".join(p.describe() for p in peers))]
    return [_line(
        WARN, f"this settings store is ALSO used by {len(unknown)} other install(s)",
        "; ".join(p.describe() for p in unknown)
        + f". Settings and vault:// pointers written by one are read by all. "
        f"Replicas on purpose: list their ids under {ssp.ACK_KEY}.",
    )]


def collect() -> list[tuple[str, str]]:
    """Return [(status, rendered_line)] — the whole report, no printing.

    Runs as a read-only diagnostic: the module docstring's promise that this
    command "writes no settings" is enforced at the stores, not trusted. It
    asks the registry to build a client, and the registry's own paths can
    write -- a lazy secret migration on read, a provider health stamp.
    """
    with read_only_diagnostic("kazma doctor"):
        return _collect()


def _collect() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    try:
        from kazma_core.config_store import get_config_store
        from kazma_core.model_registry_store import load_providers
        from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable, url_is_cloud
    except Exception as exc:  # noqa: BLE001
        return [_line(BAD, "could not import kazma_core", str(exc))]

    cs = get_config_store()

    # ── which store is actually live ────────────────────────────────────
    try:
        from kazma_core.db.backend import is_postgres

        backend = "postgres" if is_postgres() else "sqlite"
    except Exception:  # noqa: BLE001
        backend = "unknown"
    out.append(_line(
        OK, f"config store: {backend}",
        "a leftover kazma-data/settings.db is NOT read when postgres is live"
        if backend == "postgres" else "",
    ))
    if backend == "postgres":
        out.extend(_shared_store_lines(cs))

    # ── the model that will actually be used ────────────────────────────
    model = str(cs.get("registry.active_model") or cs.get("registry.active_chat_model") or "")
    pname = str(cs.get("registry.active_provider") or "")
    if not model or not pname:
        out.append(_line(BAD, "no active model/provider configured",
                         "Settings -> Models, pick a provider and a model"))
        return out

    provs = [p for p in load_providers(cs) if isinstance(p, dict)]
    prov = next((p for p in provs if str(p.get("name")) == pname), None)
    if prov is None:
        enabled = [str(p.get("name")) for p in provs
                   if str(p.get("enabled")).lower() == "true"]
        out.append(_line(BAD, f"active provider {pname!r} has no entry",
                         f"enabled providers: {enabled}"))
        return out

    base = str(prov.get("base_url") or "")
    out.append(_line(OK, f"configured: {model!r} via {pname!r}", f"endpoint: {base}"))

    # ── what the registry will ACTUALLY build ───────────────────────────
    #
    # Ask, do not re-derive. The registry resolves the key per model PROFILE
    # (_resolve_provider_config), not from the provider row, so an independent
    # re-implementation here drifts from it — and a doctor that disagrees with
    # the thing it is diagnosing is worse than no doctor. The first draft of
    # this command did exactly that: it reported "has a usable key" for the
    # provider whose profile the registry had already rejected.
    eff_url, eff_model = base, model
    try:
        from kazma_core.model_registry import get_model_registry

        client = get_model_registry().get_client(model)
        cfg = getattr(client, "config", None)
        eff_url = str(getattr(cfg, "base_url", "") or base)
        eff_model = str(getattr(cfg, "model", "") or model)
    except Exception as exc:  # noqa: BLE001
        out.append(_line(WARN, "could not ask the registry what it would use", str(exc)[:160]))

    substituted = _host(eff_url) != _host(base)
    if substituted:
        out.append(_line(
            BAD, f"the registry SUBSTITUTES {_host(eff_url)} for {_host(base)}",
            f"it will send {eff_model!r} to {eff_url}. This happens when the "
            f"configured provider has no usable API key — the registry quietly "
            f"switches to one that does. Fix the key for {pname!r} in "
            f"Settings -> Providers.",
        ))
    elif eff_model != model:
        out.append(_line(
            WARN, f"the registry resolves the model to {eff_model!r}, not {model!r}"))
    else:
        out.append(_line(OK, "the registry resolves to the configured pair"))

    # ── the key, and WHY it is unusable ─────────────────────────────────
    key = coerce_api_key(prov.get("api_key"))
    if url_is_cloud(base) and not key_is_usable(key):
        # Distinguish "never set" from "set, but this machine cannot open the
        # vault holding it". They need opposite fixes and look identical
        # otherwise: ConfigStore returns the default when a vault:// pointer
        # fails to decrypt, so the secret's absence is indistinguishable from
        # its being unreadable.
        #
        # "Unreadable" has more than one cause and they need opposite fixes,
        # so ask the vault which one it is instead of asserting. The first
        # draft asserted: it told the operator the secret lived in another
        # install's vault. It was in THIS vault, under tenant "default",
        # decrypting perfectly — invisible only because the caller had no
        # tenant context (2026-09-16, the 1211 incident). Confident wrong
        # advice cost more than a vague right answer would have.
        detail = (
            "the registry will silently substitute a provider that has one. "
            "Settings -> Providers, set a real key."
        )
        try:
            from kazma_core.model_registry_store import load_providers_unresolved

            raw = next(
                (p for p in load_providers_unresolved(cs)
                 if isinstance(p, dict) and str(p.get("name")) == pname),
                {},
            )
            ptr = str(raw.get("api_key") or "")
            if ptr.startswith("vault://"):
                detail = _diagnose_vault_pointer(ptr)
        except Exception:  # noqa: BLE001 — diagnosis must not fail the report
            pass
        out.append(_line(BAD, f"{pname!r} has NO USABLE API KEY", detail))
    else:
        out.append(_line(OK, f"{pname!r} has a usable key on its provider row"))

    # The pair that will actually go on the wire is the one worth checking.
    model, base = eff_model, eff_url

    # ── does this provider actually offer this model ────────────────────
    disc = cs.get("registry.discovered_models")
    if isinstance(disc, str):
        try:
            disc = json.loads(disc or "{}")
        except Exception:  # noqa: BLE001
            disc = {}
    _cat_owner = next((str(p.get("name")) for p in provs
                      if _host(str(p.get("base_url") or "")) == _host(base)), pname)
    catalog = list((disc or {}).get(_cat_owner, []) or [])
    if not catalog:
        out.append(_line(
            WARN, f"no discovered models for {_cat_owner!r}",
            "Settings -> Models -> Discover. Without a catalog Kazma cannot "
            "tell a good model id from a bad one, and the spelling guard is a "
            "no-op.",
        ))
    elif model in catalog:
        # Name the provider that actually OWNS this endpoint, not the
        # configured one — after a substitution they differ, and printing
        # "'deepseek' offers 'glm-5.3-flash'" is nonsense that undermines the
        # whole report.
        out.append(_line(OK, f"{_cat_owner!r} offers {model!r}",
                         f"{len(catalog)} models known"))
    else:
        bare = model.split("/", 1)[1] if "/" in model else None
        if bare and bare in catalog:
            out.append(_line(
                BAD, f"{model!r} is an aggregator spelling for this endpoint",
                f"{_host(base)} serves {bare!r} — set the active model to that, "
                f"or switch to the provider that uses {model!r}",
            ))
        else:
            near = [m for m in catalog if model.split("/")[-1][:4] in m][:6]
            out.append(_line(
                BAD, f"{_cat_owner!r} does not offer {model!r}",
                f"closest: {near or catalog[:6]}",
            ))

    # ── duplicate endpoints: 'which provider am I on?' ──────────────────
    by_host: dict[str, list[str]] = {}
    for p in provs:
        u = str(p.get("base_url") or "")
        if u and str(p.get("enabled")).lower() == "true":
            by_host.setdefault(_host(u), []).append(str(p.get("name")))
    dupes = {h: n for h, n in by_host.items() if len(n) > 1}
    if dupes:
        out.append(_line(
            WARN, "two enabled providers share one endpoint",
            "; ".join(f"{h}: {names}" for h, names in dupes.items())
            + " — ambiguous which one a turn resolves to",
        ))

    return out


def run(argv: list[str] | None = None) -> int:
    """Print the report. Exit 1 if anything is broken, 0 otherwise."""
    rows = collect()
    print("\nKazma doctor — provider & model resolution\n")
    for _status, rendered in rows:
        print(rendered)
    worst = {s for s, _ in rows}
    if BAD in worst:
        print("\nOne or more checks FAILED — the next turn will likely error.\n")
        return 1
    if WARN in worst:
        print("\nUsable, with warnings.\n")
        return 0
    print("\nAll checks passed.\n")
    return 0
