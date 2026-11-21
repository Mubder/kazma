"""What shape a question has (framework audit F9, part F;
``memory/question_shape.py``): a follow-up is read with the message before
it, a request for every memory gets a list, and the chat's own turns the
model already reads are named so recall leaves them out.
"""

from __future__ import annotations

import pytest
from kazma_core.memory.limits import FOLLOW_UP_EARLIER_CHARS, turn_key
from kazma_core.memory.question_shape import (
    asks_for_all,
    earlier_user_texts,
    is_follow_up,
    standalone_query,
    turns_in_context,
)


@pytest.mark.parametrize("text", [
    "And what does she teach?",
    "Where is her practice?",
    "Yes, book it for Saturday at 10.",
    "What about the second one?",
    "ووين يشتغل؟",
    "طيب وكم سعرها؟",
    "خلاص، اطلبها من محل الحجي، الذهبية.",
])
def test_a_follow_up_is_read_as_one(text):
    assert is_follow_up(text), text


@pytest.mark.parametrize("text", [
    "ok", "thanks", "شكرا",                       # small talk is about nothing
    "Where do I live?",
    "What is my dog's name?",
    "And tell me everything my dentist said about the crown on my upper left molar last spring",
    "",
])
def test_a_question_that_says_what_it_is_about_is_not_a_follow_up(text):
    assert not is_follow_up(text), text


def test_a_follow_up_is_searched_with_the_message_before_it():
    earlier = ["What is the weather?", "Tell me about Maya, my neighbour."]
    assert standalone_query("And her sister?", earlier) == "Tell me about Maya, my neighbour. And her sister?"


def test_a_chain_of_follow_ups_reaches_the_message_that_names_the_subject():
    earlier = ["Tell me about Maya, my neighbour.", "And her sister?"]
    q = standalone_query("Where does she live?", earlier)
    assert q.startswith("Tell me about Maya") and q.endswith("Where does she live?")


def test_a_question_that_is_not_a_follow_up_is_searched_as_typed():
    earlier = ["Tell me about Maya, my neighbour."]
    assert standalone_query("What is my dog's name?", earlier) == "What is my dog's name?"
    assert standalone_query("And her sister?", []) == "And her sister?"
    assert standalone_query("And her sister?", ["thanks"]) == "And her sister?"


def test_the_message_before_is_bounded():
    q = standalone_query("And her sister?", ["x" * 5000])
    assert len(q) <= FOLLOW_UP_EARLIER_CHARS + len(" And her sister?")


@pytest.mark.parametrize("question", [
    "How many long runs did I do?",
    "List every note I asked you to keep",
    "What are all my subscriptions?",
    "Tell me everything about the wedding",
    "كم مرة رحت النادي؟",
    "عطني كل الاشتراكات",
    "اذكر لي الكتب اللي قريتها",
])
def test_a_request_for_every_memory_of_a_kind(question):
    assert asks_for_all(question), question


@pytest.mark.parametrize("question", [
    "How often do I exercise?",       # a frequency is one fact
    "What is my dog's name?",
    "Where did I live in June?",
    "وين ساكن؟",
])
def test_a_question_for_one_answer_is_not_a_listing(question):
    assert not asks_for_all(question), question


def _chat() -> list[dict]:
    return [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "My favourite tea is jasmine."},
        {"role": "assistant", "content": "Jasmine it is."},
        {"role": "user", "content": "And my sister likes mint."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "What tea do I like?"},
    ]


def test_the_users_earlier_messages_oldest_first_without_the_latest():
    assert earlier_user_texts(_chat()) == ["My favourite tea is jasmine.", "And my sister likes mint."]
    assert earlier_user_texts(_chat(), limit=1) == ["And my sister likes mint."]


def test_the_turns_the_model_reads_are_named_without_the_latest():
    keys = turns_in_context(_chat(), max_tokens=10_000)
    assert keys == {turn_key("My favourite tea is jasmine."), turn_key("And my sister likes mint.")}


def test_a_turn_past_the_context_budget_is_not_in_context():
    # The newest messages fit in 12 tokens (48 characters); the first does not.
    keys = turns_in_context(_chat(), max_tokens=12)
    assert turn_key("My favourite tea is jasmine.") not in keys
