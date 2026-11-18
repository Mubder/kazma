"""The user's own words in a message.

A chat message can hold text the user did not say about themself:

* an attached document: every surface puts its text into the user's message
  inside a data fence, under an "[Attached: ...]" line
  (``agent_handler/attachments.build_user_content``,
  ``agent/attached_text.py``);
* a forwarded message ("---------- Forwarded message ---------", "Forwarded
  from Dana", "رسالة محولة"; the chat apps fence one they are told is
  forwarded, under a "[Forwarded message ...]" line);
* a quote: a fenced code block, ``>`` lines, a quoted sentence in the first
  person ("she wrote "I live in Lyon"", in straight, curly, single or
  backtick quotes, or «»), and what someone else said after a colon ("my
  sister said: I moved to Rotterdam", "قالت: اسمي نورة"), to the end of that
  sentence ("Kazma said: your name is Ahmed. No, my name is Ahmad." keeps the
  correction);
* the text an instruction works on: "Translate to French: My name is
  Pierre", "Fix the grammar: I work at Initech", "Can you translate this into
  Spanish? ...", "سوي لي ترجمة: اسمي سارة", to the end of the message or to
  an aside that ends it ("By the way, ...", "بالمناسبة ...");
* a pasted chat: a WhatsApp export's lines ("18/11/2026, 12:01 - Dana:
  ..."), a Telegram, Discord or Slack copy's header and the message under it,
  a mail's "On ... Dana wrote:" and what follows it to the next blank line,
  and a line whose label names who spoke ("Dana:", "dana_92:", "the
  customer:"), never a greeting ("Good morning: ...");
* someone else's claim with no colon ("According to Ali, ...", "Ali thinks
  I ...", "He was like, I ...", "As Dana put it, ..."), a text of theirs
  named ("Dana's bio:", "Her message was:"), an example or a part to play
  ("For example: ...", "Pretend you are me and say: ...").

Inside a request to remember ("remember this: "I prefer dark mode"", "تذكر:
«أحب القهوة»") the quote is what the user asks to be kept: the user's own
statement. A text the request names as pasted ("Remember this email:
...") is not.

Memory takes a fact at the user's own trust only from what is left. The
quick pass (``belief_extractor.extract_beliefs_heuristic``), the "remember
this" check (``remember_request.is_remember_request``) and the trust a
memory tool's write earns (``provenance.agent_write_source``) all read
through :func:`own_words`.

Nothing is lost by it: the turn keeps the whole message, and the model's
deep pass reads all of it, at the trust of an inference.

The rules lean one way: an instruction is recognised by its verb and, for
verbs that also mean something else ("fix", "correct", "صحح"), by naming the
text it works on ("fix the grammar:", "صحح هذا:"), so "Correct: my name is
Ahmad" stays the user's own correction. The decided cost of ending someone
else's words at their sentence: "my sister said: I'm a nurse. I live in
Rotterdam." files the second sentence under the user (a quoted span, "my
sister said: "I'm a nurse. I live in Rotterdam."", does not). Everything here
reads a bounded window per position, so a message is read in time linear in
its length.
"""

from __future__ import annotations

import re

__all__ = ["is_plain", "last_words", "own_words"]

#: "[Attached: ...]", "[Full document at: ...]" and "[Forwarded message ...]"
#: lines: a file's name, or who forwarded something, is not the user's
#: statement. To the end of the line, at most 2,000 characters: a name holding
#: "]" ("report] I live in Lyon.pdf") ended the stub at its first bracket, and
#: the rest read as the user's words. Kazma writes each stub on a line of its
#: own, after the user's words.
_STUB_RE = re.compile(r"\[(?:Attached|Full document at|Forwarded message)\b[^\n]{0,2000}+")
#: The web chat's file stub (``agent/attached_text.py``). It comes last in a
#: message, and before 2026-11 the file's text followed it in a code block
#: that a line of three backticks inside the file ended early: what follows
#: it is never the user's.
_WEB_FILE_STUB = "[Attached file:"
#: A ``>`` quote line.
_QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>[^\n]*+", re.MULTILINE)
#: A span in quotes, within one paragraph, at most 400 characters: straight
#: double quotes (never one right after a digit: 27" is an inch mark), curly
#: double quotes, guillemets, straight and curly single quotes (an
#: apostrophe inside a word is no quote: "I'm", "don't"), backticks.
_QUOTED_SPAN_RE = re.compile(
    # Double quotes and guillemets may run over a line break, never over a
    # blank line ("She wrote "My name is Dana.\nI live in Oslo."").
    r"(?<!\d)\"(?:[^\"\n]|\n(?!\n)){1,400}+\""
    r"|“(?:[^”\n]|\n(?!\n)){1,400}+”"
    r"|«(?:[^»\n]|\n(?!\n)){1,400}+»"
    # A single quote before a digit is an apostrophe ("the '90s").
    r"|(?<!\w)'(?!\d)(?:[^'\n]|'(?=\w)){1,400}+'(?!\w)"
    r"|‘(?:[^’\n]|’(?=\w)){1,400}+’(?!\w)"
    r"|`[^`\n]{1,400}+`"
)
_WORD_RE = re.compile(r"[^\W\d_](?:[\w'’-]*+)")

_FIRST_PERSON_EN = frozenset({"i", "i'm", "im", "i've", "i'd", "i'll", "me", "my", "mine", "myself",
                              "we", "we're", "we've", "our", "ours", "us"})
#: Folded: a pronoun, "my name", "I have", and the quick pass's
#: first-person verbs and participles.
_FIRST_PERSON_AR = frozenset({"انا", "احنا", "نحن", "اسمي", "عندي", "لدي", "ساكن", "ساكنه", "اسكن",
                              "اعيش", "اشتغل", "اعمل", "احب", "انتقلت", "انتقلنا", "شغال", "موظف",
                              "موظفه"})

#: Words before an instruction's verb: courtesy, and calling Kazma by name.
_POLITE_EN = ("please", "pls", "plz", "kindly", "can you", "could you", "would you", "will you",
              "hey", "hi", "hello", "ok", "okay", "now", "also", "and", "then", "next", "kazma",
              # A reminder to do something: "Don't forget to translate: ..." is
              # the instruction "translate" (the third review of F1, item 2).
              "don't forget to", "dont forget to", "do not forget to", "remember to", "make sure to",
              "be sure to")
#: Verbs that only ever work on a text, and the labels of a text that is not
#: the user speaking: the head may stop at a colon.
_TEXT_VERBS_EN = ("translate", "proofread", "rephrase", "paraphrase", "summarise", "summarize",
                  "rewrite", "reword", "sum up", "reply to", "respond to", "draft a reply to",
                  "write a reply to", "answer this", "roleplay", "role-play", "role play",
                  "example sentence", "sample sentence", "example text", "sample text")
#: A part the user asks Kazma to play, or a world to imagine: what follows is
#: no statement of the user's ("Pretend you are me and say: I live in Lyon",
#: "Imagine I work at Initech").
_HYPOTHETICAL_EN = ("pretend", "imagine", "act as", "act like", "play the role", "play as", "impersonate",
                    "say as if", "speak as", "talk like", "suppose", "let's say", "lets say")
#: An example, before a colon: what follows illustrates, it states nothing
#: ("For example: I work at Initech", "e.g.: ...").
_EXAMPLE_HEADS_EN = ("for example", "for instance", "e.g", "eg", "example", "examples", "sample",
                     "something like", "say something like")
#: Writing a text for someone ("Write an email to my landlord saying:"): an
#: instruction when it names what is written, never "write this down".
_WRITE_VERBS_EN = ("write", "draft", "compose")
#: Never "bio": a bio Kazma is asked to write is the user's, and the facts
#: given for it are theirs ("Write a bio: I work at Initech").
_WRITTEN_THINGS_EN = frozenset({"email", "e-mail", "mail", "message", "letter", "post", "tweet", "reply",
                                "story", "essay", "article", "caption", "note", "text", "paragraph",
                                "script", "speech"})
#: Verbs that also mean something else: an instruction only when it names
#: the text ("fix the grammar:", "correct this:"), never "Correct: ...".
_EDIT_VERBS_EN = ("fix", "correct", "edit", "polish", "improve", "shorten", "simplify", "reformat",
                  "format", "check", "review", "tidy up", "clean up")
_TEXT_OBJECTS_EN = frozenset({"this", "these", "that", "following", "below", "it", "text", "grammar",
                              "spelling", "punctuation", "wording", "tone", "email", "e-mail", "mail",
                              "message", "paragraph", "sentence", "letter", "note", "post", "tweet",
                              "draft", "reply", "bio", "story", "essay", "article", "caption", "cv",
                              "resume", "translation", "doc", "document"})
#: The work named as a thing, before a colon: "Give me a translation:".
_TEXT_WORK_EN = frozenset({"translation", "summary", "paraphrase", "rewording"})
#: Someone else's words after a colon: "she said:", "my boss wrote:", "her
#: bio reads:".
_SAID_EN = frozenset({"said", "says", "wrote", "writes", "told", "tells", "asked", "asks",
                      "replied", "replies", "texted", "messaged", "posted", "tweeted", "reads",
                      "added", "adds", "answered", "explained"})
#: Verbs that report what someone thinks or claims ("Ali thinks I live in
#: Lyon", "Dana claims I work at Initech"), read as the reporting verbs are.
#: Never "thought" or "figures": nouns too ("Random thought:").
#: The third review's forms (item 3): "My parents think I ...", "People
#: assume I ...", "Dana goes, I'm ...", "The website states that I ...".
#: With "I" or "we" as the subject they stay the user's.
_MORE_THINKING_EN = frozenset({"think", "believe", "assume", "claim", "say", "insist", "suspect", "reckon",
                               "goes", "states", "stated", "shows", "lists"})
_THINKING_EN = frozenset({"thinks", "believes", "believed", "claims", "claimed", "insists", "insisted",
                          "reckons", "reckoned", "mentions", "mentioned", "suggests", "suggested",
                          "assumes", "assumed", "argues", "argued", "swears", "swore", "guesses",
                          "guessed"}) | _MORE_THINKING_EN
