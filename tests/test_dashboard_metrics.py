"""Dashboard metrics must expose numeric totals (not pre-formatted strings)."""

from __future__ import annotations

from kazma_ui.dashboard import _format_uptime, _get_metrics


def test_get_metrics_returns_numbers() -> None:
    m = _get_metrics()
    assert isinstance(m["total_cost"], float)
    assert isinstance(m["total_tokens"], int)
    assert isinstance(m["total_llm_calls"], int)
    assert isinstance(m["total_tool_calls"], int)
    assert isinstance(m["total_traces"], int)
    assert isinstance(m["uptime_seconds"], float)
    assert isinstance(m["uptime"], str)
    # Must not be pre-formatted with $ or thousands separators
    assert not str(m["total_cost"]).startswith("$")
    assert "," not in str(m["total_tokens"])


def test_format_uptime() -> None:
    assert _format_uptime(30) == "30s"
    assert _format_uptime(90) == "1m"
    assert "h" in _format_uptime(3700)


def test_parse_metric_number_js_contract() -> None:
    """Mirror dashboard.js parseMetricNumber rules in Python for documentation."""

    def parse(value: object) -> float:
        import math

        if value is None or value == "":
            return 0.0
        if isinstance(value, (int, float)):
            return float(value) if math.isfinite(float(value)) else 0.0
        s = str(value).replace("$", "").replace(",", "").replace(" ", "").strip()
        try:
            n = float(s)
            return n if math.isfinite(n) else 0.0
        except ValueError:
            return 0.0

    assert parse("$0.0043") == 0.0043
    assert parse("27,127") == 27127.0
    assert parse(42) == 42.0
    assert parse("NaN") == 0.0
