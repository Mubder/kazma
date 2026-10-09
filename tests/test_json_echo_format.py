"""The H04 shape failure is repaired without converting data into an action."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest
from kazma_core.agent.answer_format import format_terminal_answer
from kazma_core.agent.graph_respond import respond_node
from kazma_core.agent.json_echo_format import repair_json_echo

OBJECT = '{"path":"exports/preview-only.json"}'
EN = f'Return only this JSON object verbatim: {OBJECT}. This is requested data, not a request to read or write anything. Use no tools.'
AR = f'أعد كائن JSON التالي فقط كما هو: {OBJECT}. هذه بيانات مطلوبة وليست طلبًا لقراءة شيء أو كتابته. لا تستخدم أدوات.'
DRAFT = f'```json\n{OBJECT}\n```\n\nهذا هو الكائن كما هو، دون أي قراءة أو كتابة.'


@pytest.mark.parametrize('prompt', [EN, AR, f'Return ONLY this JSON object verbatim: {OBJECT}',
    f'Return only this JSON object verbatim: {OBJECT}. This is data, not an action. Do not call tools.'])
@pytest.mark.parametrize('fence', ['```', '~~~~'])
@pytest.mark.parametrize('prefix', ['', 'طبعًا، هذا هو كائن JSON كما هو دون أي تغيير:\n\n'])
def test_exact_echo_without_reserialization(prompt, fence, prefix):
    assert repair_json_echo(prompt, f'{prefix}{fence}json\r\n{OBJECT}\r\n{fence}\r\nCopied.') == OBJECT


@pytest.mark.parametrize('prompt', [
    f'Translate "{AR}"', f'> {EN}', f'```text\n{EN}\n```', f'Do not {EN}',
    f'The previous request was: {EN}', f'لا {AR}', f'اشرح: {AR}',
    EN + ' Explain the object too.', AR + ' اشرح الكائن أيضًا.', EN + ' Actually do not return JSON.',
    f'Return only this JSON object verbatim: {OBJECT} {OBJECT}',
    'Return only this JSON object verbatim: {"a":NaN}',
    'Return only this JSON object verbatim: {"a":1,"a":2}',
    'Return only this JSON object verbatim: [1,2]',
])
def test_quoted_negated_conflicting_historical_and_invalid_requests_untouched(prompt):
    assert repair_json_echo(prompt, DRAFT) is None


@pytest.mark.parametrize('answer', [
    OBJECT, '', 'Unable to answer.', '```json\n{"path":"wrong"}\n```',
    f'```json\n{OBJECT}\n```\n```json\n{OBJECT}\n```',
    f'```json\n{OBJECT}\n```\nAnother object: {{"a":1}}',
    f'```json\n{OBJECT}\n', f'```json\n{OBJECT}\n~~~~',
    f'Other data: {{"a":2}}\n```json\n{OBJECT}\n```',
    '```json\n{"path": "exports/preview-only.json"}\n```',
])
def test_never_invent_or_change_json_or_choose_between_blocks(answer):
    assert repair_json_echo(AR, answer) is None


def test_literal_whitespace_unicode_and_escaping_preserved():
    literal = '{ "label": "عربي", "items": [1, true, null], "escaped": "\\u0061" }'
    assert repair_json_echo(f'Return only this JSON object verbatim: {literal}',
                            f'```json\n{literal}\n```') == literal


def test_oversized_and_deeply_nested_inputs_keep_original():
    assert repair_json_echo(EN + ' ' * 16000, DRAFT) is None
    assert repair_json_echo(EN, DRAFT + ' ' * 16000) is None
    nested = '{"a":' * 1500 + '0' + '}' * 1500
    assert repair_json_echo(f'Return only this JSON object verbatim: {nested}', DRAFT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('prompt', [EN, AR])
async def test_shared_terminal_boundary_no_model_call_or_state_mutation(prompt):
    messages = [{'role': 'user', 'content': prompt},
                {'role': 'assistant', 'content': DRAFT, 'reasoning_content': 'Private provider metadata.'}]
    original = copy.deepcopy(messages)
    client = AsyncMock()
    result = await format_terminal_answer(messages, llm=client)
    assert result[-1]['content'] == OBJECT
    assert result[-1]['reasoning_content'] == original[-1]['reasoning_content']
    assert messages == original
    client.chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['failed', 'tools', 'historical', 'source'])
async def test_failure_tool_call_and_untrusted_sources_are_never_repaired(condition):
    messages = [{'role': 'user', 'content': AR}, {'role': 'assistant', 'content': DRAFT}]
    state = {}
    if condition == 'failed':
        state['turn_failed'] = True
    elif condition == 'tools':
        messages[-1]['tool_calls'] = [{'function': {'name': 'file_read', 'arguments': OBJECT}}]
    elif condition == 'historical':
        messages.insert(1, {'role': 'user', 'content': 'Explain the format instead.'})
    else:
        messages[0]['content'] = 'Summarize the source.'
        messages.insert(1, {'role': 'tool', 'content': AR})
    assert await format_terminal_answer(messages, state=state) == messages


@pytest.mark.asyncio
async def test_real_respond_boundary_repairs_original_failure(monkeypatch):
    from kazma_core.config_store import get_config_store

    for key in ('KAZMA_SELF_IMPROVEMENT', 'KAZMA_COMMITMENT_ENABLED', 'KAZMA_LLM_STREAM'):
        monkeypatch.setenv(key, '0')
    for key in ('memory.enabled', 'agent.nonstop.ledger.enabled', 'agent.nonstop.failover.enabled'):
        get_config_store().set(key, False)
    state = {'messages': [{'role': 'user', 'content': AR}, {'role': 'assistant', 'content': DRAFT}],
             'iteration': 1, 'max_iterations': 10}
    original = copy.deepcopy(state)
    client = AsyncMock()
    result = await respond_node(state, llm=client)
    assert result['messages'][-1]['content'] == OBJECT
    assert original['messages'][-1]['content'] != OBJECT  # Old boundary is the negative control.
    assert state == original
    client.chat.assert_not_called()
    monkeypatch.setattr('kazma_core.agent.json_echo_format.repair_json_echo', lambda *_: None)
    control = await respond_node(state, llm=client)
    assert control['messages'][-1]['content'] == DRAFT