_REPORTING_EN = _SAID_EN | _THINKING_EN
#: A text of someone else's named before a colon: "Dana's bio:", "Her message
#: was:", "My sister's reply:".
_SOMEONES_TEXT_EN = frozenset({"message", "messages", "email", "e-mail", "mail", "text", "note", "notes",
                               "letter", "comment", "review", "post", "tweet", "reply", "dm", "sms",
                               "voicemail", "bio", "profile", "status", "caption", "description", "intro",
                               "cv", "resume", "words", "answer", "response", "story", "page", "article",
                               "essay", "statement"})
#: Who owns that text: "his", "her", "their", or a name's possessive --
#: never these ("It's ...", "Here's ...", "Today's news:").
_SOMEONES_EN = frozenset({"his", "her", "their"})
_NOT_POSSESSIVE_EN = frozenset({"it's", "that's", "what's", "here's", "there's", "let's", "he's", "she's",
                                "who's", "where's", "how's", "one's", "everyone's", "today's", "tomorrow's",
                                "yesterday's", "life's", "world's", "week's", "year's"})
#: A request to remember a text the user pasted ("Remember this email:",
#: "Save the message below:"): the text is someone else's, never the user's
#: statement of themself (rule C of the second security review of F1).
_REMEMBER_VERBS_EN = frozenset({"remember", "save", "keep", "store", "memorize", "memorise"})
_PASTED_THINGS_EN = frozenset({"email", "emails", "e-mail", "mail", "message", "messages", "letter",
                               "article", "articles", "post", "posts", "tweet", "comment", "review", "reply",
                               "dm", "sms", "voicemail", "document", "doc", "page", "chat", "conversation",
                               "transcript", "thread", "quote", "screenshot", "receipt", "invoice", "bio",
                               "profile", "excerpt"})
_THIS_EN = frozenset({"this", "these", "that", "following", "below", "attached", "above"})
#: "Remember this email address:" names the user's own details, not a pasted
#: mail.
_DETAIL_WORDS_EN = frozenset({"address", "addresses", "id", "account", "domain", "password", "number",
                              "signature", "handle"})
#: A head this long may still name who spoke ("My sister who lives far away
#: told me yesterday on the phone:"); the colon is what makes it a head.
_REPORTED_HEAD_WORDS = 14
#: A text named by where it came from: "Message from Dana:".
_MESSAGE_THINGS_EN = frozenset({"message", "email", "e-mail", "mail", "text", "note", "letter", "comment",
                                "review", "post", "tweet", "reply", "dm", "sms", "voicemail"})
#: A question or request about a text the user pasted: "Tell me if this is
#: spam:", "Is this sentence correct:", "What does this mean:". The head
#: starts with one of these and names the text (``_TEXT_OBJECTS_EN``).
_ASKS_ABOUT_EN = frozenset({"tell", "is", "are", "does", "do", "what", "check", "can", "could", "would",
                            "how", "which", "why", "explain", "see"})
#: A text the user hands over: "Here is the article:", "Below is the email:".
_HANDS_OVER_EN = ("here is", "here's", "heres", "here are", "below is", "below are", "this is the",
                  "the following", "following is", "attached is")
#: Where a question starts: a sentence asking something states nothing of
#: the user ("How do I say my name is Pierre in French?").
_QUESTION_WORDS_EN = frozenset({"how", "what", "why", "which", "who", "whom", "whose", "where", "when",
                                "should", "shall", "can", "could", "would", "will", "do", "does", "did",
                                "is", "are", "am", "was", "were", "have", "has"})
_QUESTION_WORDS_AR = frozenset({"هل", "كيف", "وش", "شنو", "شو", "ايش", "ليش", "متى", "وين", "شلون", "كم",
                                "مين", "ماذا", "لماذا", "اين"})

#: Arabic, folded: the verbs (with their "to me"/"it" forms), polite words
#: before them, and the text they name.
#: A verb here may also carry "ت" for "you" ("تترجم", "تصححلي").
_TEXT_VERBS_AR = frozenset({"ترجم", "ترجمي", "ترجملي", "ترجمه", "ترجمها", "ترجمو", "لخص", "لخصي",
                            "لخصلي", "لخصه", "لخصها", "دقق", "دققي"})
_EDIT_VERBS_AR = frozenset({"صحح", "صححي", "صححلي", "صححه", "صححها", "عدل", "عدلي", "عدله", "عدلها",
                            "حسن", "حسني", "حسنلي", "راجع", "راجعي", "اختصر", "اختصري", "اعد", "عيد",
                            "صيغ", "رد", "ردي", "صلح", "صلحي", "صلحلي", "صلحه", "صلحها"})
_TEXT_OBJECTS_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "التالي", "التاليه", "النص", "الرساله",
                              "الايميل", "الايميلات", "البريد", "الفقره", "الجمله", "الاملاء",
                              "القواعد", "الصياغه", "صياغه", "الكلام", "التغريده", "المنشور", "الرد",
                              "المقال", "الخطاب", "الرسالة", "النصوص"})
#: Writing a text for someone: "اكتب لي رسالة:", "سوي لي ايميل:".
_WRITE_VERBS_AR = frozenset({"اكتب", "اكتبي", "اكتبلي", "سوي", "سويلي", "سوو", "صيغلي"})
_WRITTEN_THINGS_AR = frozenset({"رساله", "الرساله", "ايميل", "الايميل", "تغريده", "التغريده", "منشور",
                                "المنشور", "خطاب", "الخطاب", "مقال", "المقال", "رد", "الرد", "نص", "فقره",
                                "قصه", "كلمه"})
#: The work named as a thing, before a colon: "سوي لي ترجمة:", "عطني تلخيص:".
#: Never "تصحيح": "تصحيح: أنا ساكن في دبي" is the user correcting themself.
_TEXT_WORK_AR = frozenset({"ترجمه", "تلخيص", "تدقيق", "صياغه"})
_POLITE_AR = frozenset({"ممكن", "لو", "سمحت", "رجاء", "رجاءا", "من", "فضلك", "يا", "ريت", "تكفى",
                        "تكفي", "ابيك", "ابغاك", "ودي", "هلا", "مرحبا", "طيب", "اوكي", "الحين",
                        "كازما"})
#: Never "كتبت": it is "I wrote" and "she wrote" alike, and what follows
#: "كتبت:" is a written text either way, never a statement of self.
_I_AR = frozenset({"انا", "احنا", "نحن", "قلت", "قلنا"})
#: Arabic heads that hand over or ask about a text ("هذا المقال:", "وش
#: معنى هذا:", "هل هذي الجملة صحيحة:").
_HANDS_OVER_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "هاذي", "التالي"})
_ASKS_ABOUT_AR = frozenset({"هل", "وش", "شنو", "شو", "ايش", "شوف", "شيك", "تاكد", "اشرح", "معنى"})
#: "قلت لي" / "قلتلي": "you told me" (the "قلت" of "I said" takes no "لي").
_YOU_TOLD_ME_AR = frozenset({"قلتلي", "كتبتلي"})
_SAID_AR = frozenset({"قال", "قالت", "قالوا", "قالو", "يقول", "تقول", "يقولون", "كتب", "كتبت",
                      "كتبو", "كتبوا", "يكتب", "تكتب", "رد", "ردت", "سال", "سالت"})
#: The Gulf participles of saying and writing ("أختي كاتبة إني ساكن في دبي":
#: my sister has written that I live in Dubai) and the verbs of thinking or
#: claiming. "أنا كاتبة" (I'm a writer) stays the user's: the user is its
#: subject.
_THINKING_AR = frozenset({"كاتب", "كاتبه", "كاتبين", "قايل", "قايله", "قايلين", "يعتقد", "تعتقد",
                          "يظن", "تظن", "يدعي", "تدعي", "يزعم", "تزعم"})
_REPORTING_AR = _SAID_AR | _THINKING_AR
#: A part to play or a world to imagine ("تخيل إنك أنا وقل: ...").
_HYPOTHETICAL_AR = frozenset({"تخيل", "تخيلي", "افترض", "افترضي", "تقمص", "تقمصي"})
#: An example before a colon ("مثلا: ساكن في دبي").
_EXAMPLE_HEADS_AR = ("مثلا", "مثال", "على سبيل المثال", "كمثال", "مثلا يعني")
#: Someone else's text named before a colon: a text noun with "his", "her"
#: or "their" written onto it ("رسالتها:", "كلامه:"), or one followed by who
#: wrote it ("رسالة دانة:", "نبذة علي:").
_TEXT_STEMS_AR = frozenset({"رسالت", "ايميل", "كلام", "تعليق", "منشور", "تغريدت", "نبذت", "سيرت", "جواب",
                            "مسج", "بايو", "رد", "مقال", "خطاب", "بوست", "بروفايل"})
_THIRD_SUFFIXES_AR = ("ها", "هم", "هن", "ه")
_TEXT_NOUNS_AR = frozenset({"رساله", "ايميل", "كلام", "تعليق", "منشور", "تغريده", "نبذه", "سيره", "جواب",
                            "مسج", "بايو", "مقال", "خطاب", "بوست", "بروفايل"})
#: Remembering a pasted text ("احفظ هذا الإيميل:", "تذكر هالرسالة:").
_REMEMBER_VERBS_AR = frozenset({"تذكر", "احفظ", "سجل", "خزن"})
_PASTED_THINGS_AR = frozenset({"الايميل", "الايميلات", "الرساله", "الرسايل", "المقال", "المحادثه",
                               "التغريده", "المنشور", "الخطاب", "الرد", "التعليق", "المسج", "البوست"})
#: Every Arabic verb these rules know: none of them names who spoke a line.
_AR_VERBS = (_TEXT_VERBS_AR | _EDIT_VERBS_AR | _WRITE_VERBS_AR | _REPORTING_AR | _HYPOTHETICAL_AR
             | {"تذكر", "احفظ", "سجل"})

