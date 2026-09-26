"""Memory retrieval benchmark (Stage 2, R7): build it, record it, run it.

  python scripts/memory_bench.py build             # the dataset JSON (deterministic)
  python scripts/memory_bench.py vectors           # record bge-m3's vectors for CI
  python scripts/memory_bench.py run [--real]      # score recall(); --real loads the model
  python scripts/memory_bench.py lock              # raise the CI ratchet to today's scores

The dataset is a persona's life told across dated chat sessions -- facts the
user states, facts only the assistant said, facts that change, codes and
numbers, a Gulf-Arabic thread -- inside a large pool of unrelated chat that
carries the traps recall has fallen into: substrings of question words ("out"
in "about"), stopword-heavy text, filler turns, near-miss facts about other
people. Every question names the memories that answer it (``gold``) or none
(``abstain``: nothing should be injected). The runner is
``kazma_core.memory.benchmark``; ``tests/test_memory_benchmark.py`` holds the
scores from the recorded vectors to a ratchet.

Reads no install: the benchmark writes its own temp database.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "kazma-core" / "kazma_core" / "memory" / "benchmark_data" / "dataset.json"
VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
THRESHOLDS = REPO / "tests" / "fixtures" / "memory_bench" / "thresholds.json"

SEED = 20260926

# ── The persona: (session id, day, [(user, assistant), ...]) ──────────────
PERSONA: list[tuple[str, int, list[tuple[str, str]]]] = [
    ("p01", 0, [
        ("Hi! Quick intro so you know me: I'm Sami, I live in Porto and I work as a structural engineer at Aurelia Bridges.",
         "Nice to meet you, Sami! Porto and bridge engineering, a good combination. How can I help?"),
        ("I have a greyhound called Pixel, she's four years old and very lazy.",
         "Greyhounds are famous couch potatoes. Pixel sounds lovely."),
    ]),
    ("p02", 2, [
        ("I'm allergic to shellfish, so please keep that in mind for any recipes.",
         "Noted: no shrimp, crab, lobster, mussels or clams in anything I suggest."),
        ("My partner is Noor, she teaches chemistry at a secondary school.",
         "Chemistry teachers make everything sound like an experiment. Say hi to Noor."),
    ]),
    ("p03", 3, [
        ("Can you help me plan training for the Lisbon half marathon on March 22?",
         "Sure. A 12-week plan: three easy runs a week to start, one long run on Sundays growing from 8 km to 18 km, and a taper in the last two weeks."),
    ]),
    ("p04", 5, [
        ("What's a good restaurant for Noor's birthday dinner in Porto? She loves seafood but I can't eat it.",
         "I'd book Cantinho do Avillez on Rua Mouzinho da Silveira: excellent seafood for her and a strong meat menu for you."),
    ]),
    ("p05", 8, [
        ("My passport expires on 14 March 2031, remind me to renew it six months before.",
         "I'll remind you in September 2030 to start the renewal."),
    ]),
    ("p06", 10, [
        ("Work update: I'm leading the Tagus footbridge retrofit project, the deadline is the end of June.",
         "Congratulations on leading it. End of June is tight for a retrofit; want a milestone plan?"),
        ("My manager there is Inês Carvalho, very detail oriented.",
         "Good to know. Detailed status reports will go a long way with Inês."),
    ]),
    ("p07", 12, [
        ("My daughter Maya is seven and just started at Escola Azul.",
         "A new school is a big step. I hope Maya settles in quickly."),
    ]),
    ("p08", 13, [
        ("School starts at 8:40 every morning, so I need to leave home by 8:10.",
         "Then a 7:30 alarm leaves room for breakfast."),
    ]),
    ("p09", 15, [
        ("I drive a 2019 Skoda Octavia estate, diesel.",
         "A practical car for a family with a greyhound."),
    ]),
    ("p10", 18, [
        ("Remember that my blood type is O negative.",
         "Saved: your blood type is O negative."),
    ]),
    ("p11", 20, [
        ("Can you compute the monthly mortgage payment on 240,000 euros over 30 years at 3.1% interest?",
         "The monthly payment comes to about 1,025 euros, roughly 129,000 euros of interest over the loan."),
    ]),
    ("p12", 22, [
        ("I take guitar lessons on Thursdays at 7pm with a teacher called Rui.",
         "Thursday evenings with Rui, nice. Classical or electric?"),
    ]),
    ("p13", 25, [
        ("My gym days are Tuesday and Friday mornings before work.",
         "Morning sessions twice a week, a solid routine."),
    ]),
    ("p14", 30, [
        ("Big news: we're moving from Porto to Braga next month.",
         "Exciting! Do you want a moving checklist?"),
    ]),
    ("p15", 45, [
        ("We finished the move, we now live in Braga near the Bom Jesus sanctuary.",
         "Welcome to Braga. The view from Bom Jesus is worth the climb."),
    ]),
    ("p16", 50, [
        ("Change of plan: my gym days are now Monday and Thursday evenings.",
         "Updated: Monday and Thursday evenings."),
    ]),
    ("p17", 55, [
        ("I switched jobs, I'm now a senior structural engineer at Norte Engenharia.",
         "Congratulations on the new role at Norte Engenharia!"),
    ]),
    ("p18", 60, [
        ("My dentist is Dr. Marta Pires on Rua de Santa Catarina.",
         "Noted: Dr. Marta Pires."),
    ]),
    ("p19", 62, [
        ("Our book club needs a novel for next month, any ideas?",
         "I'd suggest The Remains of the Day by Kazuo Ishiguro: short, quiet and great to discuss."),
    ]),
    ("p20", 65, [
        ("My mother's birthday is on 11 June and she loves orchids.",
         "An orchid it is, then. Shall I remind you on 1 June?"),
    ]),
    ("p21", 70, [
        ("My ThinkPad X1 Carbon battery drains really fast lately.",
         "Set the charge threshold to 80 percent in Lenovo Vantage and switch the power mode to Better Battery; that usually helps."),
    ]),
    ("p22", 72, [
        ("The booking reference for our Lisbon hotel is ZX4-91Q.",
         "Stored: ZX4-91Q."),
    ]),
    ("p23", 75, [
        ("Our flight to Lisbon is TP1352 on March 20, leaving at 07:15.",
         "TP1352 at 07:15, plan to be at the airport by 05:45."),
    ]),
    ("p24", 80, [
        ("For the record, I take my coffee as a cortado with no sugar.",
         "A cortado with no sugar, got it."),
    ]),
    ("p25", 85, [
        ("Pixel's vet is Clinica Veterinaria do Lima and her vaccines are due in October.",
         "I'll remind you in late September about Pixel's vaccines."),
    ]),
    ("p26", 90, [
        ("Noor was promoted to head of the science department.",
         "That's wonderful news, congratulations to Noor!"),
    ]),
    ("p27", 95, [
        ("The Tagus retrofit deadline moved to mid-September.",
         "Mid-September gives you breathing room. Updated."),
    ]),
    ("p28", 100, [
        ("We signed Maya up for swimming at Piscina Municipal da Rodovia on Saturday mornings.",
         "Saturday swimming, great for her."),
    ]),
    ("p29", 105, [
        ("I'm thinking of selling the Octavia and buying an electric car, maybe a Kia EV6.",
         "The EV6 has good range; check home charging first."),
    ]),
    ("p30", 110, [
        ("Decided: we're keeping the Octavia for another year.",
         "Sensible, the Octavia still has plenty of life."),
    ]),
    # Gulf-Arabic thread: the same user, in Arabic.
    ("a01", 4, [
        ("أخوي اسمه فهد ويشتغل في بنك الكويت الوطني.",
         "حلو، الله يوفقه في شغله."),
    ]),
    ("a02", 9, [
        ("موعد الدكتور يوم الأحد الساعة أربعة العصر في مستشفى دار الشفاء.",
         "تمام، بذكرك قبلها بساعة."),
    ]),
    ("a03", 16, [
        ("أحب القهوة العربية بالهيل والزعفران.",
         "ذوقك رايق، القهوة بالزعفران غير."),
    ]),
    ("a04", 27, [
        ("رحلتنا إلى إسطنبول في ديسمبر، حجزنا فندق قريب من ساحة تقسيم.",
         "إسطنبول في ديسمبر باردة شوي، خذوا جاكيتات."),
    ]),
    ("a05", 33, [
        ("رقم ملفي في المستشفى هو 55821.",
         "انحفظ: رقم الملف 55821."),
    ]),
    ("a06", 48, [
        ("بنتي مايا تحب الرسم وتروح دورة رسم كل يوم أربعاء.",
         "ما شاء الله، الرسم ينمي خيالها."),
    ]),
    ("a07", 67, [
        ("غيرنا موعد الدكتور إلى يوم الثلاثاء الساعة عشرة الصبح.",
         "تم، الموعد صار الثلاثاء الساعة عشرة الصبح."),
    ]),
    ("a08", 88, [
        ("أبي رواية عربية حلوة أقراها في الإجازة.",
         "أنصحك برواية موسم الهجرة إلى الشمال للطيب صالح."),
    ]),
]

# ── Near misses: true facts about OTHER people, sharing words with persona facts.
HARD_NEGATIVES: list[tuple[str, int, list[tuple[str, str]]]] = [
    ("h01", 7, [("My colleague Pedro lives in Lisbon and cycles to work.", "Cycling in Lisbon takes strong legs!")]),
    ("h02", 14, [("Noor's sister Rana is allergic to peanuts, not shellfish.", "Good to know for family dinners.")]),
    ("h03", 21, [("My friend Tiago has a greyhound too, his is called Bolt.", "Pixel and Bolt should meet!")]),
    ("h04", 34, [("My old manager at the first job was Paulo Mendes.", "Noted.")]),
    ("h05", 41, [("Where can I get a passport photo taken in Porto?", "Most Fotoplus shops in Porto do passport photos in ten minutes.")]),
    ("h06", 58, [("Inês is presenting the Douro viaduct inspection on Friday.", "Hope the presentation goes well.")]),
    ("h07", 77, [("My brother-in-law's flight is TP1330, not ours.", "Understood, TP1330 is his.")]),
    ("h08", 93, [("Rui, my guitar teacher, is also a luthier.", "A teacher who builds guitars, lucky you.")]),
]

# ── Facts: what extraction stores (dataset v2) ─────────────────────────────
# (fact id, source, subject, predicate, object, predicate type, extraction
# method, importance). The source is the turn the fact came from
# ("<session>#<turn>", seeded with that turn's session and number, so the
# belief is linked to its episode the way live extraction links it) or
# "@<day>" for a fact with no turn -- a memory_store note, a tool's fact.
# Seeded in time order through ``mutate_belief``: a functional fact said
# later supersedes the earlier one, exactly as on an install, so the stale
# value is not a current belief.
FACTS: list[tuple[str, str, str, str, str, str, str, int]] = [
    ("f01", "p01#1", "user", "name", "Sami", "functional", "llm_inferred", 4),
    ("f02", "p01#1", "user", "lives_in", "Porto", "functional", "llm_inferred", 4),
    ("f03", "p01#1", "user", "works_at", "Aurelia Bridges", "functional", "llm_inferred", 4),
    ("f04", "p01#1", "user", "job_title", "structural engineer", "functional", "llm_inferred", 3),
    ("f05", "p01#2", "user", "has_dog", "Pixel", "set", "llm_inferred", 3),
    ("f37", "p01#2", "pixel", "breed", "greyhound, four years old", "functional", "llm_inferred", 2),
    ("f06", "p02#1", "user", "allergic_to", "shellfish", "set", "llm_inferred", 5),
    ("f07", "p02#2", "user", "partner", "Noor", "functional", "llm_inferred", 4),
    ("f08", "p02#2", "noor", "teaches", "chemistry at a secondary school", "set", "llm_inferred", 3),
    ("f09", "p03#1", "user", "training_for", "Lisbon half marathon on March 22", "set", "llm_inferred", 3),
    ("f10", "p05#1", "user", "passport_expires", "14 March 2031", "functional", "llm_inferred", 4),
    ("f11", "p06#1", "user", "leads_project", "Tagus footbridge retrofit", "set", "llm_inferred", 3),
    ("f12a", "p06#1", "tagus_footbridge_retrofit", "deadline", "end of June", "functional", "llm_inferred", 3),
    ("f13", "p06#2", "user", "manager", "Inês Carvalho", "functional", "llm_inferred", 3),
    ("f14", "p07#1", "maya", "school", "Escola Azul", "functional", "llm_inferred", 3),
    ("f15", "p07#1", "user", "has_daughter", "Maya, seven years old", "set", "llm_inferred", 4),
    ("f16", "p08#1", "maya", "school_start_time", "8:40, leave home by 8:10", "functional", "llm_inferred", 3),
    ("f17", "p09#1", "user", "drives", "2019 Skoda Octavia estate, diesel", "functional", "llm_inferred", 3),
    ("f18", "p10#1", "user", "blood_type", "O negative", "functional", "user_explicit", 5),
    ("f19", "p12#1", "user", "guitar_lessons", "Thursdays at 7pm with Rui", "set", "llm_inferred", 3),
    ("f20", "p13#1", "user", "gym_days", "Tuesday and Friday mornings", "functional", "llm_inferred", 3),
    ("f25", "p15#1", "user", "lives_in", "Braga, near the Bom Jesus sanctuary", "functional", "llm_inferred", 4),
    ("f21", "p16#1", "user", "gym_days", "Monday and Thursday evenings", "functional", "llm_inferred", 3),
    ("f33", "p17#1", "user", "works_at", "Norte Engenharia", "functional", "llm_inferred", 4),
    ("f34", "p17#1", "user", "job_title", "senior structural engineer", "functional", "llm_inferred", 3),
    ("f22", "p18#1", "user", "dentist", "Dr. Marta Pires, Rua de Santa Catarina", "functional", "llm_inferred", 3),
    ("f23", "p20#1", "mother", "birthday", "11 June", "functional", "llm_inferred", 3),
    ("f24", "p20#1", "mother", "loves", "orchids", "set", "llm_inferred", 2),
    ("f32", "p22#1", "lisbon_hotel", "booking_reference", "ZX4-91Q", "functional", "llm_inferred", 3),
    ("f26", "p23#1", "user", "flight_to_lisbon", "TP1352 on March 20 at 07:15", "functional", "llm_inferred", 3),
    ("f27", "p24#1", "user", "coffee_order", "cortado, no sugar", "functional", "user_explicit", 3),
    ("f28", "p25#1", "pixel", "vet", "Clinica Veterinaria do Lima", "functional", "llm_inferred", 3),
    ("f29", "p25#1", "pixel", "vaccines_due", "October", "functional", "llm_inferred", 3),
    ("f30", "p26#1", "noor", "position", "head of the science department", "functional", "llm_inferred", 3),
    ("f12b", "p27#1", "tagus_footbridge_retrofit", "deadline", "mid-September", "functional", "llm_inferred", 3),
    ("f31", "p28#1", "maya", "swimming", "Piscina Municipal da Rodovia, Saturday mornings", "set", "llm_inferred", 3),
    ("f36", "p29#1", "user", "considering_buying", "Kia EV6", "set", "llm_inferred", 2),
    ("f35", "p30#1", "user", "car_plan", "keep the Octavia for another year", "functional", "llm_inferred", 3),
    # the Arabic thread
    ("g01", "a01#1", "user", "brother", "فهد", "functional", "llm_inferred", 3),
    ("g02", "a01#1", "fahad", "works_at", "بنك الكويت الوطني", "functional", "llm_inferred", 3),
    ("g03", "a02#1", "user", "doctor_appointment", "الأحد الساعة أربعة العصر في مستشفى دار الشفاء", "functional", "llm_inferred", 3),
    ("g04", "a03#1", "user", "coffee_preference", "القهوة العربية بالهيل والزعفران", "set", "llm_inferred", 2),
    ("g05", "a04#1", "user", "trip", "إسطنبول في ديسمبر، فندق قريب من ساحة تقسيم", "set", "llm_inferred", 3),
    ("g06", "a05#1", "user", "hospital_file_number", "55821", "functional", "llm_inferred", 4),
    ("g07", "a06#1", "maya", "hobby", "الرسم، دورة رسم كل يوم أربعاء", "set", "llm_inferred", 2),
    ("g08", "a07#1", "user", "doctor_appointment", "الثلاثاء الساعة عشرة الصبح", "functional", "llm_inferred", 3),
    # near misses: other people's facts
    ("x01", "h01#1", "pedro", "lives_in", "Lisbon", "functional", "llm_inferred", 2),
    ("x02", "h02#1", "rana", "allergic_to", "peanuts", "set", "llm_inferred", 2),
    ("x03", "h03#1", "tiago", "has_dog", "Bolt, a greyhound", "set", "llm_inferred", 2),
    ("x04", "h04#1", "user", "former_manager", "Paulo Mendes", "functional", "llm_inferred", 2),
    ("x06", "h06#1", "ines", "presenting", "Douro viaduct inspection on Friday", "set", "llm_inferred", 2),
    ("x07", "h07#1", "brother_in_law", "flight", "TP1330", "functional", "llm_inferred", 2),
    ("x08", "h08#1", "rui", "also_is", "a luthier", "set", "llm_inferred", 2),
    # notes the user asked to keep, with no turn
    ("n01", "@35", "user", "noted", "The spare house key is with our neighbour Dona Rosa in flat 3B.", "set", "user_explicit", 5),
    ("n02", "@52", "user", "noted", "Maya's paediatrician is Dr. Luís Ferraz at Hospital de Braga.", "set", "user_explicit", 5),
    ("n03", "@64", "user", "noted", "Car insurance renews on 1 February with Fidelidade, policy AU-448120.", "set", "user_explicit", 5),
    ("n04", "@81", "user", "noted", "رقم عداد الكهرباء في بيت براغا 3301-77", "set", "user_explicit", 5),
    # hub facts: always there, almost never what a question is about
    ("z01", "@1", "user", "uses_assistant", "Kazma", "set", "system_tool", 1),
    ("z02", "@1", "kazma", "verified_tool", "file_read", "set", "llm_inferred", 3),
    ("z03", "@1", "kazma", "verified_tool", "file_search", "set", "llm_inferred", 3),
    ("z04", "@6", "user", "editor", "VS Code with the dark theme", "functional", "llm_inferred", 2),
    ("z05", "@11", "user", "operates_account", "sami_builds on X", "set", "llm_inferred", 3),
    ("z06", "@17", "user", "timezone", "Europe/Lisbon", "functional", "system_tool", 2),
    ("z07", "@23", "user", "noted", "Naming rule for the bakery side project: never propose names ending in -ify.", "set", "user_explicit", 5),
    ("z08", "@29", "user", "noted", "Weekly review every Friday at 16:00: close open tasks, plan next week.", "set", "user_explicit", 5),
    ("z09", "@36", "user", "interested_in", "history of bridge design", "set", "llm_inferred", 2),
    ("z10", "@44", "user", "prefers", "short answers with bullet points", "functional", "user_explicit", 4),
    ("z11", "@57", "user", "noted", "stress-run marker: the check word is PELICAN-7734", "set", "user_explicit", 5),
    ("z12", "@73", "user", "related_to", "aurelia_bridges", "set", "system_tool", 1),
]

# ── Unrelated chat, including the traps ─────────────────────────────────────
_TRAPS = [
    "Tell me about the layout of a typical Roman villa.",
    "Is it possible to cook rice without a lid?",
    "What happened throughout the Renaissance in Florence?",
    "Why do fans shout during football matches?",
    "I have my doubts about this marketing plan.",
    "Write about something you find fascinating about octopuses.",
    "What is the best way to do it and how is it done in the end?",
    "Is it the case that it is what it is?",
    "How do you make it so that it does not do that again?",
    "What does it mean when they say it runs out of steam?",
    "Can you run the numbers on this and see if it works out?",
    "Is there a document template for meeting minutes?",
    "What travel adapters work in the United Kingdom?",
]
_TOPICS: dict[str, list[tuple[str, str]]] = {
    "code": [
        ("How do I reverse a list in Python?", "Use my_list[::-1] for a copy or my_list.reverse() in place."),
        ("Why does my React component render twice?", "In development StrictMode renders twice on purpose to surface side effects."),
        ("Explain async and await in JavaScript.", "async marks a function that returns a promise; await pauses until the promise settles."),
        ("What's the difference between a process and a thread?", "Processes have separate memory; threads share memory within one process."),
        ("How do I squash the last three git commits?", "Run git rebase -i HEAD~3 and mark two of them as squash."),
        ("What does HTTP status 429 mean?", "Too Many Requests: you are being rate limited."),
        ("How do I read a CSV file with pandas?", "pandas.read_csv('file.csv') returns a DataFrame."),
        ("Explain big O notation briefly.", "It describes how running time grows with input size, ignoring constants."),
        ("How can I center a div with CSS?", "Use display:flex with justify-content:center and align-items:center on the parent."),
        ("What is a race condition?", "Two operations on shared state whose result depends on timing."),
        ("How do I write a SQL join between orders and customers?", "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id."),
        ("What is dependency injection?", "Passing a component its collaborators instead of letting it build them."),
    ],
    "food": [
        ("Give me a recipe for mushroom risotto.", "Toast arborio rice, add stock ladle by ladle, finish with parmesan and butter."),
        ("How long should I boil an egg for a runny yolk?", "About six minutes from boiling water."),
        ("What spices go into garam masala?", "Cumin, coriander, cardamom, cloves, cinnamon and black pepper."),
        ("How do I make sourdough starter?", "Mix flour and water, feed daily, and wait about a week for it to bubble."),
        ("What's a quick vegetarian dinner idea?", "Chickpea curry with spinach over rice, ready in twenty minutes."),
        ("How do I keep avocados from browning?", "Lemon juice and tight wrap slow the oxidation."),
        ("Best way to reheat pizza?", "In a hot pan with a lid for a few minutes."),
        ("How do I make a French omelette?", "Low heat, constant stirring, roll it before it browns."),
    ],
    "travel": [
        ("My colleague is visiting Tokyo, what should she see?", "Asakusa, the Meiji shrine, Shibuya crossing and a day trip to Nikko."),
        ("What's the best time of year to visit Iceland?", "June to August for hiking, winter for the northern lights."),
        ("How do I get from Rome airport to the city centre?", "The Leonardo Express train reaches Termini in 32 minutes."),
        ("Is Prague expensive for tourists?", "Cheaper than western Europe, especially outside the old town."),
        ("What should I pack for a ski trip?", "Base layers, a waterproof jacket, gloves, goggles and sunscreen."),
        ("Recommend a weekend itinerary for Seville.", "Alcazar, the cathedral, Triana and a flamenco show at night."),
    ],
    "general": [
        ("Why is the sky blue?", "Air scatters short blue wavelengths more than red ones."),
        ("How do vaccines work?", "They train the immune system to recognise a pathogen without the disease."),
        ("Who painted the Girl with a Pearl Earring?", "Johannes Vermeer, around 1665."),
        ("How far is the Moon from Earth?", "About 384,400 kilometres on average."),
        ("What causes the seasons?", "The tilt of Earth's axis, not its distance from the Sun."),
        ("How does compound interest work?", "Interest earns interest, so growth accelerates over time."),
        ("What is the capital of Australia?", "Canberra."),
        ("Summarise the plot of Hamlet in two lines.", "A prince avenges his father's murder and nearly everyone dies."),
        ("What is photosynthesis?", "Plants turn light, water and CO2 into sugar and oxygen."),
        ("Why do cats purr?", "Contentment mostly, but also to soothe themselves when stressed."),
    ],
    "writing": [
        ("Make this email more polite: send me the report now.", "Could you please send me the report when you have a moment?"),
        ("Give me a title for a blog post about remote work.", "Home Is Where the Standup Is."),
        ("Write a haiku about rain.", "Soft rain on tin roofs, / the street forgets its footsteps, / puddles hold the sky."),
        ("Proofread: their going to the park tomorow.", "They're going to the park tomorrow."),
        ("Suggest a slogan for a bakery.", "Rise and shine, fresh every morning."),
    ],
    "tools": [
        ("run the unit tests", "Done. 42 passed, 0 failed."),
        ("check the disk space", "C: has 118 GB free of 476 GB."),
        ("list the files in the downloads folder", "12 files, the newest is invoice_0917.pdf."),
        ("ping the staging server", "staging responded in 23 ms."),
        ("show the git status", "On branch main, working tree clean."),
    ],
    "filler": [
        ("hi", "Hello! How can I help?"),
        ("thanks!", "You're welcome!"),
        ("ok", "Great."),
        ("good morning", "Good morning! What's on today?"),
        ("perfect, thank you", "Glad it helped."),
    ],
}
_AR_DISTRACTORS: list[tuple[str, str]] = [
    ("شلون أسوي كيكة بالشوكولاتة؟", "اخلط الطحين والكاكاو والبيض والسكر واخبزها على ١٨٠ درجة."),
    ("وش أفضل وقت لزيارة الأندلس؟", "الربيع أحلى وقت، الجو معتدل."),
    ("اشرح لي الذكاء الاصطناعي باختصار.", "هو أنظمة تتعلم من البيانات وتسوي مهام تحتاج ذكاء."),
    ("كم المسافة بين الكويت والرياض؟", "تقريبا ستمية كيلو بالسيارة."),
    ("اكتب لي رسالة شكر لمديري.", "شكرا على دعمك المستمر وثقتك."),
    ("وش معنى كلمة شغف؟", "الشغف هو الحب الشديد للشي والتعلق فيه."),
    ("كيف أحسن نومي؟", "نام بوقت ثابت وخفف الشاشات قبل النوم."),
    ("عطني فكرة مشروع صغير.", "مشروع توصيل وجبات صحية في الحي."),
    ("شلون أتعلم البرمجة؟", "ابدأ ببايثون وسو مشاريع صغيرة كل أسبوع."),
    ("وش أحسن طريقة لحفظ القرآن؟", "التكرار اليومي والمراجعة المنتظمة."),
]


def _distractor_sessions(rng: random.Random) -> list[tuple[str, int, list[tuple[str, str]]]]:
    out: list[tuple[str, int, list[tuple[str, str]]]] = []
    pool = [pair for pairs in _TOPICS.values() for pair in pairs]
    n = 0
    for s in range(300):
        day = rng.randint(0, 115)
        turns = []
        for _ in range(rng.randint(2, 5)):
            roll = rng.random()
            if roll < 0.12:
                q = rng.choice(_TRAPS)
                a = "Here is what I can tell you: " + rng.choice(pool)[1]
            elif roll < 0.2:
                q, a = rng.choice(_AR_DISTRACTORS)
            else:
                q, a = rng.choice(pool)
            turns.append((q, a))
        n += 1
        out.append((f"d{n:03d}", day, turns))
    return out


# ── Questions: (id, text, category, gold, refs that must rank below the answer) ──
# A ref is a turn, "<session>#<turn index>" (1-based), or a fact id. Each gold
# entry is one piece of the answer; "a|b" means either memory gives it -- the
# turn, or the fact extracted from it. No gold: nothing should be injected.
QUESTIONS: list[tuple[str, str, str, list[str], list[str]]] = [
    # single fact, the user's own words, with shared keywords
    ("q001", "What is my dog's name?", "single", ["p01#2|f05"], ["x03"]),
    ("q002", "Which company did I first work for in Porto?", "single", ["p01#1"], []),
    ("q003", "What am I allergic to?", "single", ["p02#1|f06"], ["x02"]),
    ("q004", "What does my partner Noor teach?", "single", ["p02#2|f08"], []),
    ("q005", "When does my passport expire?", "single", ["p05#1|f10"], ["h05#1"]),
    ("q006", "Which project am I leading?", "single", ["p06#1|f11"], []),
    ("q007", "Who is my manager on the Tagus project?", "single", ["p06#2|f13"], ["h04#1", "x04"]),
    ("q008", "What school does Maya go to?", "single", ["p07#1|f14"], []),
    ("q009", "What car do I drive?", "single", ["p09#1|f17"], []),
    ("q010", "What's my blood type?", "single", ["p10#1|f18"], []),
    ("q011", "Who teaches me guitar and when?", "single", ["p12#1|f19"], ["h08#1", "x08"]),
    ("q012", "Who is my dentist?", "single", ["p18#1|f22"], []),
    ("q013", "When is my mother's birthday?", "single", ["p20#1|f23"], []),
    ("q014", "How do I take my coffee?", "single", ["p24#1|f27"], []),
    ("q015", "When are Pixel's vaccines due?", "single", ["p25#1|f29"], []),
    ("q016", "Where does Maya go swimming?", "single", ["p28#1|f31"], []),
    ("q017", "What's Noor's position at school now?", "single", ["p26#1|f30"], []),
    ("q018", "Which vet does Pixel see?", "single", ["p25#1|f28"], []),
    # paraphrase: no content word in common with the memory
    ("q101", "When does my travel document run out?", "paraphrase", ["p05#1|f10"], []),
    ("q102", "Is there any seafood I must avoid eating?", "paraphrase", ["p02#1|f06"], []),
    ("q103", "What kind of pet do I have at home?", "paraphrase", ["p01#2|f05|f37"], []),
    ("q104", "How often do I exercise these days?", "paraphrase", ["p16#1|f21"], ["p13#1"]),
    ("q105", "What instrument am I learning?", "paraphrase", ["p12#1|f19"], []),
    ("q106", "Where is our new home?", "paraphrase", ["p15#1|f25"], ["p01#1"]),
    ("q107", "Who looks after my teeth?", "paraphrase", ["p18#1|f22"], []),
    ("q108", "What present should I get my mum?", "paraphrase", ["p20#1|f24"], []),
    # only the assistant said it
    ("q201", "Which restaurant did you recommend for Noor's birthday?", "assistant", ["p04#1"], []),
    ("q202", "What novel did you suggest for my book club?", "assistant", ["p19#1"], []),
    ("q203", "How much is my monthly mortgage payment?", "assistant", ["p11#1"], []),
    ("q204", "What did you tell me to change to fix my laptop battery?", "assistant", ["p21#1"], []),
    ("q205", "How long was the long run in the half marathon plan you gave me?", "assistant", ["p03#1"], []),
    ("q206", "What time should I be at the airport for the Lisbon flight?", "assistant", ["p23#1"], []),
    # two memories needed
    ("q301", "What school does Maya attend and what time does it start?", "multi", ["p07#1|f14", "p08#1|f16"], []),
    ("q302", "What's my job title and which company is it at now?", "update", ["p17#1|f33", "p17#1|f34"], ["p01#1"]),
    ("q303", "When and where is the half marathon, and when is our flight there?", "multi", ["p03#1|f09", "p23#1|f26"], []),
    ("q304", "What's the Lisbon hotel booking and the flight number?", "multi", ["p22#1|f32", "p23#1|f26"], ["h07#1", "x07"]),
    # the newest value must come first
    ("q401", "Where do I live?", "update", ["p15#1|f25"], ["p01#1", "x01"]),
    ("q402", "What days do I go to the gym?", "update", ["p16#1|f21"], ["p13#1"]),
    ("q403", "Where do I work?", "update", ["p17#1|f33"], ["p01#1"]),
    ("q404", "When is the Tagus retrofit deadline?", "update", ["p27#1|f12b"], ["p06#1"]),
    ("q405", "Did we decide to sell the Octavia?", "update", ["p30#1|f35"], ["p29#1", "f36"]),
    ("q406", "متى موعد الدكتور؟", "update", ["a07#1|g08"], ["a02#1"]),
    # exact codes and numbers
    ("q501", "What's the hotel booking reference ZX4-91Q for?", "keyword", ["p22#1|f32"], []),
    ("q502", "Which flight is TP1352?", "keyword", ["p23#1|f26"], ["h07#1", "x07"]),
    ("q503", "What is my hospital file number?", "keyword", ["a05#1|g06"], []),
    ("q504", "رقم ملفي في المستشفى كم؟", "keyword", ["a05#1|g06"], []),
    # questions heavy with stopwords but answerable
    ("q601", "what is it that I do for a living?", "noise", ["p17#1|f33|f34"], []),
    ("q602", "is it the case that I have a dog or not?", "noise", ["p01#2|f05"], []),
    ("q603", "how is it that I get to the school on time in the morning?", "noise", ["p08#1|f16"], []),
    # Arabic
    ("q701", "شنو اسم أخوي؟", "arabic", ["a01#1|g01"], []),
    ("q702", "وين يشتغل أخوي فهد؟", "arabic", ["a01#1|g02"], []),
    # The user stated two coffee preferences (p24 in English, a03 in Arabic);
    # the question does not choose between them.
    ("q703", "شلون أحب قهوتي؟", "arabic", ["a03#1|g04|p24#1|f27"], []),
    ("q704", "متى رحلتنا لإسطنبول؟", "arabic", ["a04#1|g05"], []),
    ("q705", "وين حاجزين الفندق في اسطنبول؟", "arabic", ["a04#1|g05"], []),
    ("q706", "وش تحب مايا تسوي؟", "arabic", ["a06#1|g07"], []),
    ("q707", "أي رواية نصحتني فيها؟", "arabic", ["a08#1"], []),
    ("q708", "القهوه اللي احبها وش فيها؟", "arabic", ["a03#1|g04"], []),
    # nothing answers these: nothing should be injected
    ("q801", "What is my brother-in-law's name?", "abstain", [], []),
    ("q802", "Which bank do I use?", "abstain", [], []),
    ("q803", "What's my favourite football team?", "abstain", [], []),
    ("q804", "Do I have any cats?", "abstain", [], []),
    ("q805", "What is my home Wi-Fi network called?", "abstain", [], []),
    ("q806", "Which university did I study at?", "abstain", [], []),
    ("q807", "What is Noor's favourite film?", "abstain", [], []),
    ("q808", "How tall am I?", "abstain", [], []),
    ("q809", "What's my shoe size?", "abstain", [], []),
    ("q810", "When is my wedding anniversary?", "abstain", [], []),
    ("q811", "What did my doctor say about my cholesterol?", "abstain", [], []),
    ("q812", "Which gym do I go to?", "abstain", [], []),
    ("q813", "What is my father's job?", "abstain", [], []),
    ("q814", "شنو اسم أختي؟", "abstain", [], []),
    ("q815", "وين درست الجامعة؟", "abstain", [], []),
    # only a note the user asked to keep holds the answer: no turn does
    ("q901", "Who has our spare house key?", "fact", ["n01"], []),
    ("q902", "Who is Maya's paediatrician?", "fact", ["n02"], []),
    ("q903", "When does the car insurance renew?", "fact", ["n03"], []),
    ("q904", "كم رقم عداد الكهرباء في البيت؟", "fact", ["n04"], []),
]


def build() -> dict[str, Any]:
    rng = random.Random(SEED)
    sessions = [
        {"id": sid, "day": day, "kind": kind, "turns": [{"user": u, "assistant": a} for u, a in turns]}
        for kind, group in (("persona", PERSONA), ("hard_negative", HARD_NEGATIVES),
                            ("distractor", _distractor_sessions(rng)))
        for sid, day, turns in group
    ]
    refs = {f"{s['id']}#{i}" for s in sessions for i in range(1, len(s["turns"]) + 1)}
    days = {s["id"]: s["day"] for s in sessions}
    facts = []
    for fid, source, subject, predicate, obj, ptype, method, importance in FACTS:
        if source.startswith("@"):
            day, session, turn = int(source[1:]), None, None
        elif source in refs:
            session, turn_s = source.split("#")
            day, turn = days[session], int(turn_s)
        else:
            raise ValueError(f"{fid}: unknown source turn {source}")
        facts.append({
            "id": fid, "day": day, "session": session, "turn": turn, "subject": subject,
            "predicate": predicate, "object": obj, "predicate_type": ptype,
            "extraction_method": method, "importance": importance,
        })
    # Seeded in the order the facts were stated: a later functional fact
    # supersedes an earlier one only when it comes after it.
    facts.sort(key=lambda f: (f["day"], f["turn"] or 0))
    fact_ids = {f["id"] for f in facts}
    if len(fact_ids) != len(facts):
        raise ValueError("duplicate fact id")
    questions = []
    for qid, text, cat, gold, below in QUESTIONS:
        for ref in [a for piece in gold for a in piece.split("|")] + below:
            if ref not in refs and ref not in fact_ids:
                raise ValueError(f"{qid}: unknown memory {ref}")
        questions.append({"id": qid, "text": text, "category": cat, "gold": gold, "below": below})
    return {"version": 2, "seed": SEED, "sessions": sessions, "facts": facts,
            "questions": questions}


def _cmd_build() -> int:
    from kazma_core.memory.benchmark import load_dataset

    data = build()
    before = load_dataset() if DATASET.is_file() else None
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    turns = sum(len(s["turns"]) for s in data["sessions"])
    print(f"{DATASET}: {len(data['sessions'])} sessions, {turns} turns, "
          f"{len(data['questions'])} questions")
    if before != json.loads(json.dumps(data, ensure_ascii=False)):
        # The recorded vectors answer exactly the old texts: CI cannot replay
        # a text it was never given.
        lock = "lock" if (before or {}).get("version") == data["version"] else "lock --rebaseline"
        print("the dataset changed: re-record the vectors (python scripts/memory_bench.py "
              f"vectors), then lock the scores (python scripts/memory_bench.py {lock})")
    return 0


def _patched(embedder: Any, model: str):
    return (
        mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder),
        mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: model),
    )


def _cmd_vectors() -> int:
    from kazma_core.memory.benchmark import RecordingEmbedder, run_benchmark
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    real = get_embedder()
    if real is None:
        print("no embedder is configured: install the rag extra (bge-m3)", file=sys.stderr)
        return 2
    model = get_embedding_model_name()
    recorder = RecordingEmbedder(real)
    a, b = _patched(recorder, model)
    with a, b:
        report = run_benchmark()
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    n = recorder.save(VECTORS, model=model)
    print(f"{VECTORS}: {n} vectors from {model}")
    print(json.dumps(report["overall"], indent=1))
    return 0


def _cmd_run(real: bool, as_json: bool, details: bool) -> int:
    from kazma_core.memory.benchmark import ReplayEmbedder, run_benchmark

    if real:
        report = run_benchmark(keep_results=details)
    else:
        replay = ReplayEmbedder(VECTORS)
        a, b = _patched(replay, replay.model)
        with a, b:
            report = run_benchmark(keep_results=details)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print(f"{report['memories']} memories, {report['overall']['questions']} questions")
    for name, row in report["categories"].items():
        extra = "  ".join(f"{k} {v}" for k, v in row.items() if k not in ("n",))
        print(f"  {name:<11} n={row['n']:<3} {extra}")
    print("overall:", json.dumps(report["overall"]))
    if details:
        for r in report.get("results", []):
            if not r["hit"]:
                print("  MISS", r)
    return 0


def _cmd_lock(rebaseline: bool) -> int:
    """Write the replayed scores as the ratchet; refuse to lower any of them.

    The ratchet names the dataset version it was measured on. A new dataset
    is a new instrument: its first scores are written with ``--rebaseline``,
    which is refused while the version is unchanged -- so it can never be
    used to lower the gate on the same data.
    """
    from kazma_core.memory.benchmark import (
        ReplayEmbedder,
        gated_scores,
        load_dataset,
        run_benchmark,
    )

    version = load_dataset()["version"]
    replay = ReplayEmbedder(VECTORS)
    a, b = _patched(replay, replay.model)
    with a, b:
        scores = gated_scores(run_benchmark())
    old = json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.is_file() else {}
    old_scores = old.get("scores", {})
    if rebaseline:
        if old.get("dataset_version") == version:
            print(f"the ratchet already measures dataset v{version}: nothing to rebaseline",
                  file=sys.stderr)
            return 1
        old_scores = {}
    elif old.get("dataset_version") != version:
        print(f"the ratchet measures dataset v{old.get('dataset_version')}, the dataset is "
              f"v{version}: record its vectors, then lock --rebaseline", file=sys.stderr)
        return 1
    lower = {k: (v, scores.get(k)) for k, v in old_scores.items() if scores.get(k, -1.0) < v}
    if lower:
        for k, (was, now) in sorted(lower.items()):
            print(f"  {k}: {now} < {was}", file=sys.stderr)
        print("refusing to lower the ratchet: fix the regression", file=sys.stderr)
        return 1
    THRESHOLDS.write_text(
        json.dumps({"dataset_version": version, "scores": scores}, indent=1) + "\n",
        encoding="utf-8",
    )
    raised = sorted(k for k, v in scores.items() if v > old_scores.get(k, -1.0))
    print(f"{THRESHOLDS}: dataset v{version}, {len(raised)} raised")
    for k in raised:
        print(f"  {k}: {old_scores.get(k)} -> {scores[k]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("vectors")
    lock = sub.add_parser("lock")
    lock.add_argument("--rebaseline", action="store_true",
                      help="a new dataset version: its first scores become the ratchet")
    run = sub.add_parser("run")
    run.add_argument("--real", action="store_true", help="load the real embedder")
    run.add_argument("--json", action="store_true")
    run.add_argument("--details", action="store_true", help="list every miss")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return _cmd_build()
    if args.cmd == "vectors":
        return _cmd_vectors()
    if args.cmd == "lock":
        return _cmd_lock(args.rebaseline)
    return _cmd_run(args.real, args.json, args.details)


if __name__ == "__main__":
    raise SystemExit(main())
