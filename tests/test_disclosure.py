"""Tests for the VulnerabilityDisclosure module."""

from __future__ import annotations

import pytest
from kazma_core.security.disclosure import (
    Advisory,
    DisclosureReport,
    StatusTransition,
    VulnerabilityDisclosure,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def disclosure(tmp_path) -> VulnerabilityDisclosure:
    """Fresh VulnerabilityDisclosure instance with a temp DB."""
    return VulnerabilityDisclosure(db_path=str(tmp_path / "disclosure.db"))


@pytest.fixture
async def submitted_report(disclosure: VulnerabilityDisclosure) -> str:
    """Submit a report and return its ID."""
    return await disclosure.submit_report({
        "title": "XSS in skill loader",
        "description": "The skill loader does not sanitise user input.",
        "severity": "high",
        "steps_to_reproduce": "Load a crafted skill manifest.",
        "impact": "Arbitrary JS execution in the browser.",
        "reporter_email": "researcher@example.com",
    })


@pytest.fixture
async def full_lifecycle_report(disclosure: VulnerabilityDisclosure) -> str:
    """Submit and advance a report through the full lifecycle."""
    report_id = await disclosure.submit_report({
        "title": "SQL Injection",
        "description": "Raw query in search endpoint.",
        "severity": "critical",
        "steps_to_reproduce": "Send payload to /search.",
        "impact": "Database compromise.",
        "reporter_email": "security@example.com",
    })
    await disclosure.acknowledge(report_id)
    await disclosure.update_status(report_id, "investigating")
    await disclosure.update_status(report_id, "confirmed")
    await disclosure.update_status(report_id, "patched")
    return report_id


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_disclosure_report_fields(self):
        report = DisclosureReport(
            id="VR-001",
            title="Test",
            description="Desc",
            severity="high",
            steps_to_reproduce="steps",
            impact="impact",
            reporter_email="a@b.com",
            status="submitted",
            created_at="2025-01-01T00:00:00",
        )
        assert report.id == "VR-001"
        assert report.acknowledged_at is None

    def test_status_transition_fields(self):
        st = StatusTransition(
            report_id="VR-001",
            old_status="submitted",
            new_status="acknowledged",
            changed_at="2025-01-01T00:00:00",
            notes="Test note",
        )
        assert st.old_status == "submitted"
        assert st.notes == "Test note"

    def test_advisory_fields(self):
        adv = Advisory(
            report_id="VR-001",
            cve_id="CVE-2025-1234567",
            content="Advisory content",
            published_at="2025-01-01T00:00:00",
        )
        assert adv.cve_id.startswith("CVE-")


# ---------------------------------------------------------------------------
# Report submission and storage
# ---------------------------------------------------------------------------

class TestSubmitReport:
    @pytest.mark.asyncio
    async def test_submit_returns_id(self, disclosure: VulnerabilityDisclosure):
        rid = await disclosure.submit_report({
            "title": "Test vuln",
            "description": "Description",
            "severity": "medium",
            "steps_to_reproduce": "Steps",
            "impact": "Impact",
            "reporter_email": "test@example.com",
        })
        assert rid.startswith("VR-")
        assert len(rid) > 3

    @pytest.mark.asyncio
    async def test_submit_stores_report(self, disclosure: VulnerabilityDisclosure):
        rid = await disclosure.submit_report({
            "title": "Stored vuln",
            "description": "Stored desc",
            "severity": "low",
            "steps_to_reproduce": "Steps",
            "impact": "Impact",
            "reporter_email": "store@example.com",
        })
        report = await disclosure.get_report(rid)
        assert report["title"] == "Stored vuln"
        assert report["severity"] == "low"
        assert report["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_submit_creates_initial_history(self, disclosure: VulnerabilityDisclosure):
        rid = await disclosure.submit_report({
            "title": "History test",
            "description": "Desc",
            "severity": "info",
            "steps_to_reproduce": "Steps",
            "impact": "Impact",
            "reporter_email": "hist@example.com",
        })
        report = await disclosure.get_report(rid)
        history = report["status_history"]
        assert len(history) == 1
        assert history[0]["new_status"] == "submitted"


# ---------------------------------------------------------------------------
# Acknowledgment flow
# ---------------------------------------------------------------------------

class TestAcknowledge:
    @pytest.mark.asyncio
    async def test_acknowledge_transitions_status(self, disclosure: VulnerabilityDisclosure, submitted_report: str):
        result = await disclosure.acknowledge(submitted_report)
        assert result["report_id"] == submitted_report
        assert "acknowledged_at" in result
        assert "next_steps" in result

        report = await disclosure.get_report(submitted_report)
        assert report["status"] == "acknowledged"
        assert report["acknowledged_at"] is not None

    @pytest.mark.asyncio
    async def test_acknowledge_not_found(self, disclosure: VulnerabilityDisclosure):
        with pytest.raises(ValueError, match="not found"):
            await disclosure.acknowledge("VR-NONEXISTENT")

    @pytest.mark.asyncio
    async def test_acknowledge_wrong_status(self, disclosure: VulnerabilityDisclosure, submitted_report: str):
        """Cannot acknowledge an already-acknowledged report."""
        await disclosure.acknowledge(submitted_report)
        with pytest.raises(ValueError, match="must be 'submitted'"):
            await disclosure.acknowledge(submitted_report)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, disclosure: VulnerabilityDisclosure):
        """Test submitted -> acknowledged -> investigating -> confirmed -> patched -> closed."""
        rid = await disclosure.submit_report({
            "title": "Full lifecycle",
            "description": "Test",
            "severity": "high",
            "steps_to_reproduce": "Steps",
            "impact": "Impact",
            "reporter_email": "test@example.com",
        })
        await disclosure.acknowledge(rid)
        await disclosure.update_status(rid, "investigating")
        await disclosure.update_status(rid, "confirmed")
        await disclosure.update_status(rid, "patched")
        await disclosure.update_status(rid, "closed")

        report = await disclosure.get_report(rid)
        assert report["status"] == "closed"
        assert report["patched_at"] is not None
        assert len(report["status_history"]) == 6  # initial + 5 transitions

    @pytest.mark.asyncio
    async def test_invalid_transition_rejected(self, disclosure: VulnerabilityDisclosure, submitted_report: str):
        """Cannot jump from submitted to confirmed."""
        with pytest.raises(ValueError, match="Invalid transition"):
            await disclosure.update_status(submitted_report, "confirmed")

    @pytest.mark.asyncio
    async def test_cannot_transition_from_closed(self, disclosure: VulnerabilityDisclosure):
        """Closed reports cannot be further transitioned."""
        rid = await disclosure.submit_report({
            "title": "Closed",
            "description": "Test",
            "severity": "low",
            "steps_to_reproduce": "Steps",
            "impact": "Impact",
            "reporter_email": "test@example.com",
        })
        await disclosure.acknowledge(rid)
        await disclosure.update_status(rid, "investigating")
        await disclosure.update_status(rid, "confirmed")
        await disclosure.update_status(rid, "patched")
        await disclosure.update_status(rid, "closed")

        with pytest.raises(ValueError, match="Invalid transition"):
            await disclosure.update_status(rid, "investigating")

    @pytest.mark.asyncio
    async def test_can_close_from_any_open_state(self, disclosure: VulnerabilityDisclosure):
        """Closing is allowed from any non-closed status."""
        for status in ["acknowledged", "investigating", "confirmed"]:
            disc = VulnerabilityDisclosure(db_path=str(disclosure._db_path.parent / f"close_{status}.db"))
            rid = await disc.submit_report({
                "title": f"Close from {status}",
                "description": "Test",
                "severity": "low",
                "steps_to_reproduce": "Steps",
                "impact": "Impact",
                "reporter_email": "test@example.com",
            })
            await disc.acknowledge(rid)
            if status != "acknowledged":
                next_status = "investigating" if status == "acknowledged" else status
                if status == "investigating":
                    await disc.update_status(rid, "investigating")
                elif status == "confirmed":
                    await disc.update_status(rid, "investigating")
                    await disc.update_status(rid, "confirmed")
            await disc.update_status(rid, "closed")
            report = await disc.get_report(rid)
            assert report["status"] == "closed"

    @pytest.mark.asyncio
    async def test_update_status_not_found(self, disclosure: VulnerabilityDisclosure):
        with pytest.raises(ValueError, match="not found"):
            await disclosure.update_status("VR-FAKE", "closed")


# ---------------------------------------------------------------------------
# Report retrieval
# ---------------------------------------------------------------------------

class TestGetReport:
    @pytest.mark.asyncio
    async def test_get_report_with_history(self, disclosure: VulnerabilityDisclosure, submitted_report: str):
        report = await disclosure.get_report(submitted_report)
        assert report["id"] == submitted_report
        assert "status_history" in report
        assert isinstance(report["status_history"], list)

    @pytest.mark.asyncio
    async def test_get_report_not_found(self, disclosure: VulnerabilityDisclosure):
        with pytest.raises(ValueError, match="not found"):
            await disclosure.get_report("VR-NOPE")


# ---------------------------------------------------------------------------
# Report listing with filters
# ---------------------------------------------------------------------------

class TestListReports:
    @pytest.mark.asyncio
    async def test_list_all(self, disclosure: VulnerabilityDisclosure):
        await disclosure.submit_report({
            "title": "A", "description": "D", "severity": "high",
            "steps_to_reproduce": "S", "impact": "I", "reporter_email": "a@b.com",
        })
        await disclosure.submit_report({
            "title": "B", "description": "D", "severity": "low",
            "steps_to_reproduce": "S", "impact": "I", "reporter_email": "a@b.com",
        })
        reports = await disclosure.list_reports()
        assert len(reports) == 2

    @pytest.mark.asyncio
    async def test_list_filtered_by_status(self, disclosure: VulnerabilityDisclosure):
        rid1 = await disclosure.submit_report({
            "title": "A", "description": "D", "severity": "high",
            "steps_to_reproduce": "S", "impact": "I", "reporter_email": "a@b.com",
        })
        await disclosure.submit_report({
            "title": "B", "description": "D", "severity": "low",
            "steps_to_reproduce": "S", "impact": "I", "reporter_email": "a@b.com",
        })
        await disclosure.acknowledge(rid1)

        submitted = await disclosure.list_reports(status="submitted")
        assert len(submitted) == 1
        assert submitted[0]["title"] == "B"

        acknowledged = await disclosure.list_reports(status="acknowledged")
        assert len(acknowledged) == 1
        assert acknowledged[0]["title"] == "A"

    @pytest.mark.asyncio
    async def test_list_empty(self, disclosure: VulnerabilityDisclosure):
        reports = await disclosure.list_reports()
        assert reports == []


# ---------------------------------------------------------------------------
# Advisory generation
# ---------------------------------------------------------------------------

class TestAdvisory:
    @pytest.mark.asyncio
    async def test_publish_advisory(self, disclosure: VulnerabilityDisclosure, full_lifecycle_report: str):
        result = await disclosure.publish_advisory(full_lifecycle_report)
        assert result["report_id"] == full_lifecycle_report
        assert result["cve_id"].startswith("CVE-")
        assert len(result["advisory_content"]) > 0
        assert result["published_at"] is not None

    @pytest.mark.asyncio
    async def test_publish_advisory_not_patched(self, disclosure: VulnerabilityDisclosure, submitted_report: str):
        """Cannot publish advisory for unpatched report."""
        with pytest.raises(ValueError, match="must be 'patched' or 'closed'"):
            await disclosure.publish_advisory(submitted_report)

    @pytest.mark.asyncio
    async def test_publish_advisory_not_found(self, disclosure: VulnerabilityDisclosure):
        with pytest.raises(ValueError, match="not found"):
            await disclosure.publish_advisory("VR-NOPE")

    @pytest.mark.asyncio
    async def test_publish_advisory_twice_rejected(self, disclosure: VulnerabilityDisclosure, full_lifecycle_report: str):
        """Cannot publish advisory twice for the same report."""
        await disclosure.publish_advisory(full_lifecycle_report)
        with pytest.raises(ValueError, match="already published"):
            await disclosure.publish_advisory(full_lifecycle_report)

    def test_generate_advisory_template(self, disclosure: VulnerabilityDisclosure):
        template = disclosure.generate_advisory_template({
            "id": "VR-001",
            "title": "XSS",
            "severity": "high",
            "description": "Cross-site scripting",
            "impact": "Data theft",
        })
        assert "Security Advisory" in template
        assert "VR-001" in template
        assert "high" in template


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

class TestEncrypt:
    @pytest.mark.asyncio
    async def test_encrypt_report(self, disclosure: VulnerabilityDisclosure):
        report = {"title": "Test", "description": "Desc"}
        encrypted = await disclosure.encrypt_report(report)
        assert isinstance(encrypted, bytes)
        assert b"Test" in encrypted