#: An aside that ends the text an instruction works on, at the start of a
#: line or a sentence after it: "Translate: thanks.\nBy the way, my name is
#: Sam." Compared lower-cased (English) or folded (Arabic).
_ASIDES_EN = ("by the way", "btw", "fyi", "p.s", "ps:", "ps,", "on another note", "unrelated", "oh and",
              "oh, and", "anyway,")
_ASIDES_AR = ("بالمناسبه", "على فكره", "ع فكره", "على الهامش", "بعيدا عن هذا", "بعيد عن هذا")
#: Asides only at a line's start ("Can you review my text?\nAlso my name is
#: Sam."): inside a line, "Also, I have two cats" is still the text.
_LINE_ASIDES_EN = ("also ", "also,", "and also")
_LINE_ASIDES_AR = ("كمان ", "وكمان ", "ايضا", "وايضا")

#: A line of a pasted chat or transcript: who spoke, then a colon ("Dana:
#: My name is Dana", "[12:01, 18/11/2026] Dana: ...", "نورة: اسمي نورة").
#: The label is one to three words; a bracketed time may stand before it
#: (group 1), and then any label names who spoke: the time says the line was
#: copied from a chat ("[12:01, 18/11/2026] dana_92: ...").
_SPEAKER_LINE_RE = re.compile(
    r"[ \t]{0,8}+(\[[^\]\n]{1,40}+\][ \t]{0,4}+|\d{1,2}:\d{2}(?:[ \t]?+[AaPp]\.?+[Mm]\.?+)?+[ \t]{1,4}+)?+"
    r"([^:\n]{1,40}+):"
)
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}")
#: An IRC-style line: "<Dana> I live in Oslo".
_ANGLE_LINE_RE = re.compile(r"[ \t]{0,8}+<([^<>\n]{1,40}+)>[ \t]")
#: Before a label: a list's number ("1. Dana:", "2) Ali:") or a retweet's
#: "RT" ("RT @dana:").
_LABEL_LEAD_RE = re.compile(r"(?:\d{1,3}[.)][ \t]{0,3}+|RT[ \t]{1,3}+)")
#: A phone number as the label: what an export shows for a contact not saved.
_PHONE_RE = re.compile(r"\+?[\d \-().]{7,24}")
#: "User 1", "Person A", "Speaker 2": a numbered role.
_NUMBERED_ROLES_EN = frozenset({"user", "person", "speaker", "participant", "customer", "agent", "guest",
                                "caller", "student", "member", "client", "interviewer", "respondent", "p"})
#: Words around a name that are not the name ("Dr. Dana Smith Jr.").
_TITLES_EN = frozenset({"dr", "mr", "mrs", "ms", "miss", "prof", "sir", "madam", "sheikh", "eng", "capt", "rev",
                        "jr", "sr", "ii", "iii", "phd", "md"})
_PARENTHETICAL_RE = re.compile(r"\([^()\n]{0,40}+\)")
#: A single capital as the label: a transcript's sides ("A:", "B:"); never
#: "I:" or "Q:".
_SIDES = frozenset("ABCDEFGHJKLMNOPRSTUVWXYZ")
#: A greeting before a colon is the user greeting, never who spoke ("Good
#: morning: I live in Oslo"). Compared lower-cased, spaces folded (English),
#: or by its first word folded (Arabic).
_GREETINGS_EN = frozenset({"good morning", "good evening", "good afternoon", "good night", "good day",
                           "morning", "evening", "greetings", "hey there", "hi there", "hello there",
                           "hi all", "hey all", "hello all", "hi everyone", "hey everyone", "dear all",
                           "gm", "gn", "salam", "salaam", "assalamu alaikum", "as-salamu alaykum"})
_GREETINGS_AR = frozenset({"صباح", "مساء", "السلام", "سلام", "اهلا", "هلا", "مرحبا", "حياك", "حياكم",
                           "يسعد", "تحيه"})
#: Words that make a label a heading the user wrote, never a name ("Random
#: thought:", "Hot take:", "Step 2:", "currently:"). Not names people carry
#: ("Hope", "Grace", "Will").
_HEADING_WORDS = frozenset({
    "thought", "thoughts", "take", "takes", "idea", "ideas", "point", "points", "thing", "things", "lol",
    "lmao", "tldr", "tl", "dr", "nb", "i.e", "ie", "aka", "etc", "random", "quick", "side", "fun", "hot",
    "true", "big", "short", "long", "final", "first", "second", "third", "last", "next", "new", "old",
    "step", "steps", "day", "week", "month", "part", "phase", "item", "items", "option", "options",
    "version", "problem", "issue", "solution", "request", "task", "tasks", "feedback", "conclusion",
    "takeaway", "takeaways", "recap", "verdict", "rule", "rules", "lesson", "lessons", "twist", "spoiler",
    "alert", "plot", "honestly", "currently", "lately", "recently", "finally", "basically", "seriously",
    "personally", "meanwhile", "otherwise", "again", "still", "hmm", "hm", "ugh", "wow", "yay", "imo",
    "imho", "idk", "fwiw", "afaik", "pros", "cons", "mood", "vibe", "vibes",
    # Events on a line with a time ("Dinner 8:00 PM") are the user's plans,
    # never who spoke (the copy headers of the third review, item 3).
    "dinner", "lunch", "breakfast", "meeting", "call", "class", "flight", "appointment", "gym", "practice",
    "shift", "deadline", "event", "party", "pickup", "dropoff", "reminder", "alarm"})
#: A role in a conversation or a person named by relation, after "the" or a
#: possessive ("the customer:", "my sister:", "his boss:"): who spoke.
_ROLES_EN = frozenset({
    "customer", "client", "agent", "support", "rep", "representative", "interviewer", "interviewee", "host",
    "guest", "caller", "operator", "seller", "buyer", "landlord", "tenant", "driver", "recruiter",
    "candidate", "patient", "lawyer", "officer", "cashier", "waiter", "bot", "chatbot", "assistant", "ai",
    "moderator", "admin", "owner", "author", "sender", "student", "teacher", "doctor", "nurse", "boss",
    "manager", "colleague", "coworker", "co-worker", "friend", "sister", "brother", "mother", "mom", "mum",
    "father", "dad", "wife", "husband", "partner", "son", "daughter", "cousin", "uncle", "aunt",
    "neighbour", "neighbor", "roommate", "flatmate", "boyfriend", "girlfriend", "fiance", "fiancee",
    "grandma", "grandpa", "grandmother", "grandfather", "niece", "nephew"})
_OWNERS_EN = frozenset({"the", "my", "his", "her", "their", "our", "your"})
#: A verb at a label's start is the user asking Kazma, never who spoke
#: ("Save: My Grok weekly next reset: August 10", "Add: ...", "Log: ...").
#: Never a verb people carry as a name ("Will", "Mark", "Bill").
_REQUEST_VERBS_EN = frozenset({
    "save", "keep", "store", "memorize", "memorise", "remember", "log", "record", "add", "track", "jot",
    "set", "schedule", "book", "remind", "update", "change", "delete", "forget", "list", "show", "find",
    "search", "send", "buy", "get", "make", "do", "fix", "correct", "edit", "write", "draft", "compose",
    "translate", "summarize", "summarise", "check", "review", "cancel", "plan", "note", "tell", "ask",
    "call", "email", "text", "reply", "answer", "help", "use", "try", "read", "open", "create"})
#: Labels before a colon that are the user's own, or a form they fill in
#: about themself: never a speaker ("Correct: my name is Ahmad", "Name: Sam",
#: "News: I got the job"). Compared lower-cased (English) or folded (Arabic).
_OWN_LABELS = frozenset({
    "me", "i", "my", "myself", "mine", "note", "notes", "correct", "correction", "update", "updates", "edit",
    "remember", "reminder", "also", "ps", "p.s", "fyi", "btw", "answer", "todo", "important", "info",
    "context", "background", "actually", "yes", "no", "ok", "okay", "question", "q", "a", "re", "name",
    "age", "city", "job", "email", "phone", "address", "country", "location", "title", "role", "company",
    "status", "goal", "goals", "plan", "news", "fact", "facts", "tip", "summary", "today", "yesterday",
    "tomorrow", "now", "here", "result", "reason", "detail", "details", "about", "bio", "profile", "hey",
    "hi", "hello", "thanks", "anyway", "story", "home", "work", "hobby", "hobbies", "interests",
    "profession", "occupation", "employer", "birthday", "nationality", "language", "languages", "pronouns",
    "school", "university", "degree", "skills", "username", "website", "timezone", "warning", "heads",
    "انا", "اسمي", "ملاحظه", "تصحيح", "تحديث", "تذكير", "تذكر", "مهم", "للعلم", "سؤال", "جواب", "نعم", "لا",
    "الاسم", "العمر", "المدينه", "العمل", "الوظيفه", "الهاتف", "العنوان", "خبر", "اخبار", "معلومه",
    "معلومات", "فكره", "المهم", "سالفه", "قصه", "نبذه", "عني", "اليوم", "امس", "البارحه", "بكره", "الحين",
    "حاليا", "اخيرا", "بالمناسبه", "ترى", "يعني", "بس", "صراحه", "توضيح", "للتوضيح", "تنبيه", "مثال",
})
#: Lead-in words before a question's first word ("Hey Kazma, how do I ...").
_LEAD_INS_EN = frozenset({"hey", "hi", "hello", "ok", "okay", "so", "and", "but", "also", "kazma", "please",
                          "pls", "um", "well", "btw", "oh", "yo", "then"})
_LEAD_INS_AR = frozenset({"يا", "كازما", "هلا", "مرحبا", "طيب", "اوكي", "بس", "يعني"})
#: An address in a message: its ":" and "?" are no head's end.
_URL_RE = re.compile(r"https?://[^\s<>\"]{1,2000}+")
#: An instruction whose text is an address ("Summarize https://x.y and my
#: name is Sam"): the address is all it works on.
_URL_OBJECT_GAP = 40

#: A forwarded message's header, at a line's start: what follows is someone
#: else's. Compared lower-cased without leading dashes, ">" or spaces
#: (English) or folded (Arabic).
_FORWARD_EN = ("forwarded message", "begin forwarded message", "forwarded from ", "fwd:",
               "original message", "from:", "subject:", "to:", "date:", "sent:", "cc:")
