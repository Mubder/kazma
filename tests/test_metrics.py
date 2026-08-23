"""Tests for Kazma metrics and observability."""

from __future__ import annotations


class TestMetricsModule:
    """Tests for the metrics module."""

    def test_metrics_graceful_degrade_without_prometheus(self):
        """Metrics should work even without prometheus-client installed."""
        # This test runs without prometheus-client
        from kazma_core.metrics import get_metrics_response, record_llm_call

        # get_metrics_response should return stub response
        body, status, headers = get_metrics_response()
        assert status == 200
        assert "content-type" in headers

        # record_llm_call should not crash
        record_llm_call("openai", "gpt-4", "success", 100, 50, 1000.0)

    def test_metrics_endpoint_format(self):
        """Metrics endpoint should return valid text format."""
        from kazma_core.metrics import get_metrics_response

        body, status, headers = get_metrics_response()
        # Body should be text/plain or text/plain; version=0.0.4...
        assert "plain" in headers["content-type"]

    def test_delivery_counters_noop_and_exposed(self):
        """Turn Delivery V2 counters: helpers never crash, names appear in
        the registry when prometheus-client is available."""
        from kazma_core.metrics import (
            get_metrics_response,
            record_delivery_event,
            record_delivery_replay,
        )

        # Must be no-crash regardless of prometheus availability.
        record_delivery_event(events=3, replayed=1, dropped=1)
        record_delivery_replay(replayed=2, gap=True)

        try:
            import prometheus_client  # noqa: F401

            has_prom = True
        except ImportError:
            has_prom = False

        body, status, _ = get_metrics_response()
        assert status == 200
        if has_prom:
            text = body.decode("utf-8")
            for name in (
                "kazma_delivery_events_total",
                "kazma_delivery_dropped_total",
                "kazma_delivery_replayed_total",
                "kazma_delivery_seq_gaps_total",
            ):
                assert name in text