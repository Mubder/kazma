"""The Memory page's own words (the page review of 2026-11-18).

Plain words for what each section shows and what each button does, in
English and Arabic: the page was the engine's words ("V2 Cognitive Engine",
"predicate", "Src/Tgt", "Invalidate", "FTS index").
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── The page and its status line ──
    "memory.page.intro": {
        "en": "What Kazma remembers from your conversations and what it has learned about you. Look through it, "
              "correct it, or make Kazma forget.",
        "ar": "ما يتذكره Kazma من محادثاتك وما تعلّمه عنك. تصفّحه، أو صحّحه، أو اجعل Kazma ينساه.",
    },
    "memory.page.status_checking": {"en": "Checking…", "ar": "جارٍ التحقق…"},
    "memory.page.status_checking_desc": {"en": "Checking whether memory is on.", "ar": "جارٍ التحقق من أن الذاكرة تعمل."},
    "memory.page.status_on": {"en": "On", "ar": "تعمل"},
    "memory.page.status_on_desc": {
        "en": "Kazma remembers your conversations and the facts you tell it.",
        "ar": "يتذكر Kazma محادثاتك والحقائق التي تخبره بها.",
    },
    "memory.page.status_problem": {"en": "Needs attention", "ar": "تحتاج إلى انتباه"},
    "memory.page.status_problem_desc": {
        "en": "Memory is running with less than it needs: search by meaning may be off. See Health and maintenance below.",
        "ar": "تعمل الذاكرة بأقل مما تحتاج إليه: قد يكون البحث بالمعنى متوقفًا. راجع قسم الصحة والصيانة أدناه.",
    },
    "memory.page.status_unknown": {"en": "Unknown", "ar": "غير معروف"},
    "memory.page.status_unknown_desc": {
        "en": "Kazma could not read memory's status. It tries again in a few seconds.",
        "ar": "تعذّر على Kazma قراءة حالة الذاكرة. سيحاول مجددًا بعد ثوانٍ.",
    },
    "memory.page.settings_link": {"en": "Memory settings", "ar": "إعدادات الذاكرة"},
    "memory.page.refresh_status": {"en": "Check again", "ar": "تحقّق مجددًا"},
    "memory.page.see_health": {"en": "See what needs attention", "ar": "اعرض ما يحتاج إلى انتباه"},

    # ── The memory map ──
    "memory.page.map_title": {"en": "Memory map", "ar": "خريطة الذاكرة"},
    "memory.page.map_intro": {
        "en": "Each dot is a person, place or thing Kazma knows of; each line is a fact that joins two of them. "
              "Click a dot or a line to see its facts and change them.",
        "ar": "كل نقطة شخص أو مكان أو شيء يعرفه Kazma، وكل خط حقيقة تربط بين اثنين منها. "
              "انقر نقطة أو خطًا لترى حقائقه وتغيّرها.",
    },
    "memory.page.map_find": {"en": "Find on the map…", "ar": "ابحث في الخريطة…"},
    "memory.page.map_find_label": {"en": "Find a name or fact on the map", "ar": "ابحث عن اسم أو حقيقة في الخريطة"},
    "memory.page.map_zoom_group": {"en": "Zoom", "ar": "التكبير"},
    "memory.page.map_zoom_in": {"en": "Zoom in", "ar": "تكبير"},
    "memory.page.map_zoom_out": {"en": "Zoom out", "ar": "تصغير"},
    "memory.page.map_fit": {"en": "Show all", "ar": "عرض الكل"},
    "memory.page.map_fit_hint": {"en": "Fit the whole map in view", "ar": "اعرض الخريطة كلها"},
    "memory.page.map_show_conversations": {"en": "Show conversations", "ar": "إظهار المحادثات"},
    "memory.page.map_show_conversations_hint": {
        "en": "Add the 30 newest conversation memories to the map, as faint dots",
        "ar": "أضف أحدث 30 ذكرى من المحادثات إلى الخريطة كنقاط باهتة",
    },
    "memory.page.map_reload": {"en": "Reload the map", "ar": "أعد تحميل الخريطة"},
    "memory.page.map_export": {"en": "Export", "ar": "تصدير"},
    "memory.page.export_png": {"en": "Picture (PNG)", "ar": "صورة (PNG)"},
    "memory.page.export_png_sub": {"en": "The map as it looks now", "ar": "الخريطة كما تظهر الآن"},
    "memory.page.export_svg": {"en": "Drawing (SVG)", "ar": "رسم (SVG)"},
    "memory.page.export_svg_sub": {"en": "Scales to any size", "ar": "يُكبَّر إلى أي حجم"},
    "memory.page.export_json": {"en": "Data (JSON)", "ar": "بيانات (JSON)"},
    "memory.page.export_json_sub": {"en": "Every dot and line, up to 500 dots", "ar": "كل نقطة وخط، حتى 500 نقطة"},
    "memory.page.export_graphml": {"en": "For graph tools (GraphML)", "ar": "لأدوات المخططات (GraphML)"},
    "memory.page.export_graphml_sub": {"en": "Opens in Gephi, yEd and similar", "ar": "يُفتح في Gephi وyEd وأمثالهما"},
    "memory.page.map_legend": {"en": "What the colours mean", "ar": "معاني الألوان"},
    "memory.page.map_aria": {
        "en": "Memory map. Arrow keys move it, plus and minus zoom, Home shows everything. "
              "Click a line to change or remove its fact.",
        "ar": "خريطة الذاكرة. تحرّكها مفاتيح الأسهم، ويكبّرها الزائد ويصغّرها الناقص، ويعرض المفتاح Home كل شيء. "
              "انقر خطًا لتغيير حقيقته أو إزالتها.",
    },
    "memory.page.map_loading": {"en": "Loading the map…", "ar": "جارٍ تحميل الخريطة…"},
    "memory.page.map_empty": {
        "en": "Nothing here yet. The map fills in as Kazma learns facts from your conversations.",
        "ar": "لا شيء هنا بعد. تمتلئ الخريطة كلما تعلّم Kazma حقائق من محادثاتك.",
    },
    "memory.page.map_empty_filtered": {
        "en": "Nothing on the map matches. Clear the search or the filters to see everything again.",
        "ar": "لا شيء في الخريطة يطابق. امسح البحث أو عوامل التصفية لترى كل شيء مجددًا.",
    },
    "memory.page.map_teach": {"en": "Tell Kazma something", "ar": "أخبر Kazma بشيء"},
    "memory.page.map_failed": {
        "en": "The map could not be loaded. This is not an empty memory.",
        "ar": "تعذّر تحميل الخريطة. هذا لا يعني أن الذاكرة فارغة.",
    },
    "memory.page.try_again": {"en": "Try again", "ar": "حاول مجددًا"},
    "memory.page.map_hint": {
        "en": "Drag to move · scroll or pinch to zoom · click a dot or a line",
        "ar": "اسحب للتحريك · مرّر أو قرّب بإصبعين للتكبير · انقر نقطة أو خطًا",
    },
    "memory.page.legend_you": {"en": "You", "ar": "أنت"},
    "memory.page.legend_line": {"en": "a fact", "ar": "حقيقة"},
    "memory.page.legend_ended": {"en": "a fact that ended", "ar": "حقيقة انتهت"},
    "memory.page.legend_conversation": {"en": "a conversation", "ar": "محادثة"},
    "memory.page.legend_value": {"en": "a value", "ar": "قيمة"},
    "memory.page.stats": {"en": "{things} · {facts}", "ar": "{things} · {facts}"},
    "memory.page.stats_filtered": {"en": "filtered, out of {all}", "ar": "بعد التصفية، من أصل {all}"},
    "memory.page.stats_capped": {
        "en": "the first {shown} of {all} are drawn",
        "ar": "رُسم أول {shown} من أصل {all}",
    },
    "memory.page.map_stale": {
        "en": "could not refresh: this is what was last loaded",
        "ar": "تعذّر التحديث: هذا آخر ما حُمّل",
    },
    "memory.page.things_n.zero": {"en": "{n} things", "ar": "لا أشياء"},
    "memory.page.things_n.one": {"en": "1 thing", "ar": "شيء واحد"},
    "memory.page.things_n.two": {"en": "{n} things", "ar": "شيئان"},
    "memory.page.things_n.few": {"en": "{n} things", "ar": "{n} أشياء"},
    "memory.page.things_n.many": {"en": "{n} things", "ar": "{n} شيئًا"},
    "memory.page.things_n.other": {"en": "{n} things", "ar": "{n} شيء"},
    "memory.page.facts_n.zero": {"en": "{n} facts", "ar": "لا حقائق"},
    "memory.page.facts_n.one": {"en": "1 fact", "ar": "حقيقة واحدة"},
    "memory.page.facts_n.two": {"en": "{n} facts", "ar": "حقيقتان"},
    "memory.page.facts_n.few": {"en": "{n} facts", "ar": "{n} حقائق"},
    "memory.page.facts_n.many": {"en": "{n} facts", "ar": "{n} حقيقة"},
    "memory.page.facts_n.other": {"en": "{n} facts", "ar": "{n} حقيقة"},
    "memory.page.unconnected_n.zero": {
        "en": "{n} things with no line under this filter",
        "ar": "لا أشياء بلا خط مع هذه التصفية",
    },
    "memory.page.unconnected_n.one": {
        "en": "1 thing with no line under this filter",
        "ar": "شيء واحد بلا خط مع هذه التصفية",
    },
    "memory.page.unconnected_n.two": {
        "en": "{n} things with no line under this filter",
        "ar": "شيئان بلا خط مع هذه التصفية",
    },
    "memory.page.unconnected_n.few": {
        "en": "{n} things with no line under this filter",
        "ar": "{n} أشياء بلا خط مع هذه التصفية",
    },
    "memory.page.unconnected_n.many": {
        "en": "{n} things with no line under this filter",
        "ar": "{n} شيئًا بلا خط مع هذه التصفية",
    },
    "memory.page.unconnected_n.other": {
        "en": "{n} things with no line under this filter",
        "ar": "{n} شيء بلا خط مع هذه التصفية",
    },
    "memory.page.tip_kind": {"en": "{kind} · {facts}", "ar": "{kind} · {facts}"},
    "memory.page.tip_you": {"en": "You: everything else connects to you", "ar": "أنت: كل شيء آخر يرتبط بك"},
    "memory.page.tip_click": {"en": "Click to see its facts", "ar": "انقر لترى حقائقه"},
    "memory.page.tip_line_click": {"en": "Click to change or remove this fact", "ar": "انقر لتغيير هذه الحقيقة أو إزالتها"},

    # ── Connecting or joining two things on the map ──
    "memory.page.connect_title": {"en": "Connect or join two things", "ar": "اربط شيئين أو ادمجهما"},
    "memory.page.connect_sub": {"en": "for fixing the map by hand", "ar": "لتصحيح الخريطة يدويًا"},
    "memory.page.connect_how": {
        "en": "Pick two things: click a dot, then From or To in its panel (or Shift-click two dots). Then connect "
              "them with a relation, or join the first into the second when they are the same thing.",
        "ar": "اختر شيئين: انقر نقطة ثم «من» أو «إلى» في لوحتها (أو انقر نقطتين مع Shift). ثم اربطهما بعلاقة، "
              "أو ادمج الأول في الثاني إذا كانا الشيء نفسه.",
    },
    "memory.page.connect_from_empty": {"en": "From: none", "ar": "من: لا شيء"},
    "memory.page.connect_to_empty": {"en": "To: none", "ar": "إلى: لا شيء"},
    "memory.page.connect_from": {"en": "From: {name}", "ar": "من: {name}"},
    "memory.page.connect_to": {"en": "To: {name}", "ar": "إلى: {name}"},
    "memory.page.connect_from_hint": {"en": "The first thing", "ar": "الشيء الأول"},
    "memory.page.connect_to_hint": {"en": "The second thing", "ar": "الشيء الثاني"},
    "memory.page.connect_relation": {"en": "Relation", "ar": "العلاقة"},
    "memory.page.connect_relation_hint": {
        "en": "How the first relates to the second, in a few words joined by _ (works_at, sister_of)",
        "ar": "كيف يرتبط الأول بالثاني، بكلمات قليلة تفصل بينها _ ‏(works_at، sister_of)",
    },
    "memory.page.connect_link": {"en": "Connect", "ar": "اربط"},
    "memory.page.connect_link_hint": {
        "en": "Add a fact: From, the relation, To",
        "ar": "أضف حقيقة: «من»، ثم العلاقة، ثم «إلى»",
    },
    "memory.page.connect_merge": {"en": "Join into one", "ar": "ادمجهما في واحد"},
    "memory.page.connect_merge_hint": {
        "en": "From and To are the same thing: move every fact of From to To. This cannot be undone.",
        "ar": "«من» و«إلى» شيء واحد: انقل كل حقائق «من» إلى «إلى». لا يمكن التراجع عن هذا.",
    },
    "memory.page.connect_clear": {"en": "Clear", "ar": "مسح"},
    "memory.page.connect_relations_in_use": {"en": "Relations already used:", "ar": "علاقات مستخدمة من قبل:"},
    "memory.page.connect_pick_two": {
        "en": "Pick a From and a To first: click a dot, then From or To in its panel.",
        "ar": "اختر «من» و«إلى» أولًا: انقر نقطة ثم «من» أو «إلى» في لوحتها.",
    },
    "memory.page.connect_confirm_title": {"en": "Add this fact?", "ar": "إضافة هذه الحقيقة؟"},
    "memory.page.connect_confirm": {
        "en": "Kazma will remember: {from} {relation} {to}.",
        "ar": "سيتذكر Kazma: {from} {relation} {to}.",
    },
    "memory.page.merge_confirm_title": {"en": "Join into one? This cannot be undone", "ar": "دمج في واحد؟ لا يمكن التراجع"},
    "memory.page.merge_confirm": {
        "en": "Every fact about {from} will move to {to}, and {from} will be removed. This cannot be undone.",
        "ar": "تنتقل كل الحقائق عن {from} إلى {to}، ويختفي {from}. لا يمكن التراجع عن هذا.",
    },
    "memory.page.merge_confirm_button": {"en": "Join them", "ar": "ادمجهما"},

    # ── Filters ──
    "memory.page.filter_title": {"en": "Filter the map", "ar": "صفِّ الخريطة"},
    "memory.page.filter_sub": {
        "en": "by kind of thing, kind of fact, or as it was on a day",
        "ar": "بنوع الشيء، أو نوع الحقيقة، أو كما كانت في يوم ما",
    },
    "memory.page.filter_things": {"en": "Kinds of things", "ar": "أنواع الأشياء"},
    "memory.page.filter_facts": {"en": "Kinds of facts", "ar": "أنواع الحقائق"},
    "memory.page.filter_when": {"en": "As it was on", "ar": "كما كانت في"},
    "memory.page.filter_when_hint": {
        "en": "Drag back to see what Kazma knew on an earlier day",
        "ar": "اسحب إلى الوراء لترى ما كان Kazma يعرفه في يوم سابق",
    },
    "memory.page.filter_play_hint": {"en": "Play the map's history from the start", "ar": "شغّل تاريخ الخريطة من البداية"},
    "memory.page.filter_now": {"en": "Now", "ar": "الآن"},
    "memory.page.filter_now_hint": {"en": "Back to what Kazma knows now", "ar": "عُد إلى ما يعرفه Kazma الآن"},
    "memory.page.filter_reset": {"en": "Clear filters", "ar": "امسح التصفية"},
    "memory.page.filter_remove": {"en": "Remove this filter", "ar": "أزل هذه التصفية"},
    "memory.page.recalled_n.zero": {"en": "recalled {n} times", "ar": "لم تُسترجع بعد"},
    "memory.page.recalled_n.one": {"en": "recalled once", "ar": "استُرجعت مرة واحدة"},
    "memory.page.recalled_n.two": {"en": "recalled {n} times", "ar": "استُرجعت مرتين"},
    "memory.page.recalled_n.few": {"en": "recalled {n} times", "ar": "استُرجعت {n} مرات"},
    "memory.page.recalled_n.many": {"en": "recalled {n} times", "ar": "استُرجعت {n} مرة"},
    "memory.page.recalled_n.other": {"en": "recalled {n} times", "ar": "استُرجعت {n} مرة"},

    # ── Facts ──
    "memory.page.facts_title": {"en": "What Kazma knows", "ar": "ما يعرفه Kazma"},
    "memory.page.facts_intro": {
        "en": "The facts Kazma has learned, and the changes made to the facts you told it.",
        "ar": "الحقائق التي تعلّمها Kazma، والتغييرات التي طرأت على الحقائق التي أخبرته بها.",
    },
    "memory.page.known_title": {"en": "Facts", "ar": "الحقائق"},
    "memory.page.known_hint": {
        "en": "Facts Kazma holds now. Click one to see where it came from, show it on the map, or remove it.",
        "ar": "الحقائق التي يحتفظ بها Kazma الآن. انقر إحداها لترى مصدرها، أو تعرضها في الخريطة، أو تزيلها.",
    },
    "memory.page.known_search": {"en": "Search the facts…", "ar": "ابحث في الحقائق…"},
    "memory.page.known_empty": {
        "en": "No facts yet. Tell Kazma something about yourself and it will remember.",
        "ar": "لا حقائق بعد. أخبر Kazma بشيء عنك وسيتذكره.",
    },
    "memory.page.known_none_match": {"en": "No fact holds these words.", "ar": "لا توجد حقيقة تحوي هذه الكلمات."},
    "memory.page.known_failed": {
        "en": "The facts could not be loaded. This is not an empty memory.",
        "ar": "تعذّر تحميل الحقائق. هذا لا يعني أن الذاكرة فارغة.",
    },
    "memory.page.known_detail": {"en": "About this fact", "ar": "عن هذه الحقيقة"},
    "memory.page.remove_fact": {"en": "Remove this fact", "ar": "أزل هذه الحقيقة"},
    "memory.page.remove_fact_hint": {
        "en": "Kazma stops using this fact. It stays in the record of past facts.",
        "ar": "يتوقف Kazma عن استخدام هذه الحقيقة. وتبقى في سجل الحقائق السابقة.",
    },
    "memory.page.remove_fact_confirm": {
        "en": "Remove this fact? Kazma stops using it. It stays in the record of past facts, and a later "
              "statement of yours can bring it back.",
        "ar": "إزالة هذه الحقيقة؟ سيتوقف Kazma عن استخدامها. وتبقى في سجل الحقائق السابقة، "
              "ويمكن لقولٍ لاحق منك أن يعيدها.",
    },
    "memory.page.remove": {"en": "Remove", "ar": "إزالة"},
    "memory.page.remove_selected": {"en": "Remove selected ({n})", "ar": "أزل المحدد ({n})"},
    "memory.page.edit_fact_hint": {"en": "Change what this fact says", "ar": "غيّر ما تقوله هذه الحقيقة"},
    "memory.page.show_on_map": {"en": "Show on map", "ar": "اعرض في الخريطة"},

    # ── Conversations ──
    "memory.page.conv_title": {"en": "Conversations Kazma remembers", "ar": "محادثات يتذكرها Kazma"},
    "memory.page.conv_intro": {
        "en": "What Kazma kept from your conversations, a summary of each week, and a search of all of it. "
              "Forget anything you don't want it to keep.",
        "ar": "ما احتفظ به Kazma من محادثاتك، وملخص لكل أسبوع، وبحث في ذلك كله. انسَ أي شيء لا تريده أن يحتفظ به.",
    },
    "memory.page.memories_hint": {
        "en": "One memory per question and answer. Forget removes it and the facts learned from it.",
        "ar": "ذكرى لكل سؤال وجوابه. «انسَ» يزيلها ويزيل الحقائق التي تعلّمها منها.",
    },
    "memory.page.export_all": {"en": "Download all", "ar": "نزّل الكل"},
    "memory.page.refresh_memories": {"en": "Reload the memories", "ar": "أعد تحميل الذكريات"},
    "memory.page.summaries_hint": {
        "en": "Written by Kazma a day after each week ends, one per topic.",
        "ar": "يكتبها Kazma بعد يوم من نهاية كل أسبوع، ملخص لكل موضوع.",
    },
    "memory.page.refresh_summaries": {"en": "Reload the summaries", "ar": "أعد تحميل الملخصات"},
    "memory.page.search_title": {"en": "Search memory", "ar": "ابحث في الذاكرة"},
    "memory.page.search_hint": {
        "en": "Ask a question and see what Kazma would recall for it. Nothing is changed.",
        "ar": "اطرح سؤالًا وانظر ما سيستدعيه Kazma له. لا يتغيّر شيء.",
    },
    "memory.page.search_include_kb": {"en": "Include the Knowledge Library", "ar": "اشمل مكتبة المعرفة"},
    "memory.page.search_placeholder": {"en": "What colour do I like?", "ar": "ما اللون الذي أحبه؟"},
    "memory.page.search_go": {"en": "Search", "ar": "ابحث"},
    "memory.page.search_go_hint": {
        "en": "Search memory, and the Knowledge Library when it is included",
        "ar": "ابحث في الذاكرة، وفي مكتبة المعرفة عند شمولها",
    },
    "memory.page.search_memory_only": {"en": "Memory only", "ar": "الذاكرة فقط"},
    "memory.page.search_memory_only_hint": {
        "en": "Search memory alone, exactly as a chat turn recalls it",
        "ar": "ابحث في الذاكرة وحدها، تمامًا كما تستدعيها المحادثة",
    },

    # ── Clean up memory (the operator tools) ──
    "memory.page.cleanup_title": {"en": "Clean up memory", "ar": "رتّب الذاكرة"},
    "memory.page.cleanup_sub": {
        "en": "Fix names, join duplicates and tidy old facts. Most people never need this.",
        "ar": "صحّح الأسماء، وادمج المكرّر، ورتّب الحقائق القديمة. لا يحتاج معظم الناس إلى هذا أبدًا.",
    },
    "memory.page.tab_facts": {"en": "Facts", "ar": "الحقائق"},
    "memory.page.tab_things": {"en": "Things", "ar": "الأشياء"},
    "memory.page.tab_joins": {"en": "Suggested joins", "ar": "دمج مقترح"},
    "memory.page.tab_tidy": {"en": "Tidy up", "ar": "الترتيب"},
    "memory.page.tab_facts_hint": {
        "en": "Every fact Kazma holds now. Tick facts to remove several at once.",
        "ar": "كل الحقائق التي يحتفظ بها Kazma الآن. حدّد حقائق لتزيل عدة حقائق دفعة واحدة.",
    },
    "memory.page.tab_things_hint": {
        "en": "The people, places and things Kazma knows of. Rename one, protect it, or delete a name that "
              "holds nothing.",
        "ar": "الأشخاص والأماكن والأشياء التي يعرفها Kazma. أعد تسمية أحدها، أو احمِه، أو احذف اسمًا لا يحمل شيئًا.",
    },
    "memory.page.tab_joins_hint": {
        "en": "Pairs Kazma thinks are the same thing under two names. Joining moves every fact to one of them "
              "and cannot be undone.",
        "ar": "أزواج يظن Kazma أنها الشيء نفسه باسمين. الدمج ينقل كل الحقائق إلى أحدهما ولا يمكن التراجع عنه.",
    },
    "memory.page.tab_tidy_hint": {
        "en": "Clean-ups you can run by hand. Pick the ones you want, then run them.",
        "ar": "عمليات ترتيب يمكنك تشغيلها يدويًا. اختر ما تريد ثم شغّلها.",
    },
    "memory.page.facts_search": {"en": "Search the facts…", "ar": "ابحث في الحقائق…"},
    "memory.page.facts_caption": {
        "en": "Facts. Tick facts to remove them, or use a row's buttons to show, change or remove one.",
        "ar": "الحقائق. حدّد حقائق لإزالتها، أو استخدم أزرار الصف لعرض حقيقة أو تغييرها أو إزالتها.",
    },
    "memory.page.facts_select_all": {"en": "Select every fact shown", "ar": "حدّد كل الحقائق المعروضة"},
    "memory.page.col_about": {"en": "About", "ar": "عن"},
    "memory.page.col_relation": {"en": "Relation", "ar": "العلاقة"},
    "memory.page.col_value": {"en": "Value", "ar": "القيمة"},
    "memory.page.col_kind": {"en": "Kind", "ar": "النوع"},
    "memory.page.col_id": {"en": "ID", "ar": "المعرّف"},
    "memory.page.col_facts": {"en": "Facts", "ar": "الحقائق"},
    "memory.page.col_connections": {"en": "Connections", "ar": "الروابط"},
    "memory.page.col_status": {"en": "Notes", "ar": "ملاحظات"},
    "memory.page.col_join": {"en": "Join", "ar": "ادمج"},
    "memory.page.col_into": {"en": "Into", "ar": "في"},
    "memory.page.col_why": {"en": "Why Kazma thinks so", "ar": "لماذا يظن Kazma ذلك"},
    "memory.page.col_confidence": {"en": "Confidence", "ar": "الثقة"},
    "memory.page.things_search": {"en": "Search the things…", "ar": "ابحث في الأشياء…"},
    "memory.page.things_caption": {
        "en": "Things. Click a row to show it on the map; use its buttons to rename, protect or delete it.",
        "ar": "الأشياء. انقر صفًا لعرضه في الخريطة، واستخدم أزراره لإعادة تسميته أو حمايته أو حذفه.",
    },
    "memory.page.things_connect_hint": {
        "en": "To connect or join two things, use From and To on their rows, then Connect or Join in the map's "
              "\"Connect or join two things\".",
        "ar": "لربط شيئين أو دمجهما، استخدم «من» و«إلى» في صفّيهما، ثم «اربط» أو «ادمج» في قسم "
              "«اربط شيئين أو ادمجهما» تحت الخريطة.",
    },
    "memory.page.only_empty": {"en": "Only ones with no facts", "ar": "ما ليس له حقائق فقط"},
    "memory.page.only_unconnected": {"en": "Only unconnected ones", "ar": "غير المرتبطة فقط"},
    "memory.page.unconnected_hint": {
        "en": "Unconnected: it has facts, but no line to anything else on the map. Connect it, or join it into "
              "the thing it really is.",
        "ar": "غير مرتبط: له حقائق، لكن لا خط يصله بشيء آخر في الخريطة. اربطه، أو ادمجه في الشيء الذي هو عليه فعلًا.",
    },
    "memory.page.badge_empty": {"en": "no facts", "ar": "بلا حقائق"},
    "memory.page.badge_empty_hint": {"en": "No fact names it any more", "ar": "لم تعد أي حقيقة تذكره"},
    "memory.page.badge_unconnected": {"en": "unconnected", "ar": "غير مرتبط"},
    "memory.page.badge_unconnected_hint": {
        "en": "It has facts, but no line to anything else",
        "ar": "له حقائق، لكن لا خط يصله بأي شيء آخر",
    },
    "memory.page.set_from": {"en": "From", "ar": "من"},
    "memory.page.set_from_hint": {"en": "Use as the first of two things to connect or join", "ar": "استخدمه أولَ شيئين للربط أو الدمج"},
    "memory.page.set_to": {"en": "To", "ar": "إلى"},
    "memory.page.set_to_hint": {"en": "Use as the second of two things to connect or join", "ar": "استخدمه ثانيَ شيئين للربط أو الدمج"},
    "memory.page.delete_thing_hint": {
        "en": "Delete this name. Facts about it stay; only the name goes.",
        "ar": "احذف هذا الاسم. تبقى الحقائق عنه؛ يُحذف الاسم فقط.",
    },
    "memory.page.delete_thing_confirm": {
        "en": "Delete {name}? The facts that mention it stay. An Undo button shows for a few seconds after.",
        "ar": "حذف {name}؟ تبقى الحقائق التي تذكره. يظهر زر للتراجع لثوانٍ بعد الحذف.",
    },
    "memory.page.joins_caption": {
        "en": "Suggested joins: two names Kazma thinks are one thing.",
        "ar": "دمج مقترح: اسمان يظن Kazma أنهما شيء واحد.",
    },
    "memory.page.joins_none": {"en": "No joins to decide.", "ar": "لا يوجد دمج بانتظار قرارك."},
    "memory.page.join_yes": {"en": "Join them", "ar": "ادمجهما"},
    "memory.page.join_yes_hint": {
        "en": "Move every fact to the second name. Cannot be undone.",
        "ar": "انقل كل الحقائق إلى الاسم الثاني. لا يمكن التراجع عنه.",
    },
    "memory.page.join_no": {"en": "Keep apart", "ar": "أبقهما منفصلين"},
    "memory.page.join_no_hint": {"en": "They are different things; don't ask again", "ar": "هما شيئان مختلفان؛ لا تسأل مجددًا"},
    "memory.page.join_confirm_title": {"en": "Join them? This cannot be undone", "ar": "دمجهما؟ لا يمكن التراجع"},
    "memory.page.join_confirm": {
        "en": "Every fact about {from} will move to {to}, and {from} will be removed.",
        "ar": "تنتقل كل الحقائق عن {from} إلى {to}، ويختفي {from}.",
    },
    "memory.page.merge_tier_exact": {"en": "Same name", "ar": "الاسم نفسه"},
    "memory.page.merge_tier_alias": {"en": "Same name written another way", "ar": "الاسم نفسه بكتابة أخرى"},
    "memory.page.merge_tier_vector": {"en": "Very close in meaning", "ar": "قريبان جدًا في المعنى"},
    "memory.page.merge_tier_llm": {"en": "Kazma's reading", "ar": "قراءة Kazma"},
    "memory.page.tidy_empty": {"en": "Delete names that hold no facts", "ar": "احذف الأسماء التي لا تحمل حقائق"},
    "memory.page.tidy_empty_hint": {
        "en": "A name stays after its last fact goes. Protected names and the names that keep a join working "
              "are kept.",
        "ar": "يبقى الاسم بعد زوال آخر حقائقه. تُحفظ الأسماء المحمية والأسماء التي يعتمد عليها دمج سابق.",
    },
    "memory.page.tidy_dupes": {"en": "Remove notes that repeat another", "ar": "أزل الملاحظات التي تكرّر غيرها"},
    "memory.page.tidy_dupes_hint": {
        "en": "Notes Kazma saved that say almost the same thing; the newest of each group stays. The number is "
              "how many groups.",
        "ar": "ملاحظات حفظها Kazma تقول الشيء نفسه تقريبًا؛ تبقى أحدث ملاحظة في كل مجموعة. الرقم عدد المجموعات.",
    },
    "memory.page.tidy_archive": {"en": "Move ended facts to the archive", "ar": "انقل الحقائق المنتهية إلى الأرشيف"},
    "memory.page.tidy_archive_hint": {
        "en": "Facts that were removed or replaced, up to 2,000 a run. After this, a change made before it can "
              "no longer be undone.",
        "ar": "الحقائق التي أُزيلت أو استُبدلت، حتى 2,000 في كل مرة. بعد ذلك، لا يمكن التراجع عن تغيير سبقه.",
    },
    "memory.page.tidy_run": {"en": "Run the selected clean-ups", "ar": "شغّل عمليات الترتيب المحددة"},
    "memory.page.tidy_confirm": {"en": "Run these clean-ups?", "ar": "تشغيل عمليات الترتيب هذه؟"},
    "memory.page.unconnected_things": {"en": "Unconnected things", "ar": "أشياء غير مرتبطة"},
    "memory.page.show_in_things": {"en": "Show in Things", "ar": "اعرض في الأشياء"},

    # ── Health and maintenance ──
    "memory.page.health_title": {"en": "Health and maintenance", "ar": "الصحة والصيانة"},
    "memory.page.health_sub": {
        "en": "How memory is running, its backups, and its upkeep. Open it when something seems wrong.",
        "ar": "كيف تعمل الذاكرة، ونسخها الاحتياطية، وصيانتها. افتحه إذا بدا أن شيئًا ما ليس على ما يرام.",
    },
    "memory.page.health_parts": {"en": "Memory's parts", "ar": "أجزاء الذاكرة"},
    "memory.page.health_parts_ok": {"en": "Working", "ar": "تعمل"},
    "memory.page.health_parts_ok_sub": {"en": "parts working", "ar": "أجزاء تعمل"},
    "memory.page.install_ml": {"en": "Install search by meaning", "ar": "ثبّت البحث بالمعنى"},
    "memory.page.engine_title": {"en": "Memory engine", "ar": "محرك الذاكرة"},
    "memory.page.kpi_facts": {"en": "Facts", "ar": "الحقائق"},
    "memory.page.kpi_memories": {"en": "Conversation memories", "ar": "ذكريات المحادثات"},
    "memory.page.kpi_things": {"en": "Things", "ar": "الأشياء"},
    "memory.page.kpi_queue": {"en": "Work waiting", "ar": "عمل منتظر"},
    "memory.page.after_each_reply": {"en": "After each reply", "ar": "بعد كل رد"},
    "memory.page.pipe_recall": {"en": "Recalls before replying", "ar": "يستدعي قبل الرد"},
    "memory.page.pipe_store": {"en": "Keeps facts", "ar": "يحفظ الحقائق"},
    "memory.page.pipe_tidy": {"en": "Tidies in the background", "ar": "يرتّب في الخلفية"},
    "memory.page.queue_title": {"en": "Background work", "ar": "العمل في الخلفية"},
    "memory.page.queue_hint": {
        "en": "Work memory does after your conversations: reading facts, summaries, backups. A failed task is "
              "retried, then kept here.",
        "ar": "عمل تقوم به الذاكرة بعد محادثاتك: قراءة الحقائق، والملخصات، والنسخ الاحتياطية. "
              "تُعاد المهمة الفاشلة، ثم تبقى هنا.",
    },
    "memory.page.queue_clear_failed": {"en": "Delete failed tasks", "ar": "احذف المهام الفاشلة"},
    "memory.page.refresh_queue": {"en": "Reload the work list", "ar": "أعد تحميل قائمة العمل"},
    "memory.page.reconsolidate": {"en": "Merge duplicate facts now", "ar": "ادمج الحقائق المكرّرة الآن"},
    "memory.page.reconsolidate_hint": {
        "en": "Kazma does this every day. Run it now to merge facts stored twice and refresh their search data.",
        "ar": "يقوم Kazma بهذا كل يوم. شغّله الآن لدمج الحقائق المحفوظة مرتين وتحديث بيانات البحث فيها.",
    },
    "memory.page.reconsolidate_confirm_title": {"en": "Merge duplicate facts now?", "ar": "دمج الحقائق المكرّرة الآن؟"},
    "memory.page.reconsolidate_confirm": {
        "en": "Facts stored twice are merged into one, for everyone using this Kazma. It runs in the background "
              "and cannot be undone.",
        "ar": "تُدمج الحقائق المحفوظة مرتين في واحدة، لكل من يستخدم Kazma هذا. يعمل في الخلفية ولا يمكن التراجع عنه.",
    },
    "memory.page.reconsolidate_go": {"en": "Merge now", "ar": "ادمج الآن"},
    "memory.page.procedures_title": {"en": "Learned procedures", "ar": "الإجراءات المتعلَّمة"},
    "memory.page.procedures_hint": {
        "en": "Sequences of tool steps that worked, which Kazma reuses for similar tasks.",
        "ar": "سلاسل من خطوات الأدوات نجحت، يعيد Kazma استخدامها لمهام مشابهة.",
    },
    "memory.page.refresh_procedures": {"en": "Reload the procedures", "ar": "أعد تحميل الإجراءات"},
    "memory.page.tiers_title": {"en": "How memories are kept", "ar": "كيف تُحفظ الذكريات"},
    "memory.page.tiers_hint": {
        "en": "New memories start in use; old ones that are never recalled move to the archive, and can still "
              "be found.",
        "ar": "تبدأ الذكريات الجديدة قيد الاستخدام؛ وتنتقل القديمة التي لا تُستدعى إلى الأرشيف، ويمكن العثور عليها مع ذلك.",
    },
    "memory.page.backups_title": {"en": "Backups and upkeep", "ar": "النسخ الاحتياطية والصيانة"},
    "memory.page.backups_hint": {
        "en": "Kazma backs memory up every 6 hours by itself. Back up now before a big change, or bring back an "
              "earlier copy.",
        "ar": "ينسخ Kazma الذاكرة احتياطيًا كل 6 ساعات من تلقاء نفسه. انسخها الآن قبل تغيير كبير، أو استعد نسخة سابقة.",
    },
    "memory.page.backups_show": {"en": "Show backups", "ar": "اعرض النسخ"},
    "memory.page.backups_hide": {"en": "Hide backups", "ar": "أخفِ النسخ"},
    "memory.page.backup_now": {"en": "Back up now", "ar": "انسخ احتياطيًا الآن"},
    "memory.page.backup_now_hint": {
        "en": "Copy memory and its work list now; the newest 10 copies are kept",
        "ar": "انسخ الذاكرة وقائمة عملها الآن؛ تُحفظ أحدث 10 نسخ",
    },
    "memory.page.optimize": {"en": "Compact the databases", "ar": "اضغط قواعد البيانات"},
    "memory.page.optimize_hint": {
        "en": "Reclaim the space deleted data left behind (SQLite VACUUM and ANALYZE)",
        "ar": "استعد المساحة التي خلّفتها البيانات المحذوفة (VACUUM وANALYZE في SQLite)",
    },
    "memory.page.snapshots": {"en": "Clean up Time Travel steps", "ar": "نظّف خطوات السفر عبر الزمن"},
    "memory.page.snapshots_hint": {
        "en": "Delete chat steps older than Time Travel keeps, and reclaim their space",
        "ar": "احذف خطوات المحادثات الأقدم مما يحتفظ به السفر عبر الزمن، واستعد مساحتها",
    },
    "memory.page.backups_caption": {"en": "Backups of memory, newest first", "ar": "نسخ الذاكرة الاحتياطية، الأحدث أولًا"},
    "memory.page.backup_col_made": {"en": "Made", "ar": "أُنشئت"},
    "memory.page.backup_col_size": {"en": "Size", "ar": "الحجم"},
    "memory.page.backup_col_file": {"en": "File", "ar": "الملف"},
    "memory.page.backups_none": {
        "en": "No backups yet. Kazma makes one every 6 hours, or back up now above.",
        "ar": "لا نسخ احتياطية بعد. ينشئ Kazma نسخة كل 6 ساعات، أو انسخ الآن من الأعلى.",
    },
    "memory.page.backup_made": {"en": "Backed up: {files}", "ar": "نُسخت احتياطيًا: {files}"},
    "memory.page.backup_nothing": {
        "en": "Nothing was backed up: memory has no database yet, or the copy failed. See the log.",
        "ar": "لم يُنسخ شيء: لا قاعدة بيانات للذاكرة بعد، أو فشل النسخ. راجع السجل.",
    },
    "memory.page.optimize_done": {"en": "Both databases were compacted.", "ar": "ضُغطت قاعدتا البيانات كلتاهما."},
    "memory.page.optimize_partial": {
        "en": "Not every database could be compacted:",
        "ar": "تعذّر ضغط بعض قواعد البيانات:",
    },
    "memory.page.restore_done": {
        "en": "Memory is back to that copy. The conversations since it are being read again.",
        "ar": "عادت الذاكرة إلى تلك النسخة. وتُقرأ المحادثات التي تلتها من جديد.",
    },
    "memory.page.restore_not_memory": {
        "en": "That file is not a backup of memory, so nothing was restored.",
        "ar": "هذا الملف ليس نسخة احتياطية من الذاكرة، لذلك لم يُستعد شيء.",
    },
    "memory.page.restore_confirm": {
        "en": "Bring memory back to the copy made {when}? What Kazma learned since is replaced; the "
              "conversations since are read again, and what you asked it to forget stays forgotten.",
        "ar": "إعادة الذاكرة إلى النسخة المنشأة {when}؟ سيُستبدل ما تعلّمه Kazma منذ ذلك الحين؛ وتُقرأ المحادثات "
              "منذئذ من جديد، ويبقى منسيًا ما طلبت منه نسيانه.",
    },
}