_FORWARD_AR = ("رساله محوله", "رساله معاد توجيهها", "تمت اعاده توجيه", "محوله من", "رساله معاد ارسالها")

#: Direction marks a copied Arabic chat carries around its numbers: left out
#: before a line is matched.
_BIDI_MARKS = dict.fromkeys((0x200E, 0x200F, 0x061C, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069))
#: A line of a WhatsApp export: a date, a time, then who spoke and a colon
#: ("18/11/2026, 12:01 - Dana: ...", "[18/11/2026, 12:01:33] Dana: ...",
#: "١٨/١١/٢٠٢٦، ١٢:٠١ م - نورة: ..."). Any label: the date and time say the
#: line was copied.
_EXPORT_LINE_RE = re.compile(
    r"[ \t]{0,8}+\[?+\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}[,،]?+[ \t]{0,3}+\d{1,2}:\d{2}(?::\d{2})?+"
    r"(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+\]?+[ \t]{0,3}+(?:[-–—][ \t]{0,3}+)?+[^:\n]{1,60}+:"
)
#: The header a Telegram copy puts over each message: "Dana Smith, [18.11.26
#: 12:01]", "نورة، [18.11.26 12:01]".
_TELEGRAM_HEADER_RE = re.compile(
    r"[ \t]{0,8}+[^,،\n\[\]:]{1,60}+[,،][ \t]{0,3}+\[\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}[,،]?+[ \t]{0,3}+"
    r"\d{1,2}:\d{2}(?::\d{2})?+(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+\][ \t]{0,8}+$"
)
#: Discord's: "Dana — Today at 12:01 PM", "dana_92 — 11/18/2026 12:01 PM".
_DISCORD_HEADER_RE = re.compile(
    r"[ \t]{0,8}+[^—–\n:]{1,60}+[—–][ \t]{0,3}+"
    r"(?:(?:[Tt]oday|[Yy]esterday|اليوم|أمس|امس)[ \t]{1,3}+(?:at[ \t]{1,3}+|في[ \t]{1,3}+)?+"
    r"|\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}[,،]?+[ \t]{1,3}+)"
    r"\d{1,2}:\d{2}(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+[ \t]{0,8}+$"
)
#: Slack's: "Dana Smith  12:01 PM", or the time on a line of its own under
#: the name ("12:01 PM").
_SLACK_HEADER_RE = re.compile(
    r"[ \t]{0,8}+[^\s:][^\n:]{0,60}?[ \t]{2,8}+\d{1,2}:\d{2}(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+"
    r"[ \t]{0,8}+$"
)
_CLOCK_LINE_RE = re.compile(
    r"[ \t]{0,8}+\[?+\d{1,2}:\d{2}(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+\]?+[ \t]{0,8}+$"
)
#: A mail's quote header ends its line with "wrote:" ("On Tue, 18 Nov 2026
#: at 12:01, Dana <dana@x.y> wrote:"; Gmail in Arabic: "في الثلاثاء ...، كتب
#: Dana <...>:"). Read at most this far.
_WROTE_LINE_MAX = 300
#: English, French ("Le ... a écrit :") and, read apart, German ("Am ...
#: schrieb Dana <...>:").
_WROTE_ENDS = ("wrote:", "writes:", "wrote :", "a écrit :", "a écrit:", "a ecrit :", "a ecrit:")
_WROTE_INSIDE = (" schrieb ",)
#: Any other header a copied chat puts over a message: who, then only a time
#: -- "Dana, [18 Nov 2026 at 12:01]", "Dana [12:01 PM]", "Dana 12:01 PM",
#: "Dana — 12:01", "Dana - Today at 12:01", "Dana, 12:01 PM" (the third
#: review of F1, item 3). The name must name a speaker (``_names_a_speaker``):
#: "Meeting at 3:00 PM" is no header.
_HEADER_RE = re.compile(
    r"[ \t]{0,8}+(?P<name>[^\n:\[\]—–,]{1,60}?)"
    r"(?:,[ \t]{0,3}+|[ \t]{0,8}+[—–\-][ \t]{0,3}+|[ \t]{1,8}+)"
    r"(?:\[[^\]\n]{0,40}?\d{1,2}:\d{2}[^\]\n]{0,16}+\]"
    r"|(?:(?:[Tt]oday|[Yy]esterday|اليوم|أمس|امس)[ \t]{1,3}+(?:at[ \t]{1,3}+|في[ \t]{1,3}+)?+)?+"
    r"(?:\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}[,،]?+[ \t]{1,3}+)?+"
    r"\d{1,2}:\d{2}(?:[ \t]?+(?:[AaPp]\.?+[ \t]?+[Mm]\.?+|[صم]))?+)[ \t]{0,8}+$"
)

#: How far a head may run before its colon or line end.
_HEAD_MAX = 80
_SENTENCE_ENDS = ".!?؟"


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text)


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _speaks_in_first_person(span: str) -> bool:
    if any(w in _FIRST_PERSON_EN for w in _words(span)):
        return True
    # By word, never by space-split token: a quote mark stuck to a word
    # ("«اسمي") hid it.
    words = _WORD_RE.findall(_fold(span))
    for i, w in enumerate(words):
        if w in _FIRST_PERSON_AR or (len(w) > 3 and w[0] in "وف" and w[1:] in _FIRST_PERSON_AR):
            return True
        # "لوني المفضل", "أكلتي المفضلة": the speaker's own favourite.
        if len(w) > 2 and w.endswith("ي") and i + 1 < len(words) and words[i + 1] in ("المفضل", "المفضله"):
            return True
    return False


def _strip_polite_en(head: str) -> str:
    changed = True
    while changed:
        changed = False
        for word in _POLITE_EN:
            if head.startswith(word + " ") or head.startswith(word + ","):
                head = head[len(word):].lstrip(" ,!.")
                changed = True
    return head


#: Heads that look like an instruction and are not one.
_NOT_INSTRUCTIONS_EN = ("check this out", "check it out", "check out", "check my email", "check my mail",
                        "check my inbox", "check my messages", "write down", "write this down",
                        "write it down", "write that down", "note down")

#: What a head introduces: the text an instruction works on, or the words
#: someone else said.
_INSTRUCTION = "instruction"
_REPORTED = "reported"


def _starts_with_text_verb_en(head: str) -> bool:
    head = _strip_polite_en(head.lower().replace("’", "'").strip())
    return any(head == verb or head.startswith(verb + " ") for verb in (*_TEXT_VERBS_EN, *_HYPOTHETICAL_EN))


def _names_someones_text_en(said: list[str]) -> bool:
    """Whether the head's words *said* name a text of someone else's: "Dana's
    bio", "Her message was", "My sister's reply", "Ali Hassan's post"."""
    for k, owner in enumerate(said[:3]):
        if owner in _SOMEONES_EN or (owner.endswith("'s") and len(owner) > 3 and owner not in _NOT_POSSESSIVE_EN):
            return k + 1 < len(said) <= k + 4 and said[k + 1] in _SOMEONES_TEXT_EN
    return False


def _remembers_pasted_text_en(words: list[str]) -> bool:
    """Whether the head (courtesy words left out) asks to remember a text the
    user pasted: "remember this email", "save the message below" -- never
    "remember my email", "remember this email address"."""
    if not words or words[0] not in _REMEMBER_VERBS_EN or any(w in _FIRST_PERSON_EN for w in words):
        return False
    for k in range(1, min(len(words), 5)):
        if words[k] not in _PASTED_THINGS_EN:
            continue
        after = words[k + 1] if k + 1 < len(words) else ""
        if after in _DETAIL_WORDS_EN:
            return False
        return any(w in _THIS_EN for w in words[1:k]) or after in _THIS_EN
    return False


def _head_kind_en(head: str, *, colon: bool) -> str | None:
    said = _words(head)  # "Kazma said:": the name is the speaker here, not a courtesy
    head = _strip_polite_en(head.lower().replace("’", "'").strip())
    for verb in (*_TEXT_VERBS_EN, *_HYPOTHETICAL_EN):
        if head == verb or head.startswith(verb + " "):
            return _INSTRUCTION
    words = _words(head)
    if any(head.startswith(idiom) for idiom in _NOT_INSTRUCTIONS_EN):
        return None  # "Please check my email:", "Write this down:": the user's own
    for verb in _EDIT_VERBS_EN:
        if head.startswith(verb + " ") and any(w in _TEXT_OBJECTS_EN for w in words[len(verb.split()):]):
            return _INSTRUCTION
    if any(head.startswith(verb + " ") for verb in _WRITE_VERBS_EN) and \
            any(w in _WRITTEN_THINGS_EN for w in words[1:]):
        return _INSTRUCTION
    if colon and any(w in _TEXT_WORK_EN for w in words):
        return _INSTRUCTION
    if not colon or not said:
        return None
    # "For example:", "e.g.:" -- an example states nothing.
    bare = head.rstrip(" .,")
    if any(bare == p or bare.startswith(p + " ") for p in _EXAMPLE_HEADS_EN):
        return _INSTRUCTION
    # "Remember this email:" -- the user asks to keep a text they pasted.
    if _remembers_pasted_text_en(words):
        return _INSTRUCTION
    # "Here is the article:" -- a text the user hands over, never "Here is my
    # info:" (the user's own).
    if head.startswith(_HANDS_OVER_EN) and not _speaks_in_first_person(head):
        return _INSTRUCTION
    # "Tell me if this is spam:", "What does this mean:" -- a question about
    # the text that follows. Read after the courtesy words, so "Can you
    # remember this:" is never one.
    if words and words[0] in _ASKS_ABOUT_EN and any(w in _TEXT_OBJECTS_EN for w in words[1:]):
        return _INSTRUCTION
    # "Message from Dana:" -- a text named by who sent it.
    if said[0] in _MESSAGE_THINGS_EN and "from" in said[1:3]:
        return _REPORTED
    # "Dana's bio:", "Her message was:" -- a text of someone else's.
    if _names_someones_text_en(said):
        return _REPORTED
    # "she said:", "my boss wrote:", "they told me:", "You said:" -- someone
    # else's words. A colon only (with none, "My sister said I should" is a
    # sentence ``_reported_cut`` reads), and never the user's own ("As I
    # said:").
    if (len(said) <= _REPORTED_HEAD_WORDS and any(w in _REPORTING_EN for w in said[1:])
            and not any(w.split("'")[0] in {"i", "we"} for w in said)):
        return _REPORTED
    return None


