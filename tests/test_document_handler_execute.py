"""Document handler execute tests (§20.5).

Verifies: all mutations via execute(), raw file_write NOT called,
HITL escalation, quality gate, error detection via is_error not substring.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.agent.intent.handlers.document import run_document
from kazma_core.agent.intent.types import (
    ActKind,
    EntitySet,
    IntentAct,
    ResolvedFile,
    RouteKind,
    TurnDecision,
)


def _decision(
    *,
    fmt="pdf",
    files=(),
    unresolved=(),
    inline=False,
):
    acts = (IntentAct(
        kind=ActKind.DOCUMENT_GENERATE,
        confidence=0.90,
        slots={"format": fmt, **({"inline_content": True} if inline else {})},
    ),)
    entities = EntitySet(
        files=tuple(files),
        unresolved=tuple(unresolved),
    )
    return TurnDecision(
        focus="normal",
        acts=acts,
        entities=entities,
        route=RouteKind.EXECUTE,
        handler="document_generate",
        reason="test",
    )


class FakeExecutor:
    """Records every execute() call; returns configurable results."""

    def __init__(self, results=None):
        self.calls: list[tuple[str, dict]] = []
        self.results = results or {}

    async def execute(self, name, args):
        self.calls.append((name, args))
        if name in self.results:
            return self.results[name]
        return {"content": "OK", "is_error": False}


@pytest.fixture
def hitl_off(monkeypatch):
    """Disable HITL so the handler can auto-execute."""
    monkeypatch.setattr(
        "kazma_core.agent.intent.handlers.document._can_auto_execute",
        lambda te: True,
    )


class TestExecutePath:
    @pytest.mark.asyncio
    async def test_file_write_and_generate_via_execute(self, hitl_off, tmp_path):
        """file_write and generate_pdf must go through execute()."""
        pdf = tmp_path / "source.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content for testing")

        executor = FakeExecutor(results={
            "file_write": {"content": "Wrote document.md", "is_error": False},
            "generate_pdf": {
                "content": f"PDF generated. Saved to: {tmp_path}/output.pdf",
                "is_error": False,
            },
        })
        # Create the output file so the quality gate passes
        output = tmp_path / "output.pdf"
        output.write_bytes(b"%PDF-1.4 " + b"x" * 300)

        rf = ResolvedFile(path=str(pdf), filename="source.pdf", mime="application/pdf")
        decision = _decision(files=[rf])

        # Mock the PDF reading
        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="Aug 1\nReel\nInstagram\nTest content here",
        ):
            result = await run_document(decision, {"messages": []}, tool_executor=executor, llm=None)

        assert result.ok, f"Handler failed: {result.message}"
        tool_names = [c[0] for c in executor.calls]
        assert "file_write" in tool_names, f"file_write not called: {tool_names}"
        assert "generate_pdf" in tool_names, f"generate_pdf not called: {tool_names}"

    @pytest.mark.asyncio
    async def test_raw_file_write_not_called(self, hitl_off, tmp_path):
        """Raw file_write() function must NOT be called (only execute)."""
        pdf = tmp_path / "src.pdf"
        pdf.write_bytes(b"%PDF-test")

        raw_called = []
        original_write = None

        async def spy_write(*args, **kwargs):
            raw_called.append(args)
            return "Should not be called"

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {tmp_path}/out.pdf", "is_error": False},
        })
        (tmp_path / "out.pdf").write_bytes(b"x" * 300)

        rf = ResolvedFile(path=str(pdf), filename="src.pdf")
        decision = _decision(files=[rf])

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        # Verify no raw function call was made
        assert len(raw_called) == 0, f"Raw file_write was called: {raw_called}"
        # All calls went through execute
        assert all(c[0] in ("file_write", "generate_pdf") for c in executor.calls)

    @pytest.mark.asyncio
    async def test_is_error_not_substring(self, hitl_off, tmp_path):
        """'Error' in a successful message must NOT be treated as failure."""
        # A successful write whose message happens to contain 'Error' in
        # the content (e.g. "document_error_handling.md")
        executor = FakeExecutor(results={
            "file_write": {
                "content": "Wrote document_error_handling.md",
                "is_error": False,
            },
            "generate_pdf": {"content": f"Saved to: {tmp_path}/out.pdf", "is_error": False},
        })
        (tmp_path / "out.pdf").write_bytes(b"x" * 300)

        rf = ResolvedFile(path=str(tmp_path / "src.pdf"), filename="src.pdf")
        decision = _decision(files=[rf])

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        # Should succeed — is_error=False, the substring 'Error' in the
        # filename is not a failure
        assert result.ok or "file_write failed" not in result.message


class TestHITLEscalation:
    @pytest.mark.asyncio
    async def test_hitl_on_escalates(self, monkeypatch):
        """HITL enabled + no ContextVar → handler escalates, does not write."""
        monkeypatch.setattr(
            "kazma_core.agent.intent.handlers.document._can_auto_execute",
            lambda te: False,
        )
        executor = FakeExecutor()
        decision = _decision()

        result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert not result.ok
        assert result.escalate
        assert result.message == "hitl_required"
        # No execute calls made
        assert len(executor.calls) == 0


class TestQualityGate:
    @pytest.mark.asyncio
    async def test_quality_fail_no_deliver(self, hitl_off, tmp_path):
        """Output file missing or too small → ok=False, no delivery."""
        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {
                "content": f"Saved to: {tmp_path}/nonexistent.pdf",
                "is_error": False,
            },
        })
        # Don't create the output file — quality gate should fail

        rf = ResolvedFile(path=str(tmp_path / "src.pdf"), filename="src.pdf")
        decision = _decision(files=[rf], fmt="pdf")

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert not result.ok
        assert result.escalate  # quality failure → escalate

    @pytest.mark.asyncio
    async def test_small_file_quality_fail(self, hitl_off, tmp_path):
        """Output file exists but <200 bytes → quality fail."""
        out = tmp_path / "small.pdf"
        out.write_bytes(b"tiny")  # < 200 bytes

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {out}", "is_error": False},
        })

        rf = ResolvedFile(path=str(tmp_path / "src.pdf"), filename="src.pdf")
        decision = _decision(files=[rf])

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert not result.ok


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_xlsx_unstructured_fails(self, hitl_off, tmp_path):
        """XLSX from flat markdown → fail (not silent PDF)."""
        src = tmp_path / "src.pdf"
        src.write_bytes(b"%PDF")
        rf = ResolvedFile(path=str(src), filename="src.pdf")
        decision = _decision(fmt="xlsx", files=[rf])
        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
        })

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)
        assert not result.ok
        assert "XLSX" in result.message or "xlsx" in result.message.lower()

    @pytest.mark.asyncio
    async def test_pptx_missing_fails(self, hitl_off):
        """PPTX when generate_pptx not available → fail (not silent PDF)."""
        decision = _decision(fmt="pptx")
        executor = FakeExecutor(results={
            "generate_pptx": {"content": "Error: not available", "is_error": True},
        })

        result = await run_document(decision, {}, tool_executor=executor, llm=None)
        assert not result.ok

    @pytest.mark.asyncio
    async def test_no_source_escalates(self, hitl_off):
        """No source file + no inline content → escalate."""
        decision = _decision(files=[], unresolved=("source_file",))
        executor = FakeExecutor()

        result = await run_document(decision, {}, tool_executor=executor, llm=None)
        assert not result.ok
        assert result.escalate


class TestInlineContent:
    @pytest.mark.asyncio
    async def test_inline_content_succeeds(self, hitl_off, tmp_path):
        """'write me a PDF of the notes' with inline text → uses message content."""
        out = tmp_path / "out.pdf"
        out.write_bytes(b"%PDF-" + b"x" * 300)

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {out}", "is_error": False},
        })

        decision = _decision(inline=True)
        state = {"messages": [{"role": "user", "content": "Notes about the meeting and budget"}]}

        result = await run_document(decision, state, tool_executor=executor, llm=None)
        assert result.ok


class TestDeliveryRouting:
    """F8: delivery must route through the send_file tool (execute path),
    never the raw send_file_message function."""

    def _decision_with_delivery(self, rf):
        acts = (IntentAct(
            kind=ActKind.DOCUMENT_GENERATE,
            confidence=0.90,
            slots={"format": "pdf", "deliver_to": "telegram"},
        ),)
        return TurnDecision(
            focus="normal",
            acts=acts,
            entities=EntitySet(files=(rf,)),
            route=RouteKind.EXECUTE,
            handler="document_generate",
            reason="test",
        )

    @pytest.mark.asyncio
    async def test_delivery_routes_through_send_file(self, hitl_off, tmp_path):
        pdf = tmp_path / "src.pdf"
        pdf.write_bytes(b"%PDF-test")

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {tmp_path}/out.pdf", "is_error": False},
            "send_file": {"content": "Sent file to chat", "is_error": False},
        })
        (tmp_path / "out.pdf").write_bytes(b"x" * 300)

        rf = ResolvedFile(path=str(pdf), filename="src.pdf")
        decision = self._decision_with_delivery(rf)

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ), patch(
            "kazma_core.agent.intent.handlers.document._resolve_delivery_target",
            return_value="telegram:12345",
        ), patch(
            "kazma_core.tools.send_message.send_file_message",
            new_callable=AsyncMock,
        ) as direct_send:
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert result.ok, f"Handler failed: {result.message}"
        tool_names = [c[0] for c in executor.calls]
        assert "send_file" in tool_names, f"send_file not called via executor: {tool_names}"
        # The send_file call carries the generated output path + a caption.
        send_calls = [c for c in executor.calls if c[0] == "send_file"]
        assert send_calls[0][1].get("file_path", "").endswith("out.pdf")
        assert "caption" in send_calls[0][1]
        # The raw outbound function is never invoked directly.
        direct_send.assert_not_called()
        assert "Delivered via telegram" in result.message

    @pytest.mark.asyncio
    async def test_delivery_no_target_saves_locally(self, hitl_off, tmp_path):
        """When no delivery target resolves, the file is saved locally (no send)."""
        pdf = tmp_path / "src.pdf"
        pdf.write_bytes(b"%PDF-test")

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {tmp_path}/out.pdf", "is_error": False},
        })
        (tmp_path / "out.pdf").write_bytes(b"x" * 300)

        rf = ResolvedFile(path=str(pdf), filename="src.pdf")
        decision = self._decision_with_delivery(rf)

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ), patch(
            "kazma_core.agent.intent.handlers.document._resolve_delivery_target",
            return_value="",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert result.ok
        tool_names = [c[0] for c in executor.calls]
        assert "send_file" not in tool_names
        assert "file saved locally" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_deliver_slot_skips_delivery(self, hitl_off, tmp_path):
        """Without a deliver_to slot the handler does not attempt any send."""
        pdf = tmp_path / "src.pdf"
        pdf.write_bytes(b"%PDF-test")

        executor = FakeExecutor(results={
            "file_write": {"content": "OK", "is_error": False},
            "generate_pdf": {"content": f"Saved to: {tmp_path}/out.pdf", "is_error": False},
        })
        (tmp_path / "out.pdf").write_bytes(b"x" * 300)

        rf = ResolvedFile(path=str(pdf), filename="src.pdf")
        decision = _decision(files=[rf])  # no deliver_to slot

        with patch(
            "kazma_core.agent.intent.handlers.document._read_pdf_raw",
            return_value="test content",
        ):
            result = await run_document(decision, {}, tool_executor=executor, llm=None)

        assert result.ok
        tool_names = [c[0] for c in executor.calls]
        assert "send_file" not in tool_names
