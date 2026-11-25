"""Where each provider's accounts live, what its keys look like, and what its
sign-in needs: the data behind the guided setup (Settings -> Providers).

The catalog (:mod:`kazma_core.providers`) gave each provider one address. Most
have several: Qwen one per region and one per workspace, Azure one per
resource, Bedrock one per AWS region, OpenAI and xAI one per data region. The
owner typed theirs by hand (2026-11-22), and three Qwen rows came of it.

Data only, no I/O. ``kazma_core.providers.catalog`` serves it with each
provider; ``kazma_core.provider_setup`` checks what was typed against it. The
words (region names, field names, hints) are the pages' catalog's, by id.

Addresses: OpenAI's, Alibaba's, Moonshot's, MiniMax's, SiliconFlow's and Z.AI's
were checked against the vendors' own pages on 2026-11-26; Azure's and
Cloudflare's are the patterns their documentation gives. The FIRST is the
preset's own address, so a provider set up before this keeps its row and its
place in the list. A region no vendor page lists is not here: the custom
address takes it.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["ENDPOINTS", "FIELDS", "FORM_FIELDS", "GROUPS", "KEY_PAGES", "KEY_SHAPES", "REGIONS", "endpoint_fields", "group_of",
           "key_page_for", "setup_data", "vendor_of"]

#: The groups the setup lists providers under, in order.
GROUPS: tuple[str, ...] = ("api", "host", "platform", "local", "other")

#: Providers that serve other makers' models.
_HOSTS = frozenset({
    "openrouter", "groq", "cerebras", "together", "fireworks", "deepinfra", "huggingface", "nvidia",
    "sambanova", "siliconflow", "novita", "hyperbolic", "cloudflare",
})


def group_of(preset_id: str, meta: dict[str, Any]) -> str:
    """The group *preset_id* is listed under (:data:`GROUPS`)."""
    if preset_id == "custom":
        return "other"
    if meta.get("local"):
        return "local"
    if meta.get("form") in ("azure", "bedrock"):
        return "platform"
    return "host" if preset_id in _HOSTS else "api"


#: A value a person types into an address or a row, by id: the pattern it must
#: match whole, and an example. ``options`` names a list in :data:`REGIONS`.
#: Every pattern is one bounded run of one class, or such runs joined by a
#: literal that is not in the class (linear: §69).
FIELDS: dict[str, dict[str, str]] = {
    "account_id": {"pattern": r"[0-9a-fA-F]{32}", "example": "0123456789abcdef0123456789abcdef"},
    "api_version": {"pattern": r"20\d{2}-\d{2}-\d{2}(?:-preview)?|preview|latest|v1", "example": "2024-10-21"},
    "aws_access_key_id": {"pattern": r"[A-Z0-9]{16,128}", "example": "AKIA................"},
    "location": {"pattern": r"[a-z]{2,20}(?:-[a-z]{2,20}\d{1,2})?", "example": "us-central1", "options": "vertex"},
    "project_id": {"pattern": r"(?:[a-z0-9.\-]{1,60}:)?[a-z][a-z0-9\-]{4,28}[a-z0-9]", "example": "my-project-123"},
    "region": {"pattern": r"[a-z]{2}(?:-[a-z]{2,12}){1,3}-\d", "example": "us-east-1", "options": "bedrock"},
    "resource": {"pattern": r"[A-Za-z0-9][A-Za-z0-9\-]{0,62}", "example": "my-resource"},
    "workspace_id": {"pattern": r"[A-Za-z0-9][A-Za-z0-9\-]{1,62}", "example": "llm-abc123"},
    "workspace_region": {
        "pattern": r"[a-z]{2}-[a-z]{4,12}(?:-\d)?", "example": "ap-southeast-1", "options": "qwen_workspace"},
}

#: The regions a field offers (any value its pattern takes may be typed).
REGIONS: dict[str, tuple[str, ...]] = {
    "bedrock": (
        "us-east-1", "us-east-2", "us-west-2", "ca-central-1", "sa-east-1", "eu-central-1", "eu-west-1",
        "eu-west-2", "eu-west-3", "eu-north-1", "eu-south-1", "me-central-1", "me-south-1", "ap-south-1",
        "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "ap-northeast-2", "us-gov-west-1",
    ),
    "vertex": (
        "global", "us-central1", "us-east1", "us-east4", "us-west1", "europe-west1", "europe-west3",
        "europe-west4", "me-central1", "me-central2", "me-west1", "asia-northeast1", "asia-south1",
        "asia-southeast1", "australia-southeast1",
    ),
    "qwen_workspace": (
        "ap-southeast-1", "us-east-1", "eu-central-1", "ap-northeast-1", "cn-beijing", "cn-hongkong",
    ),
}

#: What each form asks beside its key: (field, required). An address field
#: is one of the endpoint's own (``{field}`` in its address).
FORM_FIELDS: dict[str, tuple[tuple[str, bool], ...]] = {
    "azure": (("api_version", False),),
    "bedrock": (("region", True),),
    "google": (("project_id", True), ("location", True)),  # Vertex AI only
    "key": (),
    "local": (),
}

#: Each provider's endpoints: ``(id, address)``, the id being the region's or
#: the plan's word in the pages' catalog (``settings.setup.endpoint.<id>``).
#: ``{field}`` is filled with what the person types (:data:`FIELDS`). A
#: provider with none has its preset's one address.
ENDPOINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai": (
        ("global", "https://api.openai.com/v1"), ("us", "https://us.api.openai.com/v1"),
        ("eu", "https://eu.api.openai.com/v1"), ("ae", "https://ae.api.openai.com/v1"),
        ("gb", "https://gb.api.openai.com/v1"), ("in", "https://in.api.openai.com/v1"),
        ("jp", "https://jp.api.openai.com/v1"), ("sg", "https://sg.api.openai.com/v1"),
        ("kr", "https://kr.api.openai.com/v1"), ("au", "https://au.api.openai.com/v1"),
        ("ca", "https://ca.api.openai.com/v1"),
    ),
    "xai": (
        ("global", "https://api.x.ai/v1"), ("us", "https://us.api.x.ai/v1"),
    ),
    "zai": (
        ("global", "https://api.z.ai/api/paas/v4"), ("coding_plan", "https://api.z.ai/api/coding/paas/v4"),
        ("china", "https://open.bigmodel.cn/api/paas/v4"),
    ),
    "moonshot": (("global", "https://api.moonshot.ai/v1"), ("china", "https://api.moonshot.cn/v1")),
    "minimax": (("global", "https://api.minimax.io/v1"), ("china", "https://api.minimax.cn/v1")),
    "siliconflow": (("global", "https://api.siliconflow.com/v1"), ("china", "https://api.siliconflow.cn/v1")),
    "dashscope": (
        ("singapore", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("us_virginia", "https://dashscope-us.aliyuncs.com/compatible-mode/v1"),
        ("china_beijing", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("hong_kong", "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1"),
        ("workspace", "https://{workspace_id}.{workspace_region}.maas.aliyuncs.com/compatible-mode/v1"),
    ),
    "cloudflare": (("account", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"),),
    "azure": (
        ("azure_public", "https://{resource}.openai.azure.com"),
        ("azure_foundry", "https://{resource}.cognitiveservices.azure.com"),
        ("azure_gov", "https://{resource}.openai.azure.us"),
        ("azure_china", "https://{resource}.openai.azure.cn"),
    ),
}

#: Where a key is made, when it is not the page the preset names.
KEY_PAGES: dict[str, dict[str, str]] = {
    "moonshot": {"china": "https://platform.moonshot.cn/console/api-keys"},
    "siliconflow": {"china": "https://cloud.siliconflow.cn/account/ak"},
    "zai": {"china": "https://open.bigmodel.cn/usercenter/apikeys"},
    "minimax": {"china": "https://platform.minimax.cn"},
    "dashscope": {"china_beijing": "https://bailian.console.aliyun.com/?tab=model#/api-key"},
    "azure": {"azure_public": "https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub"},
    "bedrock": {"": "https://console.aws.amazon.com/iam/home#/security_credentials"},
    "xai": {"": "https://console.x.ai/team/default/api-keys"},
    "cerebras": {"": "https://cloud.cerebras.ai/platform/apikeys"},
    "nvidia": {"": "https://build.nvidia.com/settings/api-keys"},
}

#: A key's shape, longest first, and the providers whose keys look so. One
#: provider: the page says which it is. Several: it lists them. Matched in
#: the page, so a pasted key is sent nowhere to be recognised.
KEY_SHAPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"^sk-or-", ("openrouter",)),
    (r"^sk-ant-", ("anthropic",)),
    (r"^sk-(?:proj|svcacct)-", ("openai",)),
    (r"^sk-[0-9a-f]{32}$", ("deepseek", "dashscope")),
    (r"^sk-cp-", ("minimax",)),
    (r"^gsk_", ("groq",)),
    (r"^xai-", ("xai",)),
    (r"^pplx-", ("perplexity",)),
    (r"^AIza", ("google",)),
    (r"^AQ\.", ("google",)),
    (r"^hf_", ("huggingface",)),
    (r"^nvapi-", ("nvidia",)),
    (r"^csk-", ("cerebras",)),
    (r"^fw_", ("fireworks",)),
    (r"^tgp_v1_", ("together",)),
    (r"^(?:AKIA|ASIA)[A-Z0-9]{16}$", ("bedrock",)),
    (r"^[0-9a-f]{32}\.[A-Za-z0-9]{16}$", ("zai",)),
    (r"^eyJ", ("minimax", "hyperbolic")),
    (r"^sk_", ("novita",)),
    (r"^sk-", ("openai", "deepseek", "dashscope", "moonshot", "siliconflow")),
)

_PLACEHOLDER = re.compile(r"\{([a-z_]{1,40})\}")


def endpoint_fields(address: str) -> list[str]:
    """The fields *address* is filled with, in order."""
    return _PLACEHOLDER.findall(str(address or ""))


#: Domains that are one vendor's under any host, beside the addresses above.
_VENDOR_DOMAINS: tuple[tuple[str, str], ...] = (("qwencloudapi.com", "dashscope"), ("aliyuncs.com", "dashscope"))


def _hosts() -> list[tuple[re.Pattern[str], str]]:
    """Every catalog address's host, as ``(pattern, preset)``: a template's
    fields match one label each."""
    from urllib.parse import urlsplit

    from kazma_core.providers import PROVIDER_PRESETS

    out = []
    for pid, meta in PROVIDER_PRESETS.items():
        if meta.get("local") or pid == "custom":
            continue
        for url in {str(meta.get("base_url") or ""), *(u for _e, u in ENDPOINTS.get(pid, ()))}:
            host = urlsplit(_PLACEHOLDER.sub("0field0", url)).hostname
            if host:
                out.append((re.compile(re.escape(host).replace("0field0", "[a-z0-9-]{1,63}")), pid))
    return out


def vendor_of(preset: str, name: str, base_url: str) -> str:
    """Whose service a row or client is, in whatever region: the preset it was
    made from (its own name when that is one), else the catalog provider its
    address is of, else its host. No I/O."""
    from urllib.parse import urlsplit

    from kazma_core.providers import PROVIDER_PRESETS

    for candidate in (preset, name):
        pid = str(candidate or "").strip().lower()
        if pid in PROVIDER_PRESETS and pid != "custom":
            return pid
    try:
        parts = urlsplit(str(base_url or ""))
        host = (parts.hostname or "").lower()
    except ValueError:
        return str(name or "").strip().lower()
    for pattern, pid in _hosts():
        if pattern.fullmatch(host):
            return pid
    for domain, pid in _VENDOR_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return pid
    return (parts.netloc.rpartition("@")[2].lower() if host else "") or str(name or "").strip().lower()


def key_page_for(preset_id: str, base_url: str, meta: dict[str, Any]) -> str:
    """Where a key for a row of *preset_id* at *base_url* is made."""
    pages = KEY_PAGES.get(preset_id, {})
    here = str(base_url or "").rstrip("/").lower()
    for eid, url in ENDPOINTS.get(preset_id, ()):
        if eid in pages and url.lower() == here:
            return pages[eid]
    return pages.get("") or str(meta.get("docs") or "")


def setup_data(preset_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """What the setup shows for one provider, beside the catalog entry:
    its group, endpoints (each with its fields and key page), the fields its
    form asks, and where a key is made."""
    pages = KEY_PAGES.get(preset_id, {})
    own_page = pages.get("") or str(meta.get("docs") or "")
    return {
        "group": group_of(preset_id, meta),
        "key_page": own_page,
        "endpoints": [
            {"id": eid, "base_url": url, "fields": endpoint_fields(url), "key_page": pages.get(eid) or own_page}
            for eid, url in ENDPOINTS.get(preset_id, ())
        ],
        "form_fields": [{"id": f, "required": required}
                        for f, required in FORM_FIELDS.get(str(meta.get("form") or "key"), ())],
    }