def _ar_words(head: str) -> list[str]:
    """Folded words of *head* without the polite ones; "هال" ("this ...")
    read as the article: "هالجملة" is "الجملة"."""
    out = []
    for w in _WORD_RE.findall(_fold(head)):
        if w in _POLITE_AR:
            continue
        out.append("ال" + w[3:] if w.startswith("هال") and len(w) > 4 else w)
    return out


def _ar_verb_forms(word: str) -> tuple[str, ...]:
    first = word[1:] if len(word) > 3 and word[0] in "وف" else word
    return (first, first[1:]) if first.startswith("ت") and len(first) > 3 else (first,)


def _starts_with_text_verb_ar(head: str) -> bool:
    words = _ar_words(head)
    return bool(words) and any(v in _TEXT_VERBS_AR or v in _HYPOTHETICAL_AR for v in _ar_verb_forms(words[0]))


def _names_someones_text_ar(words: list[str]) -> bool:
    """Whether the folded head *words* name a text of someone else's:
    "رسالتها", "كلامه", "رسالة دانة", "نبذة علي" -- never "رسالتي" or "نبذة
    عني"."""
    if not 1 <= len(words) <= 4:
        return False
    first = words[0]
    if any(first.endswith(s) and first[:-len(s)] in _TEXT_STEMS_AR for s in _THIRD_SUFFIXES_AR):
        return True
    return (first in _TEXT_NOUNS_AR and len(words) >= 2 and words[1] not in _OWN_LABELS
            and not _speaks_in_first_person(words[1]))


def _remembers_pasted_text_ar(words: list[str]) -> bool:
    """Whether the folded head *words* ask to remember a pasted text: "احفظ
    هذا الإيميل", "تذكر هالرسالة"."""
    if not words or not any(v in _REMEMBER_VERBS_AR for v in _ar_verb_forms(words[0])):
        return False
    return any(w in _PASTED_THINGS_AR for w in words[1:5])


def _head_kind_ar(head: str, *, colon: bool) -> str | None:
    words = _ar_words(head)
    if not words:
        return None
    for verb in _ar_verb_forms(words[0]):
        if verb in _TEXT_VERBS_AR or verb in _HYPOTHETICAL_AR:
            return _INSTRUCTION
        if verb in _EDIT_VERBS_AR and any(w in _TEXT_OBJECTS_AR for w in words[1:]):
            return _INSTRUCTION
        if verb in _WRITE_VERBS_AR and any(w in _WRITTEN_THINGS_AR for w in words[1:]):
            return _INSTRUCTION
    if colon and any(w in _TEXT_WORK_AR for w in words):
        return _INSTRUCTION
    if not colon or len(words) > _REPORTED_HEAD_WORDS:
        return None
    if any(" ".join(words) == p or " ".join(words).startswith(p + " ") for p in _EXAMPLE_HEADS_AR):
        return _INSTRUCTION  # "مثلا:": an example states nothing
    if _remembers_pasted_text_ar(words):
        return _INSTRUCTION  # "احفظ هذا الإيميل:": a pasted text to keep
    if _names_someones_text_ar(words):
        return _REPORTED  # "رسالتها:", "نبذة علي:"
    # "هذا المقال:" -- a text handed over (never "هذا اسمي:"); "وش معنى هذا:",
    # "هل هذي الجملة صحيحة:" -- a question about the text that follows.
    if words[0] in _HANDS_OVER_AR and not _speaks_in_first_person(head):
        return _INSTRUCTION
    if words[0] in _ASKS_ABOUT_AR and any(w in _TEXT_OBJECTS_AR for w in words):
        return _INSTRUCTION
    told_me = any(w in _YOU_TOLD_ME_AR or (w == "قلت" and nxt == "لي")
                  for w, nxt in zip(words, [*words[1:], ""]))
    if told_me or (any(w in _REPORTING_AR for w in words) and not any(w in _I_AR for w in words)):
        return _REPORTED
    return None


def _head_kind(head: str, *, colon: bool) -> str | None:
    """Either language's rules: a head may mix them ("translate للعربي:",
    "ترجم to English:")."""
    return _head_kind_en(head, colon=colon) or _head_kind_ar(head, colon=colon)


def _starts_with_text_verb(head: str) -> bool:
    return _starts_with_text_verb_en(head) or _starts_with_text_verb_ar(head)


def _starts_with(text: str, english: tuple[str, ...], arabic: tuple[str, ...]) -> bool:
    lowered = " ".join(text.lower().replace("’", "'").split())
    if any(lowered.startswith(p) for p in english):
        return True
    folded = " ".join(_fold(text).split())
    return any(folded.startswith(p) for p in arabic)


def _head_starts(body: str) -> list[int]:
    """Where a head may start: the message's start, a line's start, and after
    the end of a sentence."""
    starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            starts.append(i + 1)
        elif ch in _SENTENCE_ENDS and i + 1 < len(body) and body[i + 1] in " \t":
            starts.append(i + 2)
    return starts


def _sentence_end(body: str, at: int) -> int:
    """Where the sentence holding *at* ends: after its closing mark, or at its
    line's end."""
    n = len(body)
    i = at
    while i < n:
        ch = body[i]
        if ch == "\n":
            return i
        if ch in _SENTENCE_ENDS and (i + 1 == n or body[i + 1] in " \t\n"):
            return i + 1
        i += 1
    return n


def _reported_end(body: str, at: int) -> int:
    """Where someone else's words that start at *at*, after their head's
    colon, end: at the end of their sentence when they start on the head's
    line (the decided cost: "My sister said: I'm a nurse. I live in
    Rotterdam." files the second sentence as the user's), else at the end
    of the message ("Dana wrote:\\nI live in Oslo.\\n\\nMy name is Dana."):
    what someone wrote has paragraphs of its own."""
    eol = body.find("\n", at)
    if body[at:eol if eol >= 0 else len(body)].strip():
        return _sentence_end(body, at)
    return len(body)


def _without_addresses(window: str) -> str:
    """*window* with each web address's characters blanked: an address's
    ":" and "?" end no head ("Summarize https://x.y?ref=1 and ...")."""
    return _URL_RE.sub(lambda m: "x" * len(m.group(0)), window)


def _is_aside(body: str, at: int) -> bool:
    text = body[at:at + 40]
    if _starts_with(text, _ASIDES_EN, _ASIDES_AR):
        return True
    return (at == 0 or body[at - 1] == "\n") and _starts_with(text, _LINE_ASIDES_EN, _LINE_ASIDES_AR)


def _cut(body: str) -> str:
    """*body* without the text its heads introduce: an instruction's text
    runs to the end of the message (or to an aside after it), or is the web
    address it names; someone else's words run to the end of their
    sentence."""
    return _cut_open(body)[0]


def _cut_open(body: str) -> tuple[str, bool]:
    """:func:`_cut`, and whether an instruction's text runs to the end of
    *body* (no aside ended it)."""
    kept: list[str] = []
    pos = 0
    starts = _head_starts(body)
    i = 0
    while i < len(starts):
        start = starts[i]
        i += 1
        if start < pos:
            continue  # inside text already dropped
        window = _without_addresses(body[start:start + _HEAD_MAX + 1])
        stops = [p for p in (window.find(":"), window.find("："), window.find("\n"), window.find("?"),
                             window.find("؟")) if p > 0]
        if not stops:
            if not _starts_with_text_verb(window):
                continue
            url = _URL_RE.search(body, start, start + _HEAD_MAX + 1)
            if url is not None and url.start() - start <= _URL_OBJECT_GAP:
                # "Summarize https://x.y and my name is Sam": the address is
                # the text; what follows it is the user's again.
                whole = _URL_RE.match(body, url.start())
                kept.append(body[pos:start])
                pos = whole.end() if whole is not None else url.end()
                continue
            # "Translate to French My name is Pierre": no colon, the rest of
            # the message is the text.
            end, kind = start, _INSTRUCTION
        else:
            stop = min(stops)
            kind = _head_kind(window[:stop], colon=window[stop] != "\n")
            if kind is None:
                continue
            end = start + stop + 1
        if kind == _REPORTED:
            # Someone else's words, head and all ("My sister said: ..."), to
            # the end of their sentence, or of the paragraph under a head that
            # ends its line ("Dana wrote:\n..."); the rules after this one
            # never read the head again.
            kept.append(body[pos:start] + " ")
            pos = _reported_end(body, end)
            continue
        # An instruction: its text runs to the end of the message, or to an
        # aside.
        kept.append(body[pos:end])
        while i < len(starts) and (starts[i] < end or not _is_aside(body, starts[i])):
            i += 1
        if i == len(starts):
            return "".join(kept), True
        kept.append("\n")  # the aside is a sentence of its own
        pos = starts[i]
    kept.append(body[pos:])
    return "".join(kept), False


#: Subjects that make a sentence the user's own report ("I told Ali I live in
#: Paris", "As I said, ..."), never someone else's.
_SUBJECT_I = frozenset({"i", "i'm", "im", "i've", "i'd", "i'll", "we", "we're", "we've", "انا", "احنا",
                        "نحن"})
