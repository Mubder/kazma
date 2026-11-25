<!-- AGENTS.md, piece 15h-facts. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**H. Facts follow the order they were said in (W1, 2026-09-27).**
- **`mutate_belief(now=...)` is when the fact was STATED** (valid time:
  `valid_from` / `valid_until`); `ingested_at` / `invalidated_at` are when
  the write happened. A single-valued (functional or state) statement older
  than the current fact is recorded as HISTORY in its place on the timeline
  (`belief_mutation._record_earlier_statement`): it holds until the next
  statement, cuts the previous one short, and never becomes current. It adds
  nothing when a neighbour says the same, and the trust gate applies to the
  value it would cut short. History rows reach the state mirror, never the
  graph. Until 2026-09-27 the last WRITE won, so any
  late writer could put an old "I live in Paris" over "I moved to London".
- **Every writer that runs after its turn passes the turn's time.** The
  post-turn scheduler stamps `at` when the turn closes; the durable queue
  carries it (`post_turn_memory` payload `at`); the episode, the heuristic
  facts and the LLM deep pass (`micro_consolidation`, from the episode's
  `created_at`) all use it; turn reconcile uses the turn's. (The deep pass
  no longer corrects its own turn's quick pass: a model's reading never
  gets the user's trust by itself, below.) Gate:
  `tests/test_memory_event_time.py::test_every_late_writer_passes_the_turn_time`
  (every call in the late-writer modules names its time).
