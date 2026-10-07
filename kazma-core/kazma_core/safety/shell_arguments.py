"""Per-binary short-option values for the post-approval path policy."""
from __future__ import annotations

_SHORT_VALUES = {
    "git": "Cc", "grep": "femABCDd", "jq": "fL", "zip": "bO", "unzip": "d",
    "tar": "fCIT",
}
_TAR_SWITCHES = frozenset("ctvzjJaO")
_SHORT_SWITCHES = {
    "git": frozenset("pP"),
    "grep": frozenset("EFGPivwxclLnoHhqsIraRUzZ"),
    "jq": frozenset("nrscRMCaSje"),
    "zip": frozenset("rqv09XjD"),
    "unzip": frozenset("pqlnvfojC"),
}


def short_option_values(binary: str, argument: str) -> list[tuple[str, str]]:
    """Decode attached values; refuse ambiguous tar clusters and old-style flags.

    A short value consumes the rest of its token, just as the binary does.
    For tar, the permitted operation is create/list with explicit dash options;
    extraction, dereferencing and file-list indirection require a native tool.
    """
    if not argument.startswith("-") or argument.startswith("--"):
        return []
    values = _SHORT_VALUES.get(binary, "")
    switches = _TAR_SWITCHES if binary == "tar" else _SHORT_SWITCHES.get(binary)
    if switches is None:
        return []
    for index, flag in enumerate(argument[1:], 1):
        if flag in values:
            return [("-" + flag, argument[index + 1:])]
        if flag not in switches:
            raise ValueError(f"Unsupported {binary} short option '-{flag}'; use explicit options or a native tool")
    return []