#: Words between a reporting verb and what was reported ("told me I ...",
#: "said that I ...", "قالت لي أنا ...").
#: "Dana told everyone I ...", "It says here I ..." (the third review, item 3).
_MORE_FILLERS = frozenset({"you", "everyone", "everybody", "people", "all", "here"})
_REPORT_FILLERS = frozenset({"me", "us", "him", "her", "them", "that", "لي", "له", "لها", "ان"}) | _MORE_FILLERS
#: "إني", "إنني", "إننا": "that I", "that we" after a reporting verb.
_THAT_I_AR = frozenset({"اني", "انني", "اننا"})
#: The most of a message the scanners read. Scanning all of a long one cost
#: about six microseconds a line (12 s for 2 MiB, the review of F3), and a
#: long message is mostly a document, not the user speaking of themself.
_SCAN_MAX = 2000
#: Reporting verbs that may end a sentence after a comma ("My name is Dana,
#: she said."). Never a verb that is also a noun or an order ("رد": reply).
_TAIL_REPORTING = frozenset({"said", "says", "added", "adds", "wrote", "writes", "replied", "explained",
                             "answered", "told", "asked", "insisted", "قال", "قالت", "قالوا", "قالو", "يقول",
                             "تقول", "يقولون", "كتبت", "اضاف", "اضافت", "ردت"})
#: Words that start a clause of the user's own after a comma, never someone
#: named as the speaker ("..., which my boss says is great").
_TAIL_OPENERS = frozenset({"as", "like", "which", "who", "that", "and", "but", "so", "because", "since",
                           "when", "while", "if", "although", "though", "كما", "مثل", "زي", "اللي", "الذي",
                           "التي", "لان", "لكن", "و"})


def _bounded(body: str) -> str:
    """The first ``_SCAN_MAX`` characters of *body*, ended at its last
    paragraph break (a blank line) before them, so no quote, sentence or
    line is read cut in half; nothing, when the first ``_SCAN_MAX``
    characters hold no paragraph break (a document). Never its end: a head,
    a fence or a forward line in the part not read would decide what the end
    is, and the end would read as the user's. The turn keeps the whole
    message and the deep pass reads it; its reading of a value the end holds
    is put to the user (:func:`last_words`), never given their trust."""
    if len(body) <= _SCAN_MAX:
        return body
    end = body.rfind("\n\n", 0, _SCAN_MAX)
    return body[:end] if end > 0 else ""


def _is_greeting(label: str) -> bool:
    """Whether *label* greets: "Good Morning", "hi all", "صباح الخير"."""
    lowered = " ".join(label.lower().replace("’", "'").split())
    if lowered in _GREETINGS_EN:
        return True
    folded = _WORD_RE.findall(_fold(label))
    return bool(folded) and folded[0] in _GREETINGS_AR


def _handle(label: str) -> bool:
    """Whether *label* is a chat handle: one token of letters joined with
    digits, "_", "." or a leading "@" ("dana_92", "user123", "@dana",
    "dana.k"), never a step or a version ("Q1", "v2")."""
    token = label.strip()
    if not 3 <= len(token) <= 30 or not all(ch.isalnum() or ch in "_.@-" for ch in token):
        return False
    if sum(ch.isalpha() for ch in token) < 2:
        return False
    return (token.startswith("@") or "_" in token or any(ch.isdigit() for ch in token)
            or "." in token.strip("."))


def _names_a_role(words: list[str]) -> bool:
    """Whether *words* name who spoke by role or relation: "the customer",
    "my sister", "his boss", "my best friend", or a numbered role ("User
    1", "Person A", "Speaker 2")."""
    lowered = [w.lower() for w in words]
    if 2 <= len(lowered) <= 3 and lowered[0] in _OWNERS_EN and lowered[-1] in _ROLES_EN:
        return True
    return False


def _numbered_role(label: str) -> bool:
    parts = label.split()
    return (len(parts) == 2 and parts[0].lower() in _NUMBERED_ROLES_EN
            and (parts[1].isdigit() or (len(parts[1]) == 1 and parts[1].isalpha() and parts[1].isupper())))


def _bare_label(label: str) -> str:
    """*label* without what surrounds a name: a parenthetical ("Dana (my
    sister)"), "from ..." ("Dana from HR"), titles ("Dr. Dana Smith Jr.")."""
    label = _PARENTHETICAL_RE.sub(" ", label)
    lowered = f" {label.lower()} "
    cut = lowered.find(" from ")
    if cut > 0:
        label = label[:cut - 1] if cut - 1 > 0 else label
    words = [w for w in label.replace(".", " ").split() if w.lower() not in _TITLES_EN]
    return " ".join(words)


def _names_a_speaker(label: str) -> bool:
    """Whether *label*, before a colon at a line's start, names who spoke a
    pasted line: a name ("Dana", "Ali Hassan", "نورة"), a handle
    ("dana_92"), a role ("the customer", "my sister"), or a single
    lower-case name ("dana"). Never the user, one of the user's own labels or
    a form field, a greeting ("Good morning"), a heading ("Random thought",
    "Step 2"), a phrase in lower case ("just saying"), or a head of its own
    (an instruction, someone reported)."""
    label = label.strip()
    if _PHONE_RE.fullmatch(label) and sum(ch.isdigit() for ch in label) >= 7:
        return True  # "+965 9999 1234:": a contact an export shows by number
    if label in _SIDES:
        return True  # "A:", "B:": a transcript's two sides
    if _numbered_role(label):
        return True
    if not _handle(label):
        label = _bare_label(label) or label
    words = _WORD_RE.findall(label)
    if not 1 <= len(words) <= 3 or _is_greeting(label):
        return False
    if _names_a_role(words):
        return True
    lowered = [w.lower().replace("’", "'") for w in words]
    if any(w in _OWN_LABELS or w in _HEADING_WORDS for w in lowered) or any(
            _fold(w) in (_OWN_LABELS | _POLITE_AR | _I_AR) for w in words):
        return False  # "كما قلت:": the user's own report
    if lowered[0] in _REQUEST_VERBS_EN:
        return False  # "Save:", "Add:": the user asking
    if _speaks_in_first_person(label) or _head_kind(label, colon=True) is not None:
        return False
    # Arabic has no capitals: a verb Kazma knows is no name ("صحح: اسمي أحمد"
    # is the user correcting themself).
    if any(v in _AR_VERBS for w in words if not w[0].isascii() for v in _ar_verb_forms(_fold(w))):
        return False
    if _handle(label):
        return True
    if any(ch.isdigit() for ch in label):
        return False  # "Step 2", "Day 3"
    # "Ali Hassan:" names someone; "Fun fact:" is a label; a single word in
    # lower case is a name typed so ("dana:"), a phrase is the user's own.
    return all(w[0].isupper() for w in words if w[0].isascii()) or len(words) == 1


def _copied_label(match: re.Match[str]) -> bool:
    """Whether a speaker line's bracket holds a time: any label then names
    who spoke ("[12:01, 18/11/2026] dana_92: ...")."""
    return bool(match.group(1)) and _CLOCK_RE.search(match.group(1)) is not None and bool(match.group(2).strip())


def _without_speaker_lines(body: str) -> str:
    """*body* without the lines of a pasted chat or transcript: who spoke,
    then a colon, at a line's start ("Dana: My name is Dana", "[12:01,
    18/11/2026] Dana: ...", "نورة: اسمي نورة")."""
    out = []
    for line in body.split("\n"):
        lead = _LABEL_LEAD_RE.match(line.lstrip(" \t"))
        bare = line.lstrip(" \t")[lead.end():] if lead else line
        match = _SPEAKER_LINE_RE.match(bare)
        angle = _ANGLE_LINE_RE.match(line)
        spoken = (match is not None and (_copied_label(match) or _names_a_speaker(match.group(2)))) or (
            angle is not None and _names_a_speaker(angle.group(1)))
        out.append("" if spoken else line)
    return "\n".join(out)


def _is_copy_header(line: str) -> bool:
    """Whether *line* (direction marks left out) is the header a Telegram,
    Discord or Slack copy puts over a message, or a time alone on its line:
    the message follows on the next lines."""
    if (_TELEGRAM_HEADER_RE.match(line) or _DISCORD_HEADER_RE.match(line) or _SLACK_HEADER_RE.match(line)
            or _CLOCK_LINE_RE.match(line)):
        return True
    header = _HEADER_RE.match(line)
    return header is not None and _names_a_speaker(header.group("name"))


def _is_wrote_line(line: str) -> bool:
    """Whether *line* heads a quoted mail: it ends with "wrote:" and names no
    "I" or "we" ("On Tue, 18 Nov 2026 at 12:01, Dana <dana@x.y> wrote:", "Dana
    wrote:"; never "As I wrote:"), or it is Gmail's Arabic header ("في
    الثلاثاء ...، كتب Dana <...>:")."""
    text = line.strip().lstrip(">").strip()
    if not text or len(text) > _WROTE_LINE_MAX:
        return False
    lowered = text.lower()
    if lowered.endswith(_WROTE_ENDS) or (lowered.endswith(":") and any(m in f" {lowered} " for m in _WROTE_INSIDE)):
        return not any(w.split("'")[0] in {"i", "we", "ich", "je"} for w in _words(text))
    folded = _fold(text).split()
    return text.endswith(":") and len(folded) > 2 and folded[0] == "في" and (
        "كتب" in folded or "كتبت" in folded)


def _without_pasted_chats(body: str) -> str:
    """*body* without a pasted chat or quoted mail: a WhatsApp export's lines
    with the lines that continue them, to the next blank line; and from a
    copy header (Telegram, Discord, Slack, any "Name, time" line) or a mail's
    "... wrote:" line, everything after it -- a copied mail or chat has
    paragraphs of its own (the third review of F1, item 3)."""
    lines = body.split("\n")
    out: list[str] = []
    dropping = False
    for at, line in enumerate(lines):
        bare = line.translate(_BIDI_MARKS)
        if not bare.strip():
            dropping = False
            out.append(line)
            continue
        if _is_copy_header(bare) or _is_wrote_line(bare):
            out.extend("" for _ in lines[at:])
            break
        if _EXPORT_LINE_RE.match(bare):
            dropping = True
            out.append("")
        else:
            out.append("" if dropping else line)
    return "\n".join(out)


def _without_quote_lines(body: str) -> str:
    """*body* without ``>`` quote lines, except those right under a request
    to remember ("Remember this:\n> my code is ZX-41"): the statement the
    user asks to be kept, read without its mark. Not under a request that
    reports someone ("Remember what my sister said:")."""
    from kazma_core.memory.remember_request import asks_to_remember

    out = []
    keeps = False
    for line in body.split("\n"):
        if _QUOTE_LINE_RE.match(line):
            out.append(line.lstrip(" \t")[1:] if keeps else " ")
            continue
        out.append(line)
        if line.strip():
            keeps = asks_to_remember(line) and not _reports(line)
    return "\n".join(out)


