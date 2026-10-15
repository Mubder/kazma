"""An iteration flag or parent allocator cannot certify deployment resources."""

import pytest
from kazma_core.documents import certification as cert


@pytest.mark.parametrize('smoke_status', ['PASS', 'FAIL'])
def test_extended_smoke_never_claims_multiday_or_metadata_qualification(tmp_path, monkeypatch, smoke_status):
    monkeypatch.setenv('KAZMA_DOCUMENTS_METADATA_BACKEND', 'postgres')
    monkeypatch.setattr(cert, 'certify_hostile_corpus', lambda _: cert.CertificationCheck('hostile_corpus', 'PASS', 'fixture'))
    monkeypatch.setattr(cert, '_capability_check', lambda _: cert.CertificationCheck('runtime_capabilities', 'PASS', 'fixture'))
    monkeypatch.setattr(cert, '_rollout_check', lambda: cert.CertificationCheck('safe_rollout', 'PASS', 'fixture'))
    async def smoke(*args, **kwargs):
        assert kwargs['iterations'] == 1
        return cert.CertificationCheck('bounded_performance_event_loop_resources', smoke_status, 'one iteration')
    monkeypatch.setattr(cert, 'run_performance_smoke', smoke)
    result = cert.run_document_certification(tmp_path, soak=True, soak_iterations=1)
    checks = {c['name']: c for c in result['checks']}
    assert checks['multi_day_soak']['status'] == 'NOT RUN'
    assert checks['metadata_multi_replica']['status'] == 'NOT RUN'
    assert checks['metadata_multi_replica']['metrics']['metadata_backend'] == 'unmeasured'
    assert checks['external_security_review']['status'] == 'NOT RUN'
    assert result['mode'] == 'extended-smoke'
    assert not result['default_ready']
    assert result['execution_profile']['scope'] == 'local_transient_parser'
    assert result['execution_profile']['filesystem_network_confinement_qualified'] is False


@pytest.mark.asyncio
async def test_native_resource_sampler_observes_actual_worker_tree(tmp_path):
    result = await cert.run_performance_smoke(tmp_path, iterations=1, duration_limit_seconds=60)
    assert result.status == 'PASS', result.detail
    metrics = result.metrics
    assert metrics['tracemalloc_scope'] == 'parent_python_allocations_only'
    assert metrics['resource_sampling_status'] == 'sampled'
    assert metrics['resource_sample_count'] > 1
    assert metrics['worker_processes_observed'] >= 1
    assert metrics['worker_rss_sampled_peak_bytes'] > 0
    assert metrics['tree_rss_sampled_peak_bytes'] > metrics['worker_rss_sampled_peak_bytes']


def test_rejected_fence_case_cannot_be_counted_as_verified_fencing(tmp_path, monkeypatch):
    from kazma_core.documents.errors import DocumentSandboxError
    def unavailable(*args, **kwargs):
        raise DocumentSandboxError('inert sandbox outage')
    monkeypatch.setattr(cert.DocumentService, 'read_transient_sync', unavailable)
    result = cert.certify_hostile_corpus(tmp_path)
    assert result.status == 'FAIL'
    assert 'unexpectedly rejected' in result.detail
    assert result.metrics['fenced'] == 0


def _container_report(**overrides):
    report = {"mode": "container", "ready": True, "image_code_matches": True,
              "image_current": True, "image_fallback": False,
              "hostile_document_profile": "container_broker"}
    report.update(overrides)
    return report


@pytest.mark.parametrize(
    ("overrides", "verdict"),
    [
        ({}, "PASS"),
        ({"ready": False}, "FAIL"),
        ({"image_code_matches": False}, "FAIL"),
        # The cases the old rule passed (Q04 defensive check C7, 2026-10-15):
        # a match not reported, an image older than its tag, a fallback build.
        ({"image_code_matches": None}, "FAIL"),
        ({"image_current": False}, "FAIL"),
        ({"image_fallback": True}, "FAIL"),
    ],
)
def test_the_container_profile_passes_only_with_an_image_known_to_run_this_code(
    monkeypatch, overrides, verdict
):
    monkeypatch.setattr("kazma_core.documents.worker_container.isolation_report",
                        lambda: _container_report(**overrides))
    check, _report = cert._isolation_check()
    assert check.status == verdict, (overrides, check)