- **The deep pass is told the turn's day and the turn before it**
  (2026-11-20, framework audit F2 step 2, part C2). The memory worker
  (`worker_bootstrap._turn_context`, off the loop) gives
  `belief_extractor.turn_message` the turn's day in the operator's zone
  (`said_on_iso`, `_turn_zone`) and the chat's remembered turn before it
  (same tenant and session, never a forgotten one), bounded by
  `limits.DEEP_PASS_PREVIOUS_QUESTION` / `DEEP_PASS_PREVIOUS_ANSWER`; the
  prompt says to learn only from "This turn" and to give a fact's start as
  `since` worked out from the day. `_since_value` keeps only a real date
  (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`, any digits, from 1900; never words),
  `_started_on` drops one after the turn, and `mutate_belief(started=)`
  keeps it in the row's metadata (`started`) -- never as `valid_from`, so
  validity and supersession still follow the order facts were said (W1).
  The same fact again gains a start it lacks, never over one it holds, and
  keeps its source. Recall shows it ("[since Apr 2026]"; else the month it
  was said). Message form 2 (`MESSAGE_SHAPE`); declared in the lock until
  the recording. Gates: `tests/test_memory_extractor_context.py` (the
  worker without the context and a writer that drops the start as negative
  controls), the write scenarios' `temporal` category.
- **The deep pass is shown the facts held and may say which one a new fact
  replaces** (2026-11-20, framework audit F4's second layer, part C2).
  `belief_extractor.held_facts` reads the current facts about the turn's
  people and things -- the speaker's own first (`user`; their entry in a
  shared space, shown as `user`), then each subject whose words the turn
  holds (`query_terms.content_terms`), never another member's entry,
  another tenant's or a forgotten fact -- at most
  `limits.DEEP_PASS_HELD_FACTS`, values cut at `DEEP_PASS_HELD_VALUE`,
  numbered `f1`... in the message (form 3). A fact whose `replaces` names
  one (`_REF_RE`) goes by that fact's name and kind (`_take_replaces`) only
  for the same subject (after filing) and only while it is still current;
  the trust gate then decides as for any change. A replaced list entry is
  closed when `provenance.may_overrule` lets the source change it, else
  counted `replace_refused` (`_close_replaced_entry`; on a private database
  no mirror or graph call). The worker passes the same list to the message
  and the write. Gates: `tests/test_memory_held_facts.py` ("replaces"
  ignored as the negative control), the write scenarios' `replaces`
  category.
- **The deep pass reads the turn inside a data fence** (2026-11-20, the
  framework audit's F10, part C2). `turn_message` (form 4) puts the turn
  before, the facts held and the turn itself inside one
  `prompt_fence.format_untrusted_block` (source `memory_turn`): a fence tag,
  sentinel or foreign role marker planted in any of them is neutralised,
  and Kazma's own lines (the day, the names in use) stand outside it. The
  prompt says the fence is data and the reply's claims are not the user's.
  `_sanitize_belief` refuses a fact whose text carries an override phrase,
  an injection payload (`filter_injection`) or a fence marker
  (`prompt_fence.carries_fence_marker`), as written or in disguise
  (`_carries_injection`); a fact the model read stays the model's, tagged
  "(Kazma's reading)" when recalled. Gate: `tests/test_memory_deep_pass_fenced.py`
  (the fence taken out, and the screen taken out, as negative controls).
- **A value is kept in the user's own spelling** (2026-11-20, the framework
  audit's F1, its third fault; part C2). The prompt asked for slugs, and
  recall said "user name is layla" for the user's "Layla". The prompt now
  asks for an object as the user wrote it, and `_apply_beliefs_to_v2`
  keeps it whatever the model wrote (`belief_extractor._users_spelling`:
  the run of the message's words that is the same value, folded as
  `belief_mutation._same_value` folds; up to `_SPELLING_WORDS` words; a
  value joined otherwise, "Kuwait-City", is left as the model wrote it).
  Trust is not touched: the fact stays the model's reading. Subjects stay
  slugs. Gate: `tests/test_memory_users_spelling.py` (the apply step
  without the respelling as the negative control).
- **The deep pass keeps a turn's events with their day** (2026-11-23, the
  memory plan's W4, dated event lines). The prompt asks for something the
  user did or that happened to them -- went somewhere, attended, bought (with
  its price), received, met, started, finished -- as a fact of its own: a
  past-tense predicate, the object in the user's words with its name, place
  and amount, and `"on"`, the day it happened worked out from the turn's day
  (never guessed); up to 8 facts a turn (it was 5). `_sanitize_belief` keeps
  `on` by `_since_value`'s rule, `_started_on` drops a day after the turn,
  and `mutate_belief(started=, happened=True)` keeps it in the row's
  metadata (`started`, `happened`) -- never as `valid_from` (W1). The same
  fact or list entry told again gains the day it lacks
  (`_add_named_start`, now from `_mutate_set` too). Recall shows it as
  "[on 30 May 2026]" (`_date_hits` `on`, `_dated_line`), never "[since
  ...]". Measured by `scripts/longmemeval_facts_pilot.py` (LongMemEval
  histories written round by round through the product's own deep-pass
  task, one store a question; `--label` keeps another extractor's run
  apart, `--prices` with `--stop-usd` ends one at its spend), then recorded
  (2026-11-24). Gate:
  `tests/test_memory_event_lines.py` (EN/AR; the writer without the mark,
  which shows the day as a start, as the negative control).
- **What an event is, whose day it carries, and when it happened** (the
  review of W4, 2026-11-24; `memory/fact_days.py` is the one home of a
  fact's day). An event is a list entry under its own name whatever the
  reply labelled it (`belief_extractor._read_days`): never
  `fact_names.users_names`, a first reading or a question ("I called my
  mom" became the user's name, "moved_to Porto" their home); on a fact a
  person holds one value of (`fact_names.names_one_value`) `on` is its
  start. The same words on another day are another occasion
  (`belief_mutation._entry_told_again`, an event's words compared by
  `_same_value`; the nightly merges read the day too). `replaces` on the
  SAME day is a correction whatever the words ("not Paris, Lyon"); with
  another day it is the same event only when the words name one thing
  (`fact_days.same_thing`: two shared words, or half of the longer's;
  "bananas" was closed by "fresh vegetables", a phone by a phone case) and
  the day told can have happened by the first telling
  (`fact_days.could_have_happened_by`); with a day on one side only, a
  switch of something that is no event, the same thing, or what the user
  says it was not (`event_text.denies`) (`_same_event`). Then it corrects
  the day, and one with no day or a vaguer one keeps the day it replaces
  (`_keep_the_day`). A day names
  who gave it when that is not the row's own turn (`day_source`: session,
  turn, method; the day it took the place of stays under `day_before`), so
  forgetting that turn or chat takes the day back
  (`forget._forget_given_days`, the ledger pass too) and a day the model
  read on a fact the user stated is tagged as Kazma's reading
  (`fact_days.days_method`). An event is true on its day only
  (`recall._held_in`, `memory_list`, `/memory/you`); a day after the turn is
  `planned` ("[planned for 3 Jun 2026]", "[was planned for ...]"), never a
  start thrown away; a month or a year reads "in". An event of the user
  is theirs to tell (`event_text`, below). At most `_MAX_BELIEFS` facts a
  reply. A new reader of a fact's day asks `fact_days`. Gate:
  `tests/test_memory_event_lines_review.py` (EN/AR; each point fails on the
  commit reviewed, with its control).
- **Whose words an event and its day are, what its name is, and one line
  for it** (the second review of W4, 2026-11-24).
  - *Its words* (`memory/event_text.stands_elsewhere`, asked for every event
    of the user's, dated or not; the rule of the third review, 2026-11-25): an
    event is left out only when its words stand in text that is clearly pasted
    or another person's and not in the rest (`memory/pasted_text.py`: a data
    fence, a code fence, `>` lines, a forward or a mail with its header, a
    chat, words after "X said:", a sentence quoted, an instruction's own text
    to its line's end or the blank line under it). **In doubt it is the
    user's** (the re-check of f68b21ae5: what it read as a chat or a mail took
    48 to 57 of 249 events of openings nobody had shown it, Arabic more): a
    paste let in is filed as Kazma's reading, a line of theirs dropped is
    lost, so a signal is added only with its count on
    `tests/fixtures/memory_events/own_words_openings.json` in English, Gulf
    and MSA. Everything else stays as Kazma's reading: reported,
    supposed, long, behind one label, or in the model's words. **Never
    `own_words`**: it is for trust, takes out what it is unsure of, and as the
    gate dropped 12 of 135 ordinary English openings and 60 Arabic ones ("لو
    سمحت، بعت سيارتي" read as "if", "القصة:" as who spoke: Arabic has no
    capitals to tell a heading from a name). A labelled line is a speaker's
    when the label is a time's, a phone number, a handle, or the message shows
    a conversation: "Me:" after another's line, A, B, A, a first word that
    answers ("congrats", "مبروك"), a question to Kazma about that person.
    Never a role or a name alone ("Doctor:", "بنتي:", "جدة:" is a city), one
    named again, a "no" or a "?". A mail's header is "From:" with an address
    or "Subject:" ("From: Kuwait / To: Dubai" is a journey); a letter ends at
    the paragraph that asks Kazma about it; a line after a chat export is the
    user's; a head with "my", "our", "I" ("My caption:", "لخص اللي سويته:")
    hands over the user's own words, unless it is their person's ("my boss").
    What the user points at is theirs ("Just bought these!"), dated or not.
    Words are compared with what Arabic writes onto
    them, on the message's side and the value's ("لدبي" holds "دبي",
    "سيارتي" is "السيارة", "بـ٩٥" holds "٩٥").
  - *Its day* (`event_text.days_elsewhere`, `time_words.says_day`: the day
    itself written out, by its weekday or counted to, in either language; it
    replaced "names a day at all"): a day a paste names and the rest of the
    message does not is dropped. A day the user counted from the turn's own
    ("today", "yesterday": `day_sure`) stands against a later turn's unless
    that turn denies it; said without a denial, the other day is another
    occasion. `event_text.corrects` ("sorry, I meant", "آسف أقصد") with
    `replaces` is enough to put an event right; `denies` ("no", "not", "لا",
    "مو") needs the old value named, is no "no" before a noun and nothing
    beside "also" or "again" -- unless it denies the old value by name
    (`puts_right`: "Not Paris, it was Lyon again") or begins the message;
    "I also bought a lamp", "شريت لمبة برضه" close nothing.
  - *Its name* (`event_text.is_event_name`, a verb list): a functional or
    state label with a day holds unless the name is an event's verb ("I
    weigh 78 kg today", "delivered yesterday"); such a name keeps a day to
    come only as a plan the user told ("I'm going to the concert tomorrow",
    read as attended: `time_words.says_to_come`), and no day past 2199 is
    read (`_since_value`: a model's "9999" lost its reply's every fact);
    "called"/"named" in a list is an event without a day too, unless
    the message says what the user is called. A plan that came to pass is
    filed as what happened, under its own name (`_came_to_pass`). `says_to_come` reads the
    future and the past of "I" in each language ("سأحضر", "بعد غد", "كنت
    رايح" is no plan).
  - *The held list shows an event's day* ("(on 2026-05-30)", message form
    5), with a prompt rule on its use.
  - *One line for an event* (`recall._one_per_event`, `_by_event`,
    `fact_days.event_key`): its occasions are one candidate in every lane,
    the latest shown with "and N more", every one for a list or a period
    question -- after everything found has been shown once (`recall._in_order`:
    six visits to the dentist hid the eye doctor of a question naming both),
    the ones the block has no room for counted; a list's siblings are those
    holding a word of the question, then the newest, never in the order of
    their ids. Five visits hid "my dentist is Dr Sara" from every search and
    were the background the question was measured against. The
    Postgres-primary path takes the same steps (`fetch_beliefs` reads the
    metadata).
  - *Forgetting a retelling opens the entry it closed* (`closed_by`,
    `forget._reopen_replaced`), unless what took its place was replaced by a
    turn that stands. One either archive moved out (the nightly sweep's, the
    Memory page's) comes back as the kind it was, with no vector, naming no
    predecessor that is not in `beliefs` (a foreign key made the forget
    raise), and leaves the archive only when `beliefs` took it
    (`_from_archive`); the ledger pass reads the archive too. A write compares `_FOLDED_ENTRIES` of a name's newest
    entries folded, and all of them in their own spelling.
  - *Own words after a question to Kazma* ("What do you think about my rugs?
    I washed them"): a thinking verb whose subject is "you" reports nobody
    (`own_words._asks_kazma_en`, `_asks_kazma_ar`), and a question asks
    about the text after it only when it names or points at one
    (`_asks_about_a_text_en`, `_asks_about_a_text_ar`).
  Gate: `tests/test_memory_event_lines_second_review.py` (78 of its cases
  fail on 174057e52; EN/AR pairs, controls),
  `tests/test_memory_event_lines_third_review.py` (119 of its 233 fail on
  0bbecc655; the twinned openings and pasted texts in
  `tests/fixtures/memory_events/openings.json`: main keeps every opening's
  event in each language and so must this),
  `tests/test_memory_event_lines_recheck.py` (117 of its 182 fail on
  f68b21ae5), the pairs in
  `tests/test_paired_languages.py`.
- The quick (heuristic) pass reads Arabic and mixed text too
  (`memory/arabic_facts.py`, 2026-10-14: the extraction benchmark measured
  0.00 Arabic recall). Token rules from the grammar, compared folded: a
  negation, an imperative without its hamza ("استخدم البحث"), a question, a
  participle outside the user's own clause ("أخوي ساكن في دبي") and "work in
  a field" keep nothing; a place must end where it is read. One fact per
  kind, the English reading first. Gate: `tests/test_memory_arabic_facts.py`
  (sentences not in the benchmark, both ways; the pass switched off as the
  negative control).
- **A time word after a place ends it, in both languages** (2026-11-22,
  the memory plan's finding 10): "I live in Lisbon these days." and
  "أسكن في لشبونة هالأيام." kept nothing in any real-use run (only "now"
  and "currently" ended an English place; "هالأيام" was no Arabic end).
  One list, `memory/time_words.py`: `TIME_AFTER_EN` (the present, a period
  named from today, "N units ago") ends the English pass's place and
  employer (`belief_extractor._value_end_en`), `present_ar_words()` the
  Arabic pass's (`arabic_facts._ends`). "I moved to Porto." / "We moved to
  ..." is read as "انتقلت إلى بورتو" is (`_MOVED_EN`; "I live in" in the same
  message wins). Gate: `tests/test_memory_time_ends_a_place.py` (each
  statement stored by the real quick pass, EN/AR, with and without a time
  word; not-a-place and someone else's move keep nothing; today's rules as
  the negative control).
- **A move to a name no word calls a place asks; it never replaces the
  home** (2026-11-22, the memory plan's finding 15, equal in both
  languages): "We moved to Slack" is a tool. A bare "moved to X" /
  "انتقلت/انتقلنا إلى X" is Kazma's reading
  (`belief_extractor._kazmas_reading`, the belief's `reading` flag): a
  question over a home the user stated (`changes.record_pending`, "Yes,
  change it" updates with Undo), Kazma's reading when none is held. It
  updates at once only when the statement says it is a home or a place:
  `_MOVED_HOME_EN` ("moved home / house / our flat to"), `_MOVED_PLACE_EN`
  ("to the city of"), `arabic_facts._SETTLE_TRIGGERS` ("سكنت في"),
  `_MOVE_HOME_TRIGGERS` + `_HOME_NOUNS` ("نقلنا بيتنا إلى"), a place head
  (`_PLACE_HEADS`, "مدينة"). "I live in" / "أسكن في" stays an update. Gate:
  `tests/test_memory_moved_to_asks.py` (through the quick pass into a
  private database, the change log read back; every move the user's own
  statement as the negative control), the write scenarios (v9).
- **A fact about someone else is never the user's** (2026-11, the memory
  framework audit's speaker rule, `memory/clause_subject.py`). `subjects` says, for
  each word, who the words before it last named: the speaker ("أنا", "I",
  "we", "اسمي"), someone else (a relative or a companion in any form, a
  third-person pronoun where its clause begins, "اسمها", a reporting verb),
  or no one; a wide list for someone else, a narrow one for the speaker. The
  Arabic quick pass's triggers are each declared `_MARKED` (the form says the
  user is the subject: "أسكن", "انتقلنا", "اسمي") or `_UNMARKED` (a
  participle, or "انتقلت", which is I, you and she alike): an unmarked one
  counts in the user's own clause (or after a time phrase: "قبل سنتين
  انتقلت") and only with no one else named before it.
  "أختي انتقلت إلى روتردام" was the user's home at the user's own trust, and
  so was "أخوي اسمه أحمد وساكن في دبي" (a clause joined by "و"). Names no
  one: a person noun right after the speaker ("أنا مدير", "عندي بنت", "I'm a
  teacher": a description or a possession), a definite word ("الأهلي",
  "الجاري" are never "my ..."), "جاري" (work in progress as often as a
  neighbour), "her" with nothing after it ("I told her."). A sentence end
  clears who was named, except someone introduced by name ("عندي أخت اسمها
  نورة. انتقلت" is still Noura); the cost, decided, is "صديقتي معلمة. ساكنة
  في جدة" read as the user's. Gate:
  `tests/test_memory_clause_subject.py` (every trigger declared once, a planted
  one as control; every unmarked trigger after a sentence about someone
  else; the old trigger list, the rule switched off, and 40db48ba6's
  time-phrase, sentence and role rules as negative controls), and the write
  scenario `other-ar-sister-moved`.
- **Only the user's own words are the user's statement** (2026-11, the
  audit's F3, `memory/own_words.py`). `own_words` is the message without
  data fences (an attached document's text: `build_user_content` puts it in
  the user's message), "[Attached: ...]" lines, fenced code, "> " lines, a
  forwarded message (a pasted header, "Forwarded from ...", "رسالة محولة";
  Telegram and WhatsApp forwards are fenced by the gateway,
  `kazma_gateway/forwarded.py`, a forwarded voice note's transcript too:
  `WaInbound.forwarded`), a quoted sentence in the first person
  (straight, curly, single, backtick quotes or «», never an inch mark), someone
  else's words after a colon ("my sister said:", "قالت أختي:", "قلت لي:",
  to the end of that sentence: "Kazma said: your name is Ahmed. No, my name
  is Ahmad." keeps the correction), and the text an instruction works on
  ("Translate to French:", "ترجم:", "Fix the grammar:", "Can you translate
  this?", "سوي لي ترجمة:", "Write an email ... saying:", to the end of the
  message or an aside after it: "By the way, ...", "بالمناسبة"). Inside a
  request to remember, a quote is the user's statement
  (`remember_request.asks_to_remember`). A verb that also means something else ("fix",
  "correct", "صحح") is an instruction only when it names the text ("Fix the
  grammar:"), so "Correct: my name is Ahmad" stays the user's own. Heads
  are read in an 80-character window at the start of the message, a line or
  a sentence, by either language's rules: linear time. **What the security
  review of F1's second half found (2026-11-18)**, each in both languages:
  the lines of a pasted chat (a name before a colon at a line's start,
  `_without_speaker_lines`: an English name written with capitals, never one
  of the user's own labels or a form field, `_OWN_LABELS`, nor an Arabic
  verb Kazma knows); a pasted mail's "From:" or "Subject:" line; a text
  handed over or asked about ("Here is the article:", "Tell me if this is
  spam:", "What does this mean:", "Message from Dana:", "Her bio reads:",
  "كتبت:"); a reporting head of up to 14 words; someone's words after a
  reporting verb with no colon, when a first-person word follows ("He said,
  I live in Lyon.", "Ali told me I work at Initech", "أختي تقول أنا ساكنة"),
  unless the user is its subject ("I told Ali I ..."), and a sentence that
  ends naming its speaker ("..., she added."; `_reported_cut`); a question
  ("How do I say my name is Pierre in French?", `_asks`, unless it asks to
  remember); a quote over a line break; a request to remember that quotes
  someone ("remember that Dana wrote "..."") is not the user's statement.
  Kept the user's: an aside on a line of its own ("Also my name is Sam"),
  what follows a web address an instruction works on ("Summarize
  https://x.y and my name is Sam"), facts given for the user's own bio, the
  '90s, a "> " line under a request to remember. At most the first 2,000
  characters are read, ended at their last paragraph break (`_bounded`:
  never the end, which a head, fence or forward line in the unread middle
  would decide). **Rule B of the review's second round (2026-11-18)**
  widened the cheap paste shapes (the change log is the net for the rest):
  a WhatsApp export's lines and those continuing them, any label
  (`_EXPORT_LINE_RE`: a digit in the label, the Arabic form with its
  direction marks), a bracketed time before any label ("[12:01, ...]
  dana_92:"), a lower-case name or a handle ("dana:", "dana_92:") or a role
  ("the customer:", "my sister:") at a line's start (`_names_a_speaker`;
  never a greeting, "Good Morning:", nor a heading, "Random thought:",
  "Step 2:", nor a lower-case phrase); the header a Telegram, Discord or
  Slack copy puts over a message, or a time alone on its line, and a mail's
  "... wrote:" line, with the text under it to the next blank line
  (`_without_pasted_chats`), as a reporting head that ends its line
  (`_reported_end`); a text of someone else's named ("Dana's bio:", "Her
  message was:", "رسالتها:"); reported claims with no listed verb ("Ali
  thinks I ...", "Dana claims I ...", "He was like, I ...", "As Dana put
  it, ...", "According to Ali, ...", "حسب كلام أختي"، "أختي كاتبة إني ...":
  `_THINKING_EN`, `_THINKING_AR`, `_reports_at`, `_attributed`); an example
  or a part to play ("For example:", "مثلا:", "Pretend you are me and say:",
  "تخيل"). **Rule C**: a text the user asks to remember and names as
  pasted ("Remember this email:", "احفظ هذا الإيميل:") is not their
  statement, so `agent_write_source` gives it `agent_asserted`, and a
  negated "remember" ("Don't remember this", "I can't remember my
  password", "لا تذكر") is no request (`remember_request`). **Rule D**:
  `own_words.last_words` reads the last paragraph of a longer message (or
  its last 400 characters from a sentence's start) as its own message,
  never after an open code fence, a forwarded message, a quoted mail or an
  instruction at the start; only `clause_subject.names_no_one_else` reads
  it, so the model's reading of a correction there is a question to the
  user (or Kazma's reading), never the user's trust. An episode keeps the
  whole message (§15F, since 2026-11-19); the deep pass reads its first
  1,500 characters (`limits.DEEP_PASS_QUESTION`): the end of a message
  longer than that reaches neither pass but `last_words` (the main
  session's decision: it stays). **The third
  review's items 2 and 3 (2026-11-18)**: a request to remember earns
  `user_explicit` only when the whole message is the user's plain words
  (`own_words.is_plain`: own_words drops nothing, no source named, "this
  note from the web page", no reporting verb), else `user_stated` at most;
  "Don't forget to <verb>" is the instruction it names. A copy header or a
  mail's "wrote:" line (also French "a écrit :" and German "schrieb ...:")
  and someone's words under a head that ends its line run to the END of the
  message (copies have paragraphs); more headers ("Name, [date at time]",
  "Name [12:01 PM]", "Name 12:01 PM", "Name — time", "Name - Today at",
  "Name, 12:01 PM", `_HEADER_RE` with a name that names a speaker, never
  an event like "Dinner"), "12:01 Name:", "To:", "Date:", "From Name:";
  labels: a phone number, "A:" (`_SIDES`), "User 1", "Person A", titles,
  parentheticals, "Dana from HR", "My best friend", "1. Dana:", `<Dana>`,
  "RT @dana:"; reported speech: bare verbs ("My parents think", "People
  assume", `_MORE_THINKING_EN`), "told everyone", "says here"
  (`_MORE_FILLERS`), "Per Ali", "In Dana's words", "Her reply was",
  "مكتوب", "ردها كان", "Dana said" ending its line; not statements: a
  conditional, "I never said", "Is it correct to say"
  (`_supposes_or_asks_to_say`). The best effort the main session asked for:
  the threat is text the user pasted, and the change log is the net. The
  quick pass (`extract_beliefs_heuristic`),
  `is_remember_request` and `provenance.agent_write_source` read through
  it; the turn keeps the whole message and the deep pass reads it (its
  first 1,500 characters) at
  an inference's trust. "Translate to French: My name is Pierre" stored the
  user's name as Pierre at `user_stated`, and a document beside a "remember
  this" could earn `user_explicit`. The quick pass no longer reads "I need
  X" as a preference. Gate: `tests/test_memory_own_words.py` (one sentence
  per quick-pass pattern, EN/AR, in twelve forms and a real
  `build_user_content` attachment; the user's own corrections kept; the
  quoted write scenarios pass and fail with the raw message; the raw message
  as the negative control for the quick pass and the trust gate; the
  review's forms, kept cases and remember quotes, with the first rules as
  the control; the security review's cases with the rules before it as the
  control, the cap, and every scanner in bounded time with the uncapped
  read as the control; rule B's pastes and kept cases with the rules before
  it as the control, rule C with the old remember pattern and the rule taken
  out as controls, rule D's ends), `tests/test_memory_own_statements.py`
  (rule D through the real deep pass, without `last_words` as the control),
  `tests/test_forwarded_messages.py` (the real Telegram
  adapter and WhatsApp parser, the adapters that ignored forwards as the
  control).
  **A file's text reaches the model in a data fence on every surface**: the
  web chat sends a text file beside the message (`text_attachment`), and the
  server fences it and keeps only its name in the chat
  (`agent/attached_text.py`). The page put it into the message in a code
  block that a line of three backticks inside the file ended early, and the
  rest was the user's words. A stub's name is cleaned (`file_label`: a name
  holding "]" ended the stub), own_words reads a stub to its line's end and
  nothing after the web chat's. Gate: `tests/test_attached_text_fenced.py`
  (the real route EN/AR, the old page message as the negative control, no
  page script builds a stub, every product function writing one declared
  with how the file's text is fenced), `tests/e2e/test_attached_text_browser.py`.
- **A model's reading never gets the user's trust by itself** (2026-11,
  the audit's F1; rule A of its second security review, 2026-11-18). The
  first half credited a deep-pass reading the user said of themself
  (`user_stated`); the reviews found the tail endless -- "I'm visiting Dubai
  next week" as lives_in, "I met Dana" as name_is, the same turn's reading
  replacing the quick pass's fact ("My name is Sam. I met Dana yesterday."),
  another name for the same fact standing beside the user's, a wrong first
  reading taking the user's trust and blocking the real one. Whether the
  model's name fits the words only the user can say. So
  `_apply_beliefs_to_v2` writes every deep-pass fact as the model's
  (`llm_inferred`), as before F1, and the trust gate
  (`provenance.may_overrule`) lets a fact the user stated yield only to the
  user's own words -- in its own turn too: the exception that let the model
  pass correct the quick pass of its turn is gone. A reading of one of the
  user's facts in their own words with no one else named is put to them
  (below): a question only where it disagrees with a fact the USER gave;
  as the first of its kind it is Kazma's reading, never a question -- not
  sticky, since any later statement of the user's replaces it. The user's
  facts go by Kazma's names before the write
  (`memory/fact_names.py`, `_canonical_for_user`: "home_city", "located_in",
  "resides_in" -> "lives_in"; employer, role, name, preferred name; subject
  `user` only; a plural is a list -- the first layer of F4's table, which
  grows to forty names after the owner's review). **One helper, every
  writer** (the main session's check of the third review, fixes 1 and 4):
  `fact_names.users_names` ("User" is the user, and the user's facts go by
  Kazma's names) is called before `mutate_belief` by the deep pass, the
  memory tools, the Memory page's link, a confirmed change and the V1
  backfill (its own inserts too), so a memory tool's `home_city` or
  `current_location` meets the user's home as one fact; a name is read in
  snake case whatever its separators or case first (`normalised_name`: "home
  town", "lives.in", "livesIn", "currentLocation"). Gate:
  `tests/test_user_fact_names_every_writer.py` (every function that calls
  `mutate_belief` or inserts into `beliefs` calls the map or is declared
  with why -- the hub's anchor, the two eval fixtures, `_insert_belief`; a
  planted writer as the control). The retrieval benchmark's fixture seeds
  the dataset's names as they are: seeded through the map, its "job_title"
  and "name" facts become `current_role` / `name_is` and recall loses
  "what is it that I do for a living?" (noise hit rate 1.0 -> 0.67,
  measured with bge-m3's own vectors), a cost the deep pass's naming
  already has live; the owner decides the dataset. The user's own word for a
  value the model read raises it to the user's trust
  (`belief_mutation`'s same-value branch). Every product call that applies
  extracted facts names `user_text` (the two forwarders aside). "The same
  value" is compared folded (`belief_mutation._same_value`: case,
  underscores, spacing, Arabic forms and marks): the model's respelling
  ("lisbon", "gulf_bank") is no change. Gates:
  `tests/test_memory_own_statements.py` (the worker's own
  micro_consolidation: corrections, first readings, a turn of five first
  readings asking nothing, "Not me", aliases, the same turn, the review's
  readings, a summarised page, the sister and the manager; every reading at
  the user's trust, no name table, asking about every first reading and no
  "Not me" as negative controls; every applier call names its message),
  `tests/test_memory_provenance.py` (the same-turn exception as the
  control), and the write scenarios (v4). **Recall says which facts the user
  did not state**: in the recall block only a fact whose `extraction_method`
  is in `provenance.USER_SOURCES` goes untagged. Any other recorded source
  starts its line with "(Kazma's reading)", or "(قراءة Kazma)" for an
  Arabic value (`recall.READING_TAG_EN`, `READING_TAG_AR`); no recorded
  source (NULL, empty, another install's fact) with "(source unknown)" /
  "(مصدر غير معروف)" (`UNKNOWN_SOURCE_TAG_EN`, `UNKNOWN_SOURCE_TAG_AR`). The tag comes BEFORE
  the value and every entry is one line (`recall._one_line`): after it, a
  value holding a line break ended the line untagged (fixes 2 and 3). The
  Postgres-primary path reads the source from this install's copy
  (`recall._add_local_methods`). Recall's ranking is unchanged (the
  benchmark's 103 scores equal). Gate: `tests/test_memory_recall_reading_tag.py`
  (the tag after the value, the rule that left an unknown source untagged,
  and the hydrate without the source as negative controls).
- **A fact the user stated changes only in ways the user can see and take
  back** (2026-11, the audit's F1, second half; `memory/changes.py`, table
  `memory_changes` in `memory_state.db`). An **update**: a `user_*` fact
  replaced from another turn (`belief_mutation._mutate_functional_locked`,
  `users_fact_replaced`) is recorded by `changes.record_update` in the
  replacement's own transaction; `changes.undo` reopens the old fact and
  closes the new one under the mutation lock, audited (`undo_update`), and
  refuses with a code (`ChangeRefusedError`: `replaced_since`, `forgotten`).
  The model read one of the user's facts -- the one way a reading only the
  model made can become theirs -- with the value in the turn's own words,
  in a sentence that states it, with no one but the speaker named before it
  (`clause_subject.names_no_one_else`, the user's spelling: "We moved to
  Rotterdam", "Moved to Rotterdam last month", "No, Lisbon"; "My sister
  moved to Rotterdam" records nothing), from `_apply_beliefs_to_v2`
  (`_put_to_the_user`). A **pending change** -- a question -- only where the
  trust gate refused it over a fact the user stated (`changes.record_pending`;
  never over the user's words of the same turn). A **reading** (kind
  `reading`) -- never a question -- for the model's first reading of one of
  `fact_names.KINDS`, written as its inference (`changes.record_first_reading`):
  the deep pass files several facts on most turns, and a question the user
  ignores teaches nothing. The user confirms either (`changes.confirm`:
  written `user_explicit`, the change marked confirmed in that write's
  transaction, `mutate_belief(confirms_change=)`; a reading the model
  replaced since is refused, `replaced_since`) or dismisses it; "Not me" on
  a reading takes the model's fact out while it is current and still the
  model's (audited, `dismiss_reading`), and the deep pass never files that
  value again (`changes.user_said_not_me`; the newest `KEEP_NOT_ME`, 500 a
  tenant, are kept) -- the user's own words still can. Any fact the user
  states answers what is waiting on it (`changes.answer_pending`). One open
  change per value; an update supersedes them; one open 30 days
  expires; the newest 200 decided ones are kept per tenant. An update that
  closed the user's other list entries with its fact records them
  (`also_closed`), and undo reopens every one (the third review, item 4),
  except one whose value the user has said again since
  (`changes._held_again`, `_same_value`: it is current already; fix 5).
  **A memory tool follows rule A on replacement** (item 1): its subject and
  fact name are the model's even when the value is the user's, so its write
  passes `mutate_belief(replaces_users_fact=False)`; one that would replace
  a fact the user stated is put to the user as a pending change
  (`tool_builtins/memory._put_to_the_user`), one that only adds stays as it
  was. The model's "User" is the user (`fact_names.users_names`); a
  list-labelled value on a fact the user keeps single takes the user's
  row's class (`belief_mutation._established_kind`); the name table also
  reads "hometown", "location", "city", "current_location", "address",
  "moved_to", "first_name", "user_name" and other separators ("lives-in")
  (item 5). The turn's note is read at iteration 0
  (`changes.read_changes_note`) and marked told only after the model
  answered (`changes.mark_noted`): a failed call does not spend it (item
  6). An operator's API token may confirm a change: it can already edit
  facts at `user_explicit` (the main session's decision). Forget takes a
  change with the fact, the turn, the chat or the text it holds
  (`changes.forget_facts`, `forget_turn`, `forget_chat`,
  `forget_mentioning`); it is in the export and the M01 plant. The accepted
  cost of the speaker rule's sentence end ("صديقتي معلمة. ساكنة في جدة"
  read as the user's) is an update the user can undo. The Memory API
  answers them for the caller's tenant: `GET /api/memory/v2/changes`
  (`state` open or decided) and `POST /api/memory/v2/changes/{change_id}/confirm`,
  `.../dismiss`, `.../undo` (a refusal is its code, with the recorder's
  status: 404, 409); they are in the memory tenant gate's REQUESTS, from
  which Spaces S1-09 makes their matrix cases. The Memory page's card
  "Changes to what you told Kazma" (`memory_console.js` `loadV2Changes`,
  EN/AR, each value a `<bdi translate="no">`) shows them with Undo,
  "Yes, change it" / "No, keep it", or for a reading "This is right" /
  "Not me". In the chat apps `/memory changes`
  lists them with a short id (`changes.short_ref`) and `/memory undo|confirm|dismiss <id>`
  decides one (`changes.decide_change`: a unique start of the id, refused
  `ambiguous` or `wrong_kind`); the four words are a gateway admin's
  (`slash_commands.MEMORY_CHANGE_WORDS` in `changes_global_config`: they
  show and rewrite the tenant's facts). The next turn of the chat a change
  came from is told once: the supervisor adds the turn note
  `memory_changes` at iteration 0 (`changes.read_changes_note`, off the
  loop; the changes whose `source_session` is one of the thread's ids,
  marked `noted_at` by `changes.mark_noted` once the model answered; the
  values fenced, Kazma's lines outside).
  It asks little (`changes._to_tell`): every update, at most one question
  a turn, never about a fact asked about in the last day, never a reading;
  the rest wait on the page and in `/memory changes`.
  A turn of another thread -- a scheduled task, a sub-agent, an A2A
  request -- never spends it; the model cannot apply a waiting change
  (no tool decides one: a confirmation is the user's channel). Gates:
  `tests/test_memory_changes.py` (EN/AR; the refusal with no change log
  and every question in one note as negative controls), the plant's
  control in `tests/test_memory_store_registry.py`, and in a browser,
  reached by the sidebar, `tests/e2e/test_memory_changes_browser.py` (an
  undo, a confirmation and Kazma's reading answered, the database read
  after each, EN/AR).
- **A fact said to have stopped being true is ended, by the same rules**
  (2026-11-20, the framework audit's part C2, retractions). The deep pass
  may return a fact with `"ended": true` (strictly the JSON true), naming
  the held fact it ends ("replaces", F4's numbers) or matching a current
  fact of the same subject, same-words name and value
  (`belief_extractor._apply_ending`, `_current_fact`); it is never written
  as a new fact. `belief_mutation.end_belief` ends it under the mutation
  lock in one transaction (`valid_until` = when it was said; the mirror
  and graph copies follow from their outboxes), and the trust gate
  decides: the model's own reading ends at once, a fact the user stated
  answers `blocked: lower_trust_source`. That is put to the user
  (`changes.record_pending_end`) only when the value stands in the user's
  own words with no one else named (`clause_subject.ends_in_own_words`:
  `names_no_one_else` with the denial allowed, since an ending denies; a
  question, a quote or someone else's ending asks nothing). An ending is a
  change with no new value (`changes.is_ending`): "Yes, it ended" (confirm)
  ends the fact at the user's trust and records an ending update, which
  Undo reverses; the page ("Has this ended?", "Ended from what you said",
  "No longer: ..."; catalog `memory.console.change_*end*`, EN/AR), the
  turn note and `/memory changes` say it. The quick pass reads no endings:
  the user's own words are asked about, as a correction only the model
  reads. Gates: `tests/test_memory_retractions.py` (the ending ignored, and
  the statement rule on a denial, as negative controls), the write
  scenarios' `retraction` category, and `tests/e2e/test_memory_changes_browser.py`
  (an ending confirmed and undone on the page, EN/AR).
- The heuristic reads "I work at/for X" as `works_at` (it shared a pattern
  with "I live in" and stored the employer as `lives_in`), and the local
  embedder remembers short texts (<= 256 characters, 4,096 of them): each
  extraction with a fact re-embedded every entity name of the tenant.

**H2. Bring your history** (2026-11-14, REMAINING 4.2,
`memory/history_import.py`): a ChatGPT or Claude export (`.zip` or
`conversations.json`, read at most `MAX_JSON_BYTES`) becomes episodes through
`dual_write.write_episode` -- the ledger asked first, one vector, each turn at
its own time (`_LATE_WRITERS`), source `history_import` (after the fact:
episodic, never the working buffer), chat `import-<source>-<id>`. ChatGPT: the
`current_node` branch only, visible text only (no tool calls or output, hidden
instructions, images); Claude: `chat_messages` text. A turn already held is
skipped before it is embedded (re-import goes on where it stopped). No facts
(the deep pass calls the model). Surfaces: `kazma import` and the Memory
page's card (`kazma_ui/history_import_api.py`, admin, single-operator, one
import at a time, the upload deleted when it ends). Gates:
`tests/test_history_import.py` (real writer, recall after),
`tests/e2e/test_history_import_browser.py`.