def _reports(text: str) -> bool:
    """Whether *text* names someone saying something ("Remember what my
    sister said:", "remember that Dana wrote")."""
    return any(w in _REPORTING_EN for w in _words(text)) or any(
        w in _REPORTING_AR for w in _WORD_RE.findall(_fold(text)))


def _sentences(body: str) -> list[str]:
    """*body* cut into its sentences and lines, which join back into it."""
    out: list[str] = []
    start = 0
    n = len(body)
    for i, ch in enumerate(body):
        if ch == "\n" or (ch in _SENTENCE_ENDS and (i + 1 == n or body[i + 1] in " \t\n")):
            out.append(body[start:i + 1])
            start = i + 1
    if start < n:
        out.append(body[start:])
    return out


def _reporting(word: str) -> bool:
    return word in _REPORTING_EN or word in _REPORTING_AR


def _named_after_comma(tokens: list[tuple[int, str]], folded: list[str], comma: int) -> bool:
    """Whether the words after *comma* only name who said the sentence
    ("..., she said.", "..., wrote Dana in her letter.", "..., قالت أختي"):
    a reporting verb among their first four, at most eight words, nobody of
    them the user, and not a clause of the user's own ("..., as my sister
    said", "..., which my boss says is great")."""
    tail = [w for (at, _), w in zip(tokens, folded, strict=True) if at > comma]
    if not 0 < len(tail) <= 8 or tail[0] in _TAIL_OPENERS:
        return False
    return (any(w in _TAIL_REPORTING or w in _THINKING_EN for w in tail[:4])
            and not any(w in _SUBJECT_I for w in tail))


#: "like" reports speech after a form of "be" ("He was like, I'm moving",
#: "she's like ..."), unless "it", "that" or "this" is its subject ("It's
#: like I live in a cave").
_BE_FORMS_EN = frozenset({"was", "is", "were", "are", "be", "been"})
_NOT_SPEAKERS_EN = frozenset({"it", "that", "this", "there", "it's", "that's", "this's", "there's",
                              "what's", "here's"})
#: "According to Ali, ...", "حسب كلام أختي، ...": the sentence is someone
#: else's claim. The Arabic words only at a sentence's start ("أشتغل حسب
#: الدوام" is the user's own); never "according to me", "حسب كلامي".
_ACCORDING_AR = frozenset({"حسب", "بحسب", "وحسب", "وفقا", "طبقا"})
_MY_SAYING_AR = frozenset({"كلامي", "رايي", "علمي", "معلوماتي", "قولي", "معرفتي"})


def _reports_at(folded: list[str], k: int) -> int | None:
    """The index of the last word of a reporting phrase at *k*, or None: a
    reporting verb ("said", "thinks", "كاتبة"), "was like" / "she's like",
    or "put it" ("As Dana put it, ...")."""
    word = folded[k]
    if _reporting(word):
        return k
    prev = folded[k - 1] if k > 0 else ""
    if word == "like" and prev and prev not in _NOT_SPEAKERS_EN:
        if prev in _BE_FORMS_EN:
            return None if k > 1 and folded[k - 2] in _NOT_SPEAKERS_EN else k
        if prev.endswith(("'s", "'re")):
            return k
    if word == "put" and k + 1 < len(folded) and folded[k + 1] == "it":
        return k + 1
    return None


def _attributed(folded: list[str]) -> bool:
    """Whether a sentence (its folded words) says whose claim it is:
    "According to Ali, I work at Initech", "I work at Initech, according to
    Ali", "حسب كلام أختي، أنا ساكن في دبي" -- never "according to me"."""
    for k, word in enumerate(folded):
        if word == "according" and k + 1 < len(folded) and folded[k + 1] == "to":
            return (folded[k + 2] if k + 2 < len(folded) else "") != "me"
    lead_en = [w for w in folded[:5] if w not in _LEAD_INS_EN]
    if lead_en:
        # "Per Ali, ...", "In Dana's words, ...", "Her reply was ..." (the
        # third review of F1, item 3); never "per me", "in my words".
        if lead_en[0] == "per" and len(lead_en) > 1 and lead_en[1] != "me":
            return True
        if (lead_en[0] == "in" and len(lead_en) > 2 and lead_en[2] == "words"
                and lead_en[1] not in ("my", "our")):
            return True
        if _names_someones_text_en(lead_en[:4]):
            return True
    if folded and (folded[0] == "مكتوب" or (
            len(folded) > 1 and folded[1] in ("كان", "كانت") and _names_someones_text_ar(folded[:1]))):
        return True  # "مكتوب في الرسالة ..." (it is written), "ردها كان ..." (her reply was)
    lead = [w for w in folded[:5] if w not in _LEAD_INS_AR]
    if len(lead) > 1 and lead[0] == "علي" and lead[1] in _ACCORDING_AR:
        lead = lead[1:]  # "على حسب" ("على" folds as the name "علي")
    if not lead or lead[0] not in _ACCORDING_AR:
        return False
    after = lead[1:3]
    if after and after[0] == "ما":
        # "حسب ما قالت أختي" reports; "حسب ما أعرف" is the user's own.
        return len(after) > 1 and _reporting(after[1])
    return not after or (after[0] not in _MY_SAYING_AR and after[0] not in _FIRST_PERSON_AR)


def _reports_on_the_user_at(folded: list[str], k: int) -> bool:
    """Whether the reporting verb at *k* is followed, past "me", "that" and
    the like, by a first-person word: someone's own words ("He said, I live
    in Lyon.", "أختي تقول أنا ساكنة") or their claim about the user ("Ali
    told me I work at Initech", "My friend says that I live in Lyon",
    "تقول إني ساكنة"). Neither is the user's own statement; the deep pass
    reads both."""
    nxt = k + 1
    while nxt < len(folded) and folded[nxt] in _REPORT_FILLERS and nxt < k + 4:
        nxt += 1
    if nxt >= len(folded):
        return False
    word = folded[nxt]
    return word in _FIRST_PERSON_EN or word in _FIRST_PERSON_AR or word in _THAT_I_AR


def _reported_cut(sentence: str) -> str:
    """*sentence* without what it reports someone else saying, with no colon
    to mark it: the words after a reporting verb that a first-person word
    follows ("He said, I live in Lyon.", "أختي تقول أنا ساكنة في دبي", "Ali
    told me I work at Initech", "Ali thinks I live in Lyon", "He was like,
    I'm moving", "As Dana put it, I'm ...") when the user is not its
    subject; or all of it, when it ends naming who said it ("My name is
    Dana, she said.", "I live in Oslo, wrote Dana in her letter.") or says
    whose claim it is ("According to Ali, ...", "حسب كلام أختي، ..."). Inside
    a request to remember too: "Remember that Ali says I work at Initech"
    asks to keep Ali's words."""
    end = "\n" if sentence.endswith("\n") else ""
    tokens = [(m.start(), m.group(0)) for m in _WORD_RE.finditer(sentence)]
    folded = [_fold(t).lower().replace("’", "'") for _, t in tokens]
    if not tokens:
        return sentence
    if _attributed(folded):
        return " " + end
    comma = max(sentence.rfind(","), sentence.rfind("،"))
    if comma > 0 and _named_after_comma(tokens, folded, comma):
        return " " + end
    for k in range(len(folded)):
        last = _reports_at(folded, k)
        if last is None:
            continue
        if k > 0 and folded[k - 1] in _NEGATIONS:
            return sentence[:tokens[k][0]] + end  # "I never said I live in Oslo"
        if any(w in _SUBJECT_I for w in folded[:k]):
            return sentence  # the user's own report ("I told Ali I ...")
        if _reports_on_the_user_at(folded, last):
            return sentence[:tokens[k][0]] + end
    return sentence


def _asks(sentence: str) -> bool:
    """Whether *sentence* is a question ("How do I say my name is Pierre in
    French?", "كيف أقول اسمي سارة بالإنجليزي؟"): it ends in a question mark and
    starts, after a greeting, with a question word. A question states
    nothing of the user; a request to remember asked as one does."""
    from kazma_core.memory.remember_request import asks_to_remember

    if not sentence.strip().rstrip("\"'”’»)").endswith(("?", "؟")) or asks_to_remember(sentence):
        return False
    for word in _WORD_RE.findall(sentence):
        lowered, folded = word.lower().replace("’", "'"), _fold(word)
        if lowered in _LEAD_INS_EN or folded in _LEAD_INS_AR:
            continue
        return lowered in _QUESTION_WORDS_EN or folded in _QUESTION_WORDS_AR
    return False


#: Before a reporting verb, a denial: "I never said ...", "I didn't say ...".
_NEGATIONS = frozenset({"never", "not", "didn't", "didnt", "don't", "dont", "doesn't", "doesnt", "won't",
                        "wouldn't", "can't", "cant", "cannot", "ما", "لم", "مو", "ابد", "ابدا"})
#: Where a hypothetical starts: "If I live in Oslo, ...", "لو كنت ساكن ...".
_SUPPOSING = frozenset({"if", "suppose", "supposing", "assuming", "imagine", "hypothetically", "lets", "let's",
                        "اذا", "لو", "فرضا", "افترض", "تخيل"})
#: Asking how to say something: "Is it correct to say I live in Oslo".
_ASKS_TO_SAY_EN = ("is it correct to say", "is it right to say", "is it ok to say", "is it okay to say",
                   "is it wrong to say", "is it proper to say", "can i say", "could i say", "should i say",
                   "how do i say", "how would i say", "how to say", "how do you say", "is this correct")
_ASKS_TO_SAY_AR = ("هل صحيح اقول", "هل يصح اقول", "هل يصح ان اقول", "هل اقدر اقول", "كيف اقول", "شلون اقول",
                   "شلون اكتب", "كيف اكتب")


