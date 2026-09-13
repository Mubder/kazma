"""A tool step must lead with what it did, not with its argument JSON.

The Live Task Card showed `file_search “auth middleware”`. The in-bubble step
for the same event stored, and still shows after every reload:

    {"query":"auth middleware","path":"C:/x/y","max_results":50}

One event, two renderings, and the raw one was the one that got persisted into
the TurnDocument. The operator called it junk, correctly.

The raw text is not deleted — `.step-detail` clamps to three lines with a "Show
more", so arguments and results stay inspectable. It just stops being the first
thing anyone reads.

These run the real module. The formatting used to live inside chat.js, 7,700
lines wrapped in an IIFE around a DOM, where it could only be checked by
reading it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui" / "kazma_ui" / "static" / "js" / "turn_detail.js"
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _call(expr: str):
    harness = (
        "globalThis.window = globalThis;\n"
        f"eval(require('fs').readFileSync({json.dumps(str(_JS))}, 'utf8'));\n"
        "const M = globalThis.KazmaTurnDetail;\n"
        f"console.log(JSON.stringify({expr}));\n"
    )
    # encoding + errors are load-bearing on Windows: the default is cp1252,
    # and every gist is wrapped in curly quotes.
    out = subprocess.run(
        ["node", "-e", harness],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_ARGS = '{"query":"auth middleware","path":"C:/x/y","max_results":50}'


class TestTheGistLeads:
    def test_the_first_line_is_readable(self):
        detail = _call(f"M.forArgs({_ARGS})")
        assert detail.split("\n")[0] == "\u201cauth middleware\u201d"

    def test_the_raw_arguments_are_still_there(self):
        """Clamped behind "Show more", not discarded — they are how you debug
        a tool call that went wrong."""
        detail = _call(f"M.forArgs({_ARGS})")
        assert "max_results" in detail

    def test_it_prefers_the_identifying_argument(self):
        assert _call('M.argSummary({"path":"/etc/hosts","mode":"r"})') == "\u201c/etc/hosts\u201d"
        assert _call('M.argSummary({"mode":"r","weird":"value"})') == "\u201cr\u201d"

    def test_a_json_string_is_parsed_not_quoted_whole(self):
        assert _call('M.argSummary(\'{"query":"hello"}\')') == "\u201chello\u201d"

    def test_plain_text_arguments_are_not_duplicated(self):
        """When the raw value already reads as the gist, prefixing it would
        print the same thing twice."""
        assert _call("M.forArgs('just some text')") == "just some text"


class TestResults:
    def test_a_list_result_is_counted(self):
        assert _call("M.resultSummary('[{\"a\":1},{\"a\":2}]')") == "2 results"
        assert _call("M.resultSummary('[{\"a\":1}]')") == "1 result"

    def test_a_long_result_leads_with_its_first_line(self):
        multi = "first line" + chr(92) + "nsecond line" + chr(92) + "nthird line"
        detail = _call("M.forResult('" + multi + "')")
        assert detail.split("\n")[0] == "\u201cfirst line\u201d"
        assert "third line" in detail


class TestItNeverBreaksARow:
    @pytest.mark.parametrize("expr", [
        "M.forArgs(null)", "M.forArgs(undefined)", "M.forArgs('')",
        "M.forResult(null)", "M.forResult('')", "M.withGist('', '')",
        "M.argSummary(123)", "M.argSummary([])",
    ])
    def test_empty_and_odd_inputs_return_a_string(self, expr):
        assert isinstance(_call(expr), str)

    def test_secrets_style_ids_are_not_chosen_as_the_gist(self):
        """A thread/session id is never what the step is about."""
        got = _call('M.argSummary({"task_id":"abc123","query":"real thing"})')
        assert got == "\u201creal thing\u201d"


class TestChatJsUsesIt:
    @staticmethod
    def _chat() -> str:
        return (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
        ).read_text(encoding="utf-8")

    def test_both_transports_log_the_gist_not_the_raw_blob(self):
        """SSE and WS each had their own pair of call sites with the same bug.
        Fixing one would have left the other."""
        chat = self._chat()
        assert chat.count("_tcDetailWithGist(_tcArgSummary(data.inputs), inputs)") == 2
        assert chat.count("_tcDetailWithGist(_tcResultSummary(data.result), data.result)") == 2
        assert "detail: String(inputs || '')" not in chat
        assert "detail: String(data.result || '')" not in chat

    def test_the_formatting_is_not_reimplemented_inside_chat_js(self):
        chat = self._chat()
        assert "M.resultSummary(result)" in chat, "chat.js grew its own copy again"
        assert "M.withGist(gist, raw)" in chat

    def test_the_module_is_loaded_before_chat_js(self):
        html = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
        ).read_text(encoding="utf-8")
        assert html.index("turn_detail.js") < html.index("js/chat.js")
