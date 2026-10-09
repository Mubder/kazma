"""Working memory must not invent basename aliases for explicit file paths."""
from __future__ import annotations

import re

import pytest
from kazma_core.agent.turn_input import extract_active_attachments, format_working_memory_anchor


@pytest.mark.parametrize("path", [
    "reports/release.txt", "./reports/release.txt", "../reports/release.txt",
    "/tmp/reports/release.txt", r"reports\release.txt", r"C:\reports\release.txt",
    r"\\server\share\release.txt", "تقارير/الإصدار.txt", "release.txt",
])
def test_exact_path_survives_extraction_and_working_memory(path):
    text = f"Read `{path}` and report its status."
    attachments = extract_active_attachments([], user_text=text)
    assert [a["path"] for a in attachments] == [path]
    block = format_working_memory_anchor(active_goal=text, active_attachments=attachments)
    assert f"- (file) {path}\n" in block


def test_distinct_files_with_same_basename_are_not_collapsed():
    attachments = extract_active_attachments([], user_text="Compare a/release.txt and b/release.txt.")
    assert [a["path"] for a in attachments] == ["a/release.txt", "b/release.txt"]


def test_attachment_stub_does_not_add_a_bare_filename_alias():
    attachments = extract_active_attachments([], user_text="Read [Attached: reports/release notes.txt — use file_read].")
    assert [a["path"] for a in attachments] == ["reports/release notes.txt"]


def test_actual_metadata_path_is_shown_instead_of_display_filename():
    messages = [{"role": "user", "content": [{"type": "text", "text": "Read this.",
                 "filename": "release.txt", "path": "uploads/123/release.txt"}]}]
    attachments = extract_active_attachments(messages)
    block = format_working_memory_anchor(active_goal="Read this.", active_attachments=attachments)
    assert "- (file) uploads/123/release.txt\n" in block
    assert "- (file) release.txt\n" not in block


@pytest.mark.parametrize("text", ["Read https://example.com/reports/release.txt",
                                 "Read reports/release.txt.backup"])
def test_urls_and_partial_extensions_are_not_local_attachment_paths(text):
    assert extract_active_attachments([], user_text=text) == []


def test_old_basename_extractor_is_a_negative_control():
    path = "reports/release.txt"
    old = re.search(r"([\w.\-]+\.(?:docx|pdf|pptx|xlsx|doc|txt|md|html))", path).group(1)
    assert old == "release.txt" and old != path
    assert extract_active_attachments([], user_text=path)[0]["path"] == path