def _supposes_or_asks_to_say(sentence: str) -> bool:
    """Whether *sentence* is a hypothetical ("If I live in Oslo, ...") or
    asks how to say something ("Is it correct to say I live in Oslo"): it
    states nothing of the user (the third review of F1, item 3)."""
    lowered = [w.lower().replace("’", "'") for w in _WORD_RE.findall(sentence)]
    lowered = [w for w in lowered if w not in _LEAD_INS_EN] or [""]
    folded = [_fold(w) for w in _WORD_RE.findall(sentence) if _fold(w) not in _LEAD_INS_AR] or [""]
    if lowered[0] in _SUPPOSING or folded[0] in _SUPPOSING or lowered[:2] in (["what", "if"], ["even", "if"]):
        return True
    said = " ".join(lowered)
    if any(said == p or said.startswith(p + " ") for p in _ASKS_TO_SAY_EN):
        return True
    joined = " ".join(folded)
    return any(joined == p or joined.startswith(p + " ") for p in _ASKS_TO_SAY_AR)


def _ends_reporting(sentence: str) -> bool:
    """Whether *sentence* ends with someone's reporting verb and no colon:
    "Dana said" on a line of its own, what she said on the next."""
    folded = [_fold(t).lower().replace("’", "'") for t in _WORD_RE.findall(sentence)]
    return bool(folded) and _reporting(folded[-1]) and not any(w in _SUBJECT_I for w in folded)


def _without_reported_or_asked(body: str) -> str:
    out = []
    after_report = False
    for sentence in _sentences(body):
        if not sentence.strip():
            out.append(sentence)
            continue
        if after_report or _asks(sentence) or _supposes_or_asks_to_say(sentence):
            out.append("\n" if sentence.endswith("\n") else " ")
            after_report = False
            continue
        out.append(_reported_cut(sentence))
        after_report = sentence.endswith("\n") and _ends_reporting(sentence)
    return "".join(out)


def _from_someone(line: str) -> bool:
    """Whether *line* heads a message from someone: "From Dana:", "From
    Dana Smith <dana@x.y>:" -- never "From now on: ..."."""
    if not line.lower().startswith("from "):
        return False
    colon = line.find(":", 5, 70)
    if colon < 0:
        return False
    name = line[5:colon].split("<")[0].strip()
    return bool(name) and _names_a_speaker(name)


def _without_forwarded(body: str) -> str:
    """*body* up to a forwarded message's header line."""
    start = 0
    while start <= len(body):
        end = body.find("\n", start)
        line = body[start:end if end >= 0 else len(body)][:120].lstrip(" \t>-*_")
        if line and (_starts_with(line, _FORWARD_EN, _FORWARD_AR) or _from_someone(line)):
            return body[:start]
        if end < 0:
            return body
        start = end + 1
    return body


def _without_quotes(body: str) -> str:
    """*body* without quoted first-person spans, except the one a request to
    remember names ("remember this: "my code is ZX-41""): that is the
    statement the user asks to be kept. Not when the request names someone
    saying it ("remember that Dana wrote "I live in Oslo"")."""
    from kazma_core.memory.remember_request import asks_to_remember

    def replace(match: re.Match[str]) -> str:
        span = match.group(0)
        if not _speaks_in_first_person(span):
            return span
        line_start = body.rfind("\n", max(0, match.start() - _HEAD_MAX), match.start()) + 1
        before = body[max(line_start, match.start() - _HEAD_MAX):match.start()]
        return span if asks_to_remember(before) and not _reports(before) else " "

    return _QUOTED_SPAN_RE.sub(replace, body)


def own_words(text: str | None) -> str:
    """*text* without what the user did not say about themself: data fences,
    attachment and forward lines, a forwarded message or pasted mail, a
    pasted chat (an export's lines, a copy's headers and messages, the lines
    of a transcript), quote blocks, quoted first-person sentences, someone
    else's words after a colon or a reporting phrase, the text an
    instruction works on, and questions. At most its first ``_SCAN_MAX``
    characters are read (``_bounded``)."""
    return _read(_bounded(_message_body(text)))[0]


#: How much of a long message's end :func:`last_words` reads when its last
#: paragraph is longer than ``_SCAN_MAX``, or it has no paragraph break.
_TAIL_MAX = 400
_FENCE_LINE_RE = re.compile(r"^[ \t]{0,8}+(?:```|~~~)", re.MULTILINE)
_FORWARD_LINE_RE = re.compile(
    r"^[ \t>*_\-]{0,40}+(?:forwarded|begin forwarded|fwd:|original message|from:|subject:|رسال[ةه] م|تمت إعادة|"
    r"تمت اعادة|محول)",
    re.MULTILINE | re.IGNORECASE,
)
_WROTE_END_RE = re.compile(r"(?:wrote|writes)[ \t]{0,3}+:[ \t]{0,8}+$|كتبت?[^\n:]{0,80}+:[ \t]{0,8}+$",
                           re.MULTILINE | re.IGNORECASE)


def last_words(text: str | None) -> str:
    """The user's own words at the end of a message longer than the part
    :func:`own_words` reads: its last paragraph (after the last blank line),
    or its last ``_TAIL_MAX`` characters from a sentence's start when that
    paragraph is longer or there is no blank line -- read as a message of
    its own. Nothing for a short message, or when something before the end
    may make it someone else's: a code fence left open, a forwarded message,
    a quoted mail, an instruction at the start that no aside ends.

    Never the user's trust: the quick pass reads :func:`own_words` only. The
    model's reading of a value found here is put to the user -- a question
    over a fact they stated, Kazma's reading where they gave none
    (``clause_subject.names_no_one_else``; rule D of the second security
    review of F1, 2026-11-18). The checks before the end are searches in C,
    so a long message costs its length once."""
    body = _message_body(text)
    if len(body) <= _SCAN_MAX:
        return ""
    para = body.rfind("\n\n")
    start = para + 2 if para >= 0 and len(body) - (para + 2) <= _SCAN_MAX else _sentence_start(
        body, len(body) - _TAIL_MAX)
    if start is None:
        return ""
    before = body[:start]
    if (len(_FENCE_LINE_RE.findall(before)) % 2 or _FORWARD_LINE_RE.search(before)
            or _WROTE_END_RE.search(before)):
        return ""
    if _read(body[:_SCAN_MAX])[1]:
        return ""  # "Translate the following:" at the start works on all of it
    return _read(body[start:])[0]


#: A text named by where it was taken from ("this note from the web page",
#: "من الموقع"): pasted, whatever its shape.
_SOURCE_NOUNS_EN = frozenset({"page", "webpage", "website", "site", "web", "article", "email", "mail", "message",
                              "post", "tweet", "chat", "document", "doc", "pdf", "book", "link", "internet",
                              "google", "chatgpt", "news", "paper", "report", "screenshot", "forum", "reddit",
                              "wikipedia", "thread", "comment", "group"})
_SOURCE_NOUNS_AR = frozenset({"الموقع", "الصفحه", "المقال", "الرساله", "الايميل", "المحادثه", "الكتاب", "النت",
                              "الانترنت", "جوجل", "القروب", "الجروب", "التغريده", "المنشور", "الخبر", "التقرير"})


def is_plain(text: str | None) -> bool:
    """Whether *text* is the user's own plain words, all of it: nothing in it
    is a paste, someone else's words or claim, the text an instruction works
    on, or a question (:func:`own_words` keeps every word), it names no
    source it was taken from ("this note from the web page"), and it is no
    longer than the part :func:`own_words` reads. A request to remember
    earns the user's highest trust only for such a message
    (``provenance.agent_write_source``; the third review of F1, item 2)."""
    body = _message_body(text)
    if not body.strip() or len(body) > _SCAN_MAX:
        return False
    kept = own_words(text)
    if [_fold(w) for w in _WORD_RE.findall(body)] != [_fold(w) for w in _WORD_RE.findall(kept)]:
        return False
    return not _names_a_source(kept) and not _reports(kept)


def _names_a_source(text: str) -> bool:
    """Whether *text* says where its words were taken from: "from" (or
    "من") and a source within the next four words."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    folded = [_fold(w) for w in _WORD_RE.findall(text)]
    for k, word in enumerate(words):
        if word == "from" and any(w in _SOURCE_NOUNS_EN for w in words[k + 1:k + 5]):
            return True
    for k, word in enumerate(folded):
        if word == "من" and any(w in _SOURCE_NOUNS_AR for w in folded[k + 1:k + 5]):
            return True
    return False


def _sentence_start(body: str, at: int) -> int | None:
    """The first place at or after *at* where a sentence starts: after a line
    break, or after a sentence's closing mark and a space."""
    for i in range(max(at, 2), len(body)):
        if body[i - 1] == "\n" or (body[i - 1] in " \t" and body[i - 2] in _SENTENCE_ENDS):
            return i
    return None


def _message_body(text: str | None) -> str:
    """*text* outside its data fences, up to the web chat's file stub."""
    from kazma_core.safety.prompt_fence import outside_fences

    body = outside_fences(str(text or ""))
    stub = body.find(_WEB_FILE_STUB)
    return body[:stub] if stub >= 0 else body


def _read(body: str) -> tuple[str, bool]:
    """The user's own words of *body* (already bounded), and whether an
    instruction's text runs to its end."""
    body = _strip_code_fences(body)
    body = _STUB_RE.sub(" ", body)
    body = _without_forwarded(body)
    body = _without_pasted_chats(body)
    body = _without_speaker_lines(body)
    body = _without_quote_lines(body)
    body = _without_quotes(body)
    cut, runs_to_the_end = _cut_open(body)
    return _without_reported_or_asked(cut).strip(), runs_to_the_end


def _strip_code_fences(body: str) -> str:
    """Lines between a ``` (or ~~~) fence and its closing fence, or the end."""
    out: list[str] = []
    fence = ""
    for line in body.split("\n"):
        mark = line.lstrip(" \t")[:3]
        if fence:
            if line.strip().startswith(fence) and set(line.strip()) <= {fence[0]}:
                fence = ""
            continue
        if mark in ("```", "~~~"):
            fence = mark
            continue
        out.append(line)
    return "\n".join(out)
