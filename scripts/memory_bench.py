"""Memory retrieval benchmark (Stage 2, R7): build it, record it, run it.

  python scripts/memory_bench.py build             # the dataset JSON (deterministic)
  python scripts/memory_bench.py vectors           # record bge-m3's vectors for CI
  python scripts/memory_bench.py run [--real]      # score recall(); --real loads the model
  python scripts/memory_bench.py lock              # raise the CI ratchet to today's scores
  python scripts/memory_bench.py lock --trade "why"  # a reviewed trade (see TRADE_RULE)
  python scripts/memory_bench.py lock --new-recording "why"  # lock a new recording of the model's pass
  python scripts/memory_bench.py lock --prompt-changed "why"  # a prompt change waiting for its recording
  python scripts/memory_bench.py lock --edge q601 "why"  # name a hit that flips inside the rounding (EDGE_RULE)
  python scripts/memory_bench.py lock-pgvector --from LOG --run "CI run"  # the pgvector floors, from a named run
  python scripts/memory_bench.py sealed --look "why"    # score the sealed set: every look is logged
  python scripts/memory_bench.py extraction           # extraction scores from the recorded replies
                                                    # (recorded by scripts/memory_extraction_record.py)
  python scripts/memory_bench.py writes               # the write scenarios: what passes, what does not

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
import os
import random
import sys
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "kazma-core" / "kazma_core" / "memory" / "benchmark_data" / "dataset.json"
VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
THRESHOLDS = REPO / "tests" / "fixtures" / "memory_bench" / "thresholds.json"
EXTRACTION = DATASET.with_name("extraction.json")
WRITES = DATASET.with_name("writes.json")
LLM_REPLIES = REPO / "tests" / "fixtures" / "memory_bench" / "extraction_llm.json"
#: The speakers case set's vectors (Spaces S1-11; recorded by the R3 study).
SPEAKER_VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "speakers_vectors.npz"
#: The sealed set (benchmark audit R8, docs/plans/MEMORY_SEALED_SET.md): written
#: by another session, scored only by ``sealed --look "why"``.
SEALED = REPO / "tests" / "fixtures" / "memory_bench" / "sealed_questions.json"
SEALED_VECTORS = REPO / "tests" / "fixtures" / "memory_bench" / "sealed_vectors.npz"

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
    # A topic returned to every week (v4): the half marathon training.
    ("r00", 7, [("First week of the plan: three easy 5 km runs, and the Sunday long run was 8 km.",
                 "Right on plan. Keep the easy runs truly easy, conversation pace.")]),
    ("r01", 14, [("Training check-in: this week's long run was 10 km, easy pace, knees felt fine.",
                  "Good start. Keep the long runs conversational and add 1-2 km a week.")]),
    ("r02", 21, [("Long run 12 km on Sunday, but my left calf was tight afterwards.",
                  "Stretch and foam-roll the calf; if it is still tight by Wednesday, swap one easy run for cycling.")]),
    ("r03", 28, [("Calf is fine now. Did 13 km at 5:40 per km.",
                  "Nice, 5:40 is a good steady pace for your goal.")]),
    ("r04", 35, [("Skipped the long run this week, work on the retrofit was crazy.",
                  "One missed week is fine; don't try to make up the distance, just pick up the plan.")]),
    ("r05", 42, [("Back on track: 14 km long run, and I added one interval session.",
                  "Great. Keep the intervals to once a week so the long run stays fresh.")]),
    ("r06", 49, [("16 km long run done, felt strong at the end.",
                  "That's the sign the base is there. Two more build weeks, then the taper.")]),
    ("r07", 56, [("Long run 18 km, my longest ever! Averaged 5:35 per km.",
                  "Excellent, 18 km means race day is well within reach. Taper starts next week.")]),
    ("r08", 63, [("Taper week: only 10 km on Sunday and legs feel fresh. Race goal is under 2 hours.",
                  "A sub-2-hour half is realistic from your long runs. Rest well and trust the taper.")]),
    ("r09", 70, [("Last long run before the race: 8 km easy, and I booked a sports massage for Thursday.",
                  "Perfect taper, and a massage two days before the race is well timed.")]),
    ("r10", 77, [("Race result: I finished the Lisbon half marathon in 1:56:40!",
                  "Congratulations, a sub-2-hour half marathon on your first attempt!")]),
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

# ── Weekly topic summaries (dataset v3, plan C2b) ───────────────────────────
# (summary id, day the week starts, title, text, the turns it was written
# from). Written the way the live model writes them (2026-09-27): what was
# said, decided and left open, nothing else. They answer the "overview"
# questions. The unrelated chat is summarized too, week by week and topic by
# topic (``_distractor_summaries``): about a hundred summaries, the pool a
# real install holds over these four months -- a question's background
# among ten summaries sat far below the live one among 67.
SUMMARIES: list[tuple[str, int, str, str, list[str]]] = [
    ("sw1", 0, "Getting to know Sami: home, work, family, running",
     "Sami introduced himself: he lives in Porto and works as a structural engineer at Aurelia "
     "Bridges. He has a lazy four-year-old greyhound called Pixel, is allergic to shellfish, and "
     "his partner Noor teaches chemistry at a secondary school. Kazma drew up a 12-week plan for "
     "the Lisbon half marathon on March 22 (three easy runs a week, a Sunday long run growing from "
     "8 to 18 km, a two-week taper) and suggested Cantinho do Avillez for Noor's birthday dinner: "
     "seafood for her, a meat menu for him. Left open: booking the dinner.",
     ["p01#1", "p01#2", "p02#1", "p02#2", "p03#1", "p04#1"]),
    ("sw2", 7, "Passport renewal, the Tagus retrofit, Maya's new school",
     "Sami's passport expires on 14 March 2031; Kazma will remind him in September 2030 to renew "
     "it. At work he now leads the Tagus footbridge retrofit, due at the end of June, under his "
     "manager Ines Carvalho, who likes detailed status reports; Kazma offered a milestone plan. "
     "His daughter Maya, seven, started at Escola Azul: school begins at 8:40, so they leave home "
     "by 8:10 and a 7:30 alarm leaves time for breakfast.",
     ["p05#1", "p06#1", "p06#2", "p07#1", "p08#1"]),
    ("sw4", 70, "Lisbon trip bookings and a laptop battery fix",
     "For the Lisbon half marathon trip the hotel is booked (reference ZX4-91Q) and the flight is "
     "TP1352 on March 20 at 07:15; Kazma advised being at the airport by 05:45. Separately, "
     "Sami's ThinkPad X1 Carbon battery was draining fast: Kazma suggested an 80 percent charge "
     "threshold in Lenovo Vantage and the Better Battery power mode.",
     ["p21#1", "p22#1", "p23#1"]),
    ("sw5", 105, "Keep the Octavia or buy an electric car",
     "Sami considered selling the 2019 Skoda Octavia estate and buying an electric car, probably "
     "a Kia EV6; Kazma noted its good range and said to check home charging first. A few days "
     "later he decided to keep the Octavia for another year.",
     ["p29#1", "p30#1"]),
    # The same topic every week (v4): eight summaries of it, as a real install
    # holds for anything the user returns to -- the case where a question's
    # background among the summaries used to be the topic itself.
    ("sr0", 7, "Half marathon training: week one",
     "Week one of the 12-week plan for the Lisbon half marathon: three easy 5 km runs and an 8 km "
     "Sunday long run. Kazma: keep the easy runs truly easy.",
     ["r00#1"]),
    ("sr1", 14, "Half marathon training: first check-in",
     "Sami's long run this week was 10 km at an easy pace, knees fine. Kazma advised keeping long "
     "runs conversational and adding 1-2 km a week toward the Lisbon half marathon.",
     ["r01#1"]),
    ("sr2", 21, "Half marathon training: 12 km and a tight calf",
     "Sami ran 12 km on Sunday for the Lisbon half marathon, and his left calf was tight "
     "afterwards. Kazma suggested stretching and foam-rolling, and cycling instead of one easy run "
     "if it stayed tight. Left open: whether the calf recovers.",
     ["r02#1"]),
    ("sr3", 28, "Half marathon training: 13 km, calf recovered",
     "The calf recovered. Sami ran 13 km at 5:40 per km, a good steady pace for his half "
     "marathon goal.",
     ["r03#1"]),
    ("sr4", 35, "Half marathon training: a missed long run",
     "Sami skipped this week's long run because of work on the Tagus retrofit. Kazma said one "
     "missed week is fine: pick the plan up again without making up the distance.",
     ["r04#1"]),
    ("sr5", 42, "Half marathon training: 14 km and intervals",
     "Back on track with a 14 km long run and one interval session added. Kazma: keep intervals "
     "to once a week so the long run stays fresh.",
     ["r05#1"]),
    ("sr6", 49, "Half marathon training: 16 km, felt strong",
     "Sami ran 16 km and felt strong at the end; two more build weeks, then the taper before the "
     "Lisbon half marathon.",
     ["r06#1"]),
    ("sr7", 56, "Half marathon training: longest run, 18 km",
     "Sami's longest run ever: 18 km at an average 5:35 per km. Kazma said race day is well within "
     "reach; the taper starts next week.",
     ["r07#1"]),
    ("sr8", 63, "Half marathon training: taper week",
     "Taper week: only 10 km on Sunday and fresh legs. Sami's goal for the Lisbon half marathon is "
     "under two hours, which Kazma judged realistic from his long runs.",
     ["r08#1"]),
    ("sr9", 70, "Half marathon training: last long run and a massage",
     "The last long run before the Lisbon half marathon: 8 km easy. Sami booked a sports massage "
     "for Thursday, which Kazma judged well timed.",
     ["r09#1"]),
    ("sr10", 77, "Lisbon half marathon: finished in 1:56:40",
     "Sami finished the Lisbon half marathon in 1:56:40, under his two-hour goal, on his first "
     "attempt.",
     ["r10#1"]),
    ("sw6", 21, "رحلة إسطنبول في ديسمبر",
     "رحلة العائلة إلى إسطنبول في ديسمبر، وحجزوا فندق قريب من ساحة تقسيم. نصح كاظمة بأخذ "
     "جاكيتات لأن الجو في ديسمبر بارد شوي.",
     ["a04#1"]),
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


_SUMMARY_TITLES = {
    "code": "Programming questions",
    "food": "Cooking questions",
    "travel": "Travel questions",
    "general": "General knowledge questions",
    "writing": "Writing help",
    "tools": "Tool runs: tests, disk, files, servers",
    "traps": "Miscellaneous questions",
    "arabic": "أسئلة متفرقة",
}


def _distractor_summaries(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A weekly summary per topic of the unrelated chat: the week's turns on
    it (four or more, the product's minimum), up to six questions with their
    answers, as the model lists a week of assorted questions."""
    topic_of = {q: name for name, pairs in _TOPICS.items() for q, _ in pairs}
    topic_of.update({q: "traps" for q in _TRAPS})
    topic_of.update({q: "arabic" for q, _ in _AR_DISTRACTORS})
    weeks: dict[tuple[int, str], list[tuple[str, str, str]]] = {}
    for s in sessions:
        if s["kind"] != "distractor":
            continue
        for i, t in enumerate(s["turns"], 1):
            topic = topic_of.get(t["user"], "")
            if topic in _SUMMARY_TITLES:
                weeks.setdefault((s["day"] // 7, topic), []).append((t["user"], t["assistant"], f"{s['id']}#{i}"))
    out: list[dict[str, Any]] = []
    for (week, topic), turns in sorted(weeks.items()):
        if len(turns) < 4:
            continue
        shown: dict[str, str] = {}
        for q, a, _ref in turns:
            shown.setdefault(q, a)
        pairs = list(shown.items())[:6]
        if topic == "arabic":
            text = "سأل المستخدم: " + "؛ ".join(f"{q} ({a})" for q, a in pairs)
        else:
            text = "The user asked: " + "; ".join(f"{q.rstrip('?.')} ({a.rstrip('.')})" for q, a in pairs) + "."
        out.append({"id": f"dw{week:02d}{topic}", "day": week * 7, "title": _SUMMARY_TITLES[topic],
                    "text": text, "sources": [ref for _q, _a, ref in turns]})
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
    # asked for an overview of something never discussed (v3: summaries must stay out)
    ("q816", "Catch me up on the kitchen renovation.", "abstain", [], []),
    ("q817", "What did we decide about the solar panels?", "abstain", [], []),
    ("q818", "Where are we with the garden redesign?", "abstain", [], []),
    # only a note the user asked to keep holds the answer: no turn does
    ("q901", "Who has our spare house key?", "fact", ["n01"], []),
    ("q902", "Who is Maya's paediatrician?", "fact", ["n02"], []),
    ("q903", "When does the car insurance renew?", "fact", ["n03"], []),
    ("q904", "كم رقم عداد الكهرباء في البيت؟", "fact", ["n04"], []),
    # an overview of a topic: a weekly summary gives it in one memory, the
    # turns and facts in several (v3). "sw4|p22#1|f32": the summary, the turn
    # or the fact holds that piece.
    ("q951", "What's booked for our Lisbon trip?", "overview", ["sw4|p22#1|f32", "sw4|p23#1|f26"], []),
    ("q952", "Catch me up on the Tagus retrofit project.", "overview", ["sw2|p06#1|f11", "p27#1|f12b"], []),
    ("q953", "What did we decide about the car in the end?", "overview", ["sw5|p30#1|f35"], ["p29#1", "f36"]),
    ("q954", "Remind me what you know about Maya's school.", "overview", ["sw2|p07#1|f14", "sw2|p08#1|f16"], []),
    ("q955", "What did I tell you about myself when we first talked?", "overview",
     ["sw1|p01#1|f01", "sw1|p01#2|f05"], []),
    ("q956", "شنو خطتنا لرحلة إسطنبول؟", "overview", ["sw6|a04#1|g05"], []),
    # a topic with a summary every week (v4): its own summaries must not hide it
    ("q957", "How is my half marathon training going?", "overview",
     ["sr10|sr9|sr8|r10#1|r09#1|r08#1"], []),
    ("q960", "How did the half marathon race go in the end?", "overview", ["sr10|r10#1"], []),
    ("q958", "What long runs have I done for the half marathon so far?", "overview",
     ["sr7|r07#1|sr6|r06#1|sr5|r05#1"], []),
    ("q959", "What happened with my calf during training?", "overview", ["sr2|sr3|r02#1|r03#1"], []),
]


#: The second set (Memory audit M24; renamed 2026-11-20, benchmark audit R8
#: and the owner's decision 4). Recall's thresholds were swept on QUESTIONS;
#: these 40 were written after the last sweep, against the same memories, and
#: no threshold is swept on them. They are a second check, not an unseen
#: test: two decisions were taken by looking at them (2026-10-14 and
#: 2026-10-15, the guide's "Benchmark report"), and they are gated in the
#: same lock, so every change kept is one that did not lower them. They stay
#: gated; a held-out set is a sealed one, written by another session and
#: scored only on request. ``mixed`` asks in one language about a memory in the other, or
#: switches language inside the question. Several abstain questions come close
#: to a real memory (a dentist, a car), which is where injecting nothing is
#: hard.
SECOND_SET: list[tuple[str, str, str, list[str], list[str]]] = [
    ("h001", "What's my partner's name?", "single", ["p02#2|f07"], []),
    ("h002", "Which breed is Pixel?", "single", ["p01#2|f37"], ["x03"]),
    ("h003", "What's the name of my guitar teacher?", "single", ["p12#1|f19|h08#1"], []),
    ("h004", "Who is my boss on the footbridge job?", "paraphrase", ["p06#2|f13"], ["h04#1", "x04"]),
    ("h005", "What food would make me ill?", "paraphrase", ["p02#1|f06"], ["x02"]),
    ("h006", "Which flowers does my mum like?", "paraphrase", ["p20#1|f24"], []),
    ("h007", "Where does my little girl go to school?", "paraphrase", ["p07#1|f14"], []),
    ("h008", "What time do I have to head out for the school run?", "paraphrase", ["p08#1|f16"], []),
    ("h009", "How much is the house loan each month?", "assistant", ["p11#1"], []),
    ("h010", "Which book did you pick for the reading group?", "assistant", ["p19#1"], []),
    ("h011", "What did you say about the laptop's charge limit?", "assistant", ["p21#1"], []),
    ("h012", "Which city do I live in now?", "update", ["p15#1|f25"], ["p01#1", "x01"]),
    ("h013", "What is my current job title?", "update", ["p17#1|f34"], ["p01#1"]),
    ("h014", "When is the footbridge retrofit due now?", "update", ["p27#1|f12b"], ["p06#1"]),
    ("h015", "Are we getting an electric car?", "update", ["p30#1|f35"], ["p29#1", "f36"]),
    ("h016", "Which hotel booking is ZX4-91Q?", "keyword", ["p22#1|f32"], []),
    ("h017", "What's the check word on the stress-run marker?", "fact", ["z11"], []),
    ("h018", "Which neighbour keeps a key to our place?", "fact", ["n01"], []),
    ("h019", "Who insures our car?", "fact", ["n03"], []),
    ("h020", "is it true that my blood group is negative or what?", "noise", ["p10#1|f18"], []),
    ("h021", "وين أسكن الحين؟", "mixed", ["p15#1|f25"], ["p01#1", "x01"]),
    ("h022", "شنو فصيلة دمي؟", "mixed", ["p10#1|f18"], []),
    ("h023", "متى ينتهي جوازي؟", "mixed", ["p05#1|f10"], []),
    ("h024", "What's my brother's name?", "mixed", ["a01#1|g01"], []),
    ("h025", "Which bank does Fahad work at?", "mixed", ["a01#1|g02"], []),
    ("h026", "شنو رقم رحلتنا إلى Lisbon؟", "mixed", ["p23#1|f26"], ["h07#1", "x07"]),
    ("h027", "Maya وش تسوي يوم الأربعاء؟", "mixed", ["a06#1|g07"], []),
    ("h028", "رحلة إسطنبول متى بالضبط؟", "arabic", ["a04#1|g05"], []),
    ("h029", "عطني رقم ملفي الطبي", "arabic", ["a05#1|g06"], []),
    ("h030", "شنو اسم الرواية العربية اللي نصحتني فيها؟", "arabic", ["a08#1"], []),
    ("h031", "What is my sister's name?", "abstain", [], []),
    ("h032", "Which dentist does Noor see?", "abstain", [], []),
    ("h033", "What colour is my car?", "abstain", [], []),
    ("h034", "When is Maya's birthday?", "abstain", [], []),
    ("h035", "شنو اسم مدير أخوي فهد؟", "abstain", [], []),
    ("h036", "What's my salary at the new job?", "abstain", [], []),
    ("h037", "Catch me up on the bathroom remodel.", "abstain", [], []),
    ("h038", "وش رقم سيارتي؟", "abstain", [], []),
    ("h039", "What was my finishing time in the Lisbon half?", "overview", ["sr10|r10#1"], []),
    ("h040", "What do you know about our Istanbul plans?", "overview", ["sw6|a04#1|g05"], []),
]


def _questions(rows, refs, fact_ids, summary_ids) -> list[dict[str, Any]]:
    out = []
    for qid, text, cat, gold, below in rows:
        for ref in [a for piece in gold for a in piece.split("|")] + below:
            if ref not in refs and ref not in fact_ids and ref not in summary_ids:
                raise ValueError(f"{qid}: unknown memory {ref}")
        out.append({"id": qid, "text": text, "category": cat, "gold": gold, "below": below})
    return out


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
    summaries = []
    for sid, day, title, text, sources in SUMMARIES:
        for ref in sources:
            if ref not in refs:
                raise ValueError(f"{sid}: unknown source turn {ref}")
        summaries.append({"id": sid, "day": day, "title": title, "text": text, "sources": sources})
    summaries.extend(_distractor_summaries(sessions))
    summary_ids = {s["id"] for s in summaries}
    if len(summary_ids) != len(summaries) or summary_ids & (fact_ids | refs):
        raise ValueError("duplicate summary id")
    questions = _questions(QUESTIONS, refs, fact_ids, summary_ids)
    second = _questions(SECOND_SET, refs, fact_ids, summary_ids)
    if {q["id"] for q in questions} & {q["id"] for q in second} or {q["text"] for q in questions} & {
            q["text"] for q in second}:
        raise ValueError("a question of the second set repeats a tuning question")
    return {"version": 5, "seed": SEED, "sessions": sessions, "facts": facts,
            "summaries": summaries, "questions": questions, "second": second}


#: The extraction benchmark (Memory audit M24): one chat turn each, in English,
#: Arabic (Gulf and MSA) or both, with the facts a reader would keep from it
#: -- each fact a list of acceptable (subject, predicate names, value) -- or
#: none (a question, thanks: nothing should be kept). Scored by
#: ``benchmark.run_extraction_benchmark``.
EXTRACTION_TURNS: list[tuple[str, str, str, list[list[tuple[str, list[str], str]]]]] = [
    ("e01", "en", "My name is Layla and I live in Lisbon now.",
     [[("user", ["name", "name_is"], "Layla")], [("user", ["lives_in", "location", "city"], "Lisbon")]]),
    ("e02", "en", "I work at Northwind Labs as a data analyst.",
     [[("user", ["works_at", "employer", "company"], "Northwind Labs")],
      [("user", ["job_title", "role", "occupation", "job"], "data analyst")]]),
    ("e03", "en", "I prefer tea over coffee in the mornings.",
     [[("user", ["prefers", "drink_preference", "beverage_preference", "morning_drink"], "tea")]]),
    ("e04", "en", "My favourite city is Kyoto.",
     [[("user", ["favorite_city", "favourite_city"], "Kyoto")]]),
    ("e05", "en", "I'm allergic to penicillin, please remember that.",
     [[("user", ["allergic_to", "allergy", "allergies"], "penicillin")]]),
    ("e06", "en", "We moved to Rotterdam last month.",
     [[("user", ["lives_in", "location", "city", "moved_to"], "Rotterdam")]]),
    ("e07", "en", "My sister Hana is a pilot with KLM.",
     [[("user", ["sister", "has_sister", "sibling"], "Hana"), ("hana", ["relation", "relationship"], "sister")],
      [("hana", ["job", "job_title", "occupation", "role", "profession"], "pilot"),
       ("hana", ["works_at", "employer"], "KLM")]]),
    ("e08", "en", "Can you explain how a heat pump works?", []),
    ("e09", "en", "Thanks, that's really helpful!", []),
    ("e10", "en", "I love hiking and I use a Garmin watch.",
     [[("user", ["prefers", "likes", "hobby", "hobbies", "enjoys"], "hiking")],
      [("user", ["uses", "owns", "device", "watch"], "Garmin")]]),
    ("e11", "en", "The project deadline is the 3rd of May.",
     [[("project", ["deadline", "due_date"], "3rd of May"), ("user", ["project_deadline", "deadline"], "3rd of May")]]),
    ("e12", "en", "What's the weather like in Porto in April?", []),
    ("a01", "ar", "اسمي ليلى وساكنة في دبي.",
     [[("user", ["name", "name_is"], "ليلى")], [("user", ["lives_in", "location", "city"], "دبي")]]),
    ("a02", "ar", "أشتغل في شركة أرامكو مهندسة.",
     [[("user", ["works_at", "employer", "company"], "أرامكو")],
      [("user", ["job_title", "job", "occupation", "role", "profession"], "مهندسة")]]),
    ("a03", "ar", "أحب الشاي بالنعناع.",
     [[("user", ["prefers", "likes", "drink_preference", "beverage_preference"], "الشاي بالنعناع")]]),
    ("a04", "ar", "عندي حساسية من الفول السوداني.",
     [[("user", ["allergic_to", "allergy", "allergies"], "الفول السوداني")]]),
    ("a05", "ar", "انتقلنا إلى الرياض الشهر الماضي.",
     [[("user", ["lives_in", "location", "city", "moved_to"], "الرياض")]]),
    ("a06", "ar", "أختي نورة طبيبة أسنان.",
     [[("user", ["sister", "has_sister", "sibling"], "نورة"), ("نورة", ["relation", "relationship"], "أخت")],
      [("نورة", ["job", "job_title", "occupation", "profession", "role"], "طبيبة أسنان")]]),
    ("a07", "ar", "شكراً، وايد مفيد", []),
    ("a08", "ar", "كيف أسوي كيكة بدون بيض؟", []),
    ("a09", "ar", "جوازي ينتهي في ٢٠٢٩",
     [[("user", ["passport_expires", "passport_expiry", "passport_expiration"], "٢٠٢٩")]]),
    ("a10", "ar", "رياضتي المفضلة السباحة",
     [[("user", ["favorite_sport", "favourite_sport", "sport", "hobby"], "السباحة")]]),
    ("m01", "mixed", "My name is Omar وساكن في Kuwait City.",
     [[("user", ["name", "name_is"], "Omar")], [("user", ["lives_in", "location", "city"], "Kuwait City")]]),
    ("m02", "mixed", "أشتغل at Gulf Bank as a teller.",
     [[("user", ["works_at", "employer", "company"], "Gulf Bank")],
      [("user", ["job_title", "job", "occupation", "role"], "teller")]]),
    ("m03", "mixed", "I prefer القهوة العربية with cardamom.",
     [[("user", ["prefers", "likes", "coffee_preference", "drink_preference"], "القهوة العربية")]]),
    ("m04", "mixed", "عندي meeting مع Sarah كل Sunday الساعة 9.",
     [[("user", ["meeting", "recurring_meeting", "meets_with", "schedule"], "Sarah"),
       ("user", ["meeting", "recurring_meeting", "schedule"], "Sunday")]]),
    ("m05", "mixed", "My daughter Lulwa تدرس في Bayan School.",
     [[("lulwa", ["school", "studies_at", "attends"], "Bayan School"),
       ("user", ["daughter_school"], "Bayan School")],
      [("user", ["daughter", "has_daughter", "child"], "Lulwa"), ("lulwa", ["relation", "relationship"], "daughter")]]),
    ("m06", "mixed", "ok تمام", []),
    ("m07", "mixed", "I work at Zain الحين, moved from Ooredoo.",
     [[("user", ["works_at", "employer", "company"], "Zain")]]),
    ("m08", "mixed", "Call me Abu Faisal يا Kazma.",
     [[("user", ["name", "name_is", "nickname", "preferred_name"], "Abu Faisal")]]),
]


#: True facts a turn holds that its gold list does not ask for: a pass that
#: keeps one is not wrong (dataset v2).
EXTRACTION_EXTRAS: dict[str, list[list[tuple[str, list[str], str]]]] = {
    "m07": [[("user", ["previously_worked_at", "former_employer", "worked_at"], "Ooredoo")]],
}

#: Facts a turn must not give, whatever else it keeps (dataset v2): the
#: assistant's name filed as a fact, and someone else's job, employer or
#: school filed as the user's own (the user is never "a pilot" because their
#: sister is). ``None`` for the names: under any name.
_JOB = ["job", "job_title", "occupation", "role", "profession", "works_as"]
EXTRACTION_AVOID: dict[str, list[tuple[str, list[str] | None, str]]] = {
    "e07": [("user", _JOB, "pilot"), ("user", ["works_at", "employer", "company"], "KLM")],
    "a06": [("user", _JOB, "طبيبة أسنان")],
    "m05": [("user", ["school", "studies_at", "attends"], "Bayan School")],
    "m08": [("assistant", None, "Kazma"), ("kazma", None, "Abu Faisal")],
}

#: The extraction dataset's version: v2 added ``extras`` and ``avoid`` and
#: the scores read the value (benchmark audit F1).
EXTRACTION_VERSION = 2


def build_extraction() -> dict[str, Any]:
    turns = []

    def facts_of(rows: list[list[tuple[str, list[str], str]]]) -> list[list[dict[str, Any]]]:
        return [[{"subject": a, "predicates": b, "object": c} for a, b, c in alts] for alts in rows]

    for tid, lang, text, facts in EXTRACTION_TURNS:
        turn: dict[str, Any] = {"id": tid, "lang": lang, "user": text, "facts": facts_of(facts)}
        if tid in EXTRACTION_EXTRAS:
            turn["extras"] = facts_of(EXTRACTION_EXTRAS[tid])
        if tid in EXTRACTION_AVOID:
            turn["avoid"] = [{"subject": a, **({"predicates": b} if b else {}), "object": c}
                             for a, b, c in EXTRACTION_AVOID[tid]]
        turns.append(turn)
    ids = [t["id"] for t in turns]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate extraction turn id")
    unknown = (set(EXTRACTION_EXTRAS) | set(EXTRACTION_AVOID)) - set(ids)
    if unknown:
        raise ValueError(f"extras or avoid for no turn: {sorted(unknown)}")
    return {"version": EXTRACTION_VERSION, "turns": turns}


#: Write scenarios (2026-11, the memory framework audit's F1, F8): a few turns
#: of one chat through the write path -- the quick pass, then the model's deep
#: pass -- and the facts that must be current, and must not, afterwards. The
#: model's reply to a turn is either its recorded reply to an extraction turn
#: of the same text (``("recorded", "e06")``) or written here in the shape the
#: model returns (``("written", [...])``): a scenario tests Kazma's rules
#: around the model, never the model. Scored by
#: ``benchmark.run_write_scenarios``.
#:
#: Categories: ``update`` (a fact stated, then corrected in the user's own
#: words), ``kept_as_said`` (the deep pass of the same turn leaves the user's
#: words and trust as they were), ``short`` (short statements are read, small
#: talk is not), ``someone_else`` (a fact about another person never becomes
#: the user's), ``quoted`` (text the user quoted, attached or asked Kazma to
#: work on is never the user's own statement), ``model_read`` (a name the
#: model chose may not fit the words: the user's fact stands, and a
#: statement is asked about), ``temporal`` (the start a turn names is kept
#: beside the fact, worked out from the turn's day; a start after the turn
#: or in words is never kept). ``pending`` names the corrections that must wait
#: for the user (``memory/changes.py``), and only those; an ``("", ("answer",
#: "confirm"))`` turn is the user answering the newest one.
def _said(subject: str, predicate: str, kind: str, obj: str) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "predicate_type": kind, "object": obj, "confidence": 0.9}


def _now(subject: str, obj: str, trust: str | None = None, *, exact: bool = False,
         started: str | bool | None = False) -> dict[str, Any]:
    """A fact that must be current (or must not). *started*: the start kept
    beside it (``metadata.started``), None for none; False checks nothing."""
    out: dict[str, Any] = {"subject": subject, "object": obj}
    if trust:
        out["trust"] = trust
    if exact:
        out["exact"] = True
    if started is not False:
        out["started"] = started
    return out


def _began(fact: dict[str, Any], since: str) -> dict[str, Any]:
    """*fact* with the start the model gives it (``since``)."""
    return {**fact, "since": since}


def _attached(message: str, filename: str, mime: str, document: str) -> str:
    """A message with an attached document, in the shape the chat apps and
    the web chat give the model (``agent_handler/attachments.build_user_content``:
    the parsed text inside a data fence). tests/test_memory_own_words.py holds
    the shape to the real builder."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    fenced = format_untrusted_block(document, source="document_attachment")
    return (f"{message}\n[Attached: {filename} ({mime}) \u2014 parsed contents:\n{fenced}]\n"
            f"[Full document at: kazma-data/attachments/{filename} \u2014 use read_document for complete content]")


WRITE_SCENARIOS: list[dict[str, Any]] = [
    # ── update: stated, then corrected in the user's own words ──────────────
    # A correction the quick pass reads in its exact shapes replaces the fact
    # ("I live in Rotterdam now", "انتقلنا إلى ..."). One only the model reads
    # never does: Kazma cannot tell without a model whether the model's name
    # fits the words, so it keeps the user's fact and asks (``pending``);
    # the user's "Yes, change it" (an ``answer`` turn) makes the change (the
    # review of the memory framework audit's F1, 2026-11-18; v3).
    {"id": "update-en-moved", "category": "update", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("We moved to Rotterdam last month.", ("recorded", "e06"))],
     "current": [_now("user", "Paris", "user")], "not_current": [_now("user", "Rotterdam")],
     "pending": [_now("user", "Rotterdam")]},
    {"id": "update-en-moved-confirmed", "category": "update", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("We moved to Rotterdam last month.", ("recorded", "e06")),
        ("", ("answer", "confirm"))],
     "current": [_now("user", "Rotterdam", "user")], "not_current": [_now("user", "Paris")]},
    {"id": "update-en-new-job", "category": "update", "lang": "en", "turns": [
        ("I work at Google.", ("written", [_said("user", "works_at", "functional", "Google")])),
        ("I left Google, I am at Meta now.", ("written", [_said("user", "works_at", "functional", "Meta")]))],
     "current": [_now("user", "Google", "user")], "not_current": [_now("user", "Meta")],
     "pending": [_now("user", "Meta")]},
    {"id": "update-en-said-again", "category": "update", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("I live in Rotterdam now.", ("written", [_said("user", "lives_in", "functional", "Rotterdam")]))],
     "current": [_now("user", "Rotterdam", "user")], "not_current": [_now("user", "Paris")]},
    {"id": "update-ar-moved", "category": "update", "lang": "ar", "turns": [
        ("ساكن في الكويت.", ("written", [_said("user", "lives_in", "functional", "الكويت")])),
        ("انتقلنا إلى الرياض الشهر الماضي.", ("recorded", "a05"))],
     "current": [_now("user", "الرياض", "user")], "not_current": [_now("user", "الكويت")]},
    {"id": "update-ar-new-job", "category": "update", "lang": "ar", "turns": [
        ("أشتغل في شركة زين.", ("written", [_said("user", "works_at", "functional", "شركة زين")])),
        ("تركت زين، أشتغل الحين في أريدُو.", ("written", [_said("user", "works_at", "functional", "أريدو")]))],
     "current": [_now("user", "زين", "user")], "not_current": [_now("user", "أريدو")],
     "pending": [_now("user", "أريدو")]},
    {"id": "update-ar-new-job-confirmed", "category": "update", "lang": "ar", "turns": [
        ("أشتغل في شركة زين.", ("written", [_said("user", "works_at", "functional", "شركة زين")])),
        ("تركت زين، أشتغل الحين في أريدُو.", ("written", [_said("user", "works_at", "functional", "أريدو")])),
        ("", ("answer", "confirm"))],
     "current": [_now("user", "أريدو", "user")], "not_current": [_now("user", "زين")]},
    {"id": "update-mixed-moved", "category": "update", "lang": "mixed", "turns": [
        ("My name is Omar وساكن في Kuwait City.", ("recorded", "m01")),
        ("We moved to Rotterdam last month.", ("recorded", "e06"))],
     "current": [_now("user", "Kuwait City", "user")], "not_current": [_now("user", "Rotterdam")],
     "pending": [_now("user", "Rotterdam")]},
    # A word that also introduces a text to work on, said with no text named:
    # the user's own correction, kept as theirs.
    {"id": "update-en-correction", "category": "update", "lang": "en", "turns": [
        ("My name is Ahmed.", ("written", [_said("user", "name_is", "functional", "Ahmed")])),
        ("Correct: my name is Ahmad.", ("written", [_said("user", "name_is", "functional", "Ahmad")]))],
     "current": [_now("user", "Ahmad", "user")], "not_current": [_now("user", "Ahmed")]},
    {"id": "update-ar-correction", "category": "update", "lang": "ar", "turns": [
        ("اسمي أحمد.", ("written", [_said("user", "name_is", "functional", "أحمد")])),
        ("صحح: اسمي محمد.", ("written", [_said("user", "name_is", "functional", "محمد")]))],
     "current": [_now("user", "محمد", "user")], "not_current": [_now("user", "أحمد")]},
    # ── model_read: the model files a reading under a name the words may not
    # fit. The user's fact stands in every one; a statement is asked about,
    # a denial, a question or a request is not (the review of F1's first
    # half: each of these replaced the user's fact).
    {"id": "model-en-visiting", "category": "model_read", "lang": "en", "turns": [
        ("I live in Kuwait.", ("written", [_said("user", "lives_in", "functional", "Kuwait")])),
        ("I'm visiting Dubai next week.", ("written", [_said("user", "lives_in", "functional", "dubai")]))],
     "current": [_now("user", "Kuwait", "user")], "not_current": [_now("user", "Dubai")],
     "pending": [_now("user", "Dubai")]},
    {"id": "model-en-never", "category": "model_read", "lang": "en", "turns": [
        ("I live in Kuwait.", ("written", [_said("user", "lives_in", "functional", "Kuwait")])),
        ("I never lived in Paris.", ("written", [_said("user", "lives_in", "functional", "paris")]))],
     "current": [_now("user", "Kuwait", "user")], "not_current": [_now("user", "Paris")]},
    {"id": "model-en-someone-named", "category": "model_read", "lang": "en", "turns": [
        ("I live in Kuwait.", ("written", [_said("user", "lives_in", "functional", "Kuwait")])),
        ("I think Rotterdam is where Dana lives.",
         ("written", [_said("user", "lives_in", "functional", "rotterdam")]))],
     "current": [_now("user", "Kuwait", "user")], "not_current": [_now("user", "Rotterdam")],
     "pending": [_now("user", "Rotterdam")]},
    {"id": "model-en-met", "category": "model_read", "lang": "en", "turns": [
        ("My name is Sam.", ("written", [_said("user", "name_is", "functional", "Sam")])),
        ("I met Dana yesterday.", ("written", [_said("user", "name_is", "functional", "dana")]))],
     "current": [_now("user", "Sam", "user")], "not_current": [_now("user", "Dana")],
     "pending": [_now("user", "Dana")]},
    {"id": "model-en-read-that", "category": "model_read", "lang": "en", "turns": [
        ("I work at Acme.", ("written", [_said("user", "works_at", "functional", "Acme")])),
        ("I read that Initech is hiring.", ("written", [_said("user", "works_at", "functional", "initech")]))],
     "current": [_now("user", "Acme", "user")], "not_current": [_now("user", "Initech")],
     "pending": [_now("user", "Initech")]},
    {"id": "model-en-question", "category": "model_read", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("Should I move to Rotterdam?", ("written", [_said("user", "lives_in", "functional", "rotterdam")]))],
     "current": [_now("user", "Paris", "user")], "not_current": [_now("user", "Rotterdam")]},
    {"id": "model-en-summary", "category": "model_read", "lang": "en", "turns": [
        ("I live in Kuwait.", ("written", [_said("user", "lives_in", "functional", "Kuwait")])),
        ("Summarize this page about Dubai.", ("written", [_said("user", "lives_in", "functional", "Dubai")]))],
     "current": [_now("user", "Kuwait", "user")], "not_current": [_now("user", "Dubai")]},
    {"id": "model-ar-going", "category": "model_read", "lang": "ar", "turns": [
        ("ساكن في الكويت.", ("written", [_said("user", "lives_in", "functional", "الكويت")])),
        ("أنا رايح دبي الأسبوع الجاي", ("written", [_said("user", "lives_in", "functional", "دبي")]))],
     "current": [_now("user", "الكويت", "user")], "not_current": [_now("user", "دبي")],
     "pending": [_now("user", "دبي")]},
    {"id": "model-ar-never", "category": "model_read", "lang": "ar", "turns": [
        ("ساكن في الكويت.", ("written", [_said("user", "lives_in", "functional", "الكويت")])),
        ("أنا ما أسكن في دبي.", ("written", [_said("user", "lives_in", "functional", "دبي")]))],
     "current": [_now("user", "الكويت", "user")], "not_current": [_now("user", "دبي")]},
    {"id": "model-ar-question", "category": "model_read", "lang": "ar", "turns": [
        ("ساكن في الكويت.", ("written", [_said("user", "lives_in", "functional", "الكويت")])),
        ("هل أنتقل إلى دبي؟", ("written", [_said("user", "lives_in", "functional", "دبي")]))],
     "current": [_now("user", "الكويت", "user")], "not_current": [_now("user", "دبي")]},
    # ── kept as said: the deep pass of the same turn keeps the user's words ─
    {"id": "kept-en-name-and-city", "category": "kept_as_said", "lang": "en", "turns": [
        ("My name is Layla and I live in Lisbon now.", ("recorded", "e01"))],
     "current": [_now("user", "Layla", "user", exact=True), _now("user", "Lisbon", "user", exact=True)],
     "not_current": []},
    # The quick pass reads the employer; the role only the model reads is
    # its first reading, Kazma's own, and no question.
    {"id": "kept-mixed-employer", "category": "kept_as_said", "lang": "mixed", "turns": [
        ("أشتغل at Gulf Bank as a teller.", ("recorded", "m02"))],
     "current": [_now("user", "Gulf Bank", "user", exact=True)], "not_current": []},
    {"id": "kept-ar-name-and-city", "category": "kept_as_said", "lang": "ar", "turns": [
        ("اسمي ليلى وساكنة في دبي.", ("recorded", "a01"))],
     "current": [_now("user", "ليلى", "user", exact=True), _now("user", "دبي", "user", exact=True)],
     "not_current": []},
    # ── short statements are read; small talk is not ──────────────────────
    {"id": "short-en-condition", "category": "short", "lang": "en", "turns": [
        ("I'm diabetic", ("written", [_said("user", "has_condition", "set", "diabetes")]))],
     "current": [_now("user", "diabetes")], "not_current": []},
    {"id": "short-en-call-me", "category": "short", "lang": "en", "turns": [
        ("Call me Sam", ("written", [_said("user", "preferred_name", "functional", "Sam")]))],
     "current": [_now("user", "Sam")], "not_current": []},
    {"id": "short-en-diet", "category": "short", "lang": "en", "turns": [
        ("I'm vegan", ("written", [_said("user", "diet", "functional", "vegan")]))],
     "current": [_now("user", "vegan")], "not_current": []},
    # "No, Lisbon" names no one: the model's reading of it waits for the user.
    {"id": "short-en-no-city", "category": "short", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("No, Lisbon", ("written", [_said("user", "lives_in", "functional", "Lisbon")]))],
     "current": [_now("user", "Paris", "user")], "not_current": [_now("user", "Lisbon")],
     "pending": [_now("user", "Lisbon")]},
    {"id": "short-en-thanks", "category": "short", "lang": "en", "turns": [
        ("Thanks!", ("written", [_said("user", "mood", "state", "grateful")]))],
     "current": [], "not_current": [_now("user", "grateful")]},
    {"id": "short-ar-condition", "category": "short", "lang": "ar", "turns": [
        ("عندي سكري", ("written", [_said("user", "has_condition", "set", "السكري")]))],
     "current": [_now("user", "السكري")], "not_current": []},
    {"id": "short-ar-thanks", "category": "short", "lang": "ar", "turns": [
        ("شكراً", ("written", [_said("user", "mood", "state", "ممتن")]))],
     "current": [], "not_current": [_now("user", "ممتن")]},
    # ── someone else: a fact about another person never becomes the user's ─
    {"id": "other-en-sister-moved", "category": "someone_else", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("My sister moved to Rotterdam last month.", ("written", [_said("user", "lives_in", "functional", "Rotterdam")]))],
     "current": [_now("user", "Paris", "user")], "not_current": [_now("user", "Rotterdam")]},
    {"id": "other-en-manager-job", "category": "someone_else", "lang": "en", "turns": [
        ("I work at Google.", ("written", [_said("user", "works_at", "functional", "Google")])),
        ("My manager works at Meta now.", ("written", [_said("user", "works_at", "functional", "Meta")]))],
     "current": [_now("user", "Google", "user")], "not_current": [_now("user", "Meta")]},
    {"id": "other-ar-sister-moved", "category": "someone_else", "lang": "ar", "turns": [
        ("ساكن في الكويت.", ("written", [_said("user", "lives_in", "functional", "الكويت")])),
        ("أختي انتقلت إلى روتردام.", ("written", [_said("أختي", "lives_in", "functional", "روتردام")]))],
     "current": [_now("user", "الكويت", "user")], "not_current": [_now("user", "روتردام")]},
    {"id": "other-mixed-sister-job", "category": "someone_else", "lang": "mixed", "turns": [
        ("أشتغل في شركة زين.", ("written", [_said("user", "works_at", "functional", "شركة زين")])),
        ("أختي تشتغل at Gulf Bank.", ("written", [_said("user", "works_at", "functional", "Gulf Bank")]))],
     "current": [_now("user", "زين", "user")], "not_current": [_now("user", "Gulf Bank")]},
    # ── quoted: text the user quoted, attached or asked Kazma to work on ───
    # The model's reply here files the quoted facts under the user, as a
    # careless model would: what the quick pass reads decides the outcome.
    {"id": "quoted-en-translate", "category": "quoted", "lang": "en", "turns": [
        ("My name is Layla.", ("written", [_said("user", "name_is", "functional", "Layla")])),
        ("Translate to French: My name is Pierre and I live in Lyon.",
         ("written", [_said("user", "name_is", "functional", "Pierre"),
                      _said("user", "lives_in", "functional", "Lyon")]))],
     "current": [_now("user", "Layla", "user")],
     "not_current": [_now("user", "Pierre"), _now("user", "Lyon", "user")]},
    {"id": "quoted-ar-translate", "category": "quoted", "lang": "ar", "turns": [
        ("اسمي ليلى.", ("written", [_said("user", "name_is", "functional", "ليلى")])),
        ("ترجم للإنجليزي: اسمي سارة وساكنة في جدة.",
         ("written", [_said("user", "name_is", "functional", "سارة"),
                      _said("user", "lives_in", "functional", "جدة")]))],
     "current": [_now("user", "ليلى", "user")],
     "not_current": [_now("user", "سارة"), _now("user", "جدة", "user")]},
    {"id": "quoted-en-attached", "category": "quoted", "lang": "en", "turns": [
        ("I work at Google.", ("written", [_said("user", "works_at", "functional", "Google")])),
        (_attached("Please file this.", "letter.pdf", "application/pdf",
                   "Dear team, my name is Sara Lee and I work at Acme Corp."),
         ("written", [_said("user", "works_at", "functional", "Acme Corp")]))],
     "current": [_now("user", "Google", "user")],
     "not_current": [_now("user", "Acme Corp"), _now("user", "Sara Lee", "user")]},
    {"id": "quoted-ar-attached", "category": "quoted", "lang": "ar", "turns": [
        ("أشتغل في شركة زين.", ("written", [_said("user", "works_at", "functional", "شركة زين")])),
        (_attached("خل هذا الملف عندك.", "خطاب.pdf", "application/pdf",
                   "أشتغل في شركة الاتصالات وساكن في الدمام."),
         ("written", [_said("user", "works_at", "functional", "شركة الاتصالات")]))],
     "current": [_now("user", "زين", "user")],
     "not_current": [_now("user", "شركة الاتصالات"), _now("user", "الدمام", "user")]},
    {"id": "quoted-mixed-fix", "category": "quoted", "lang": "mixed", "turns": [
        ("My name is Omar.", ("written", [_said("user", "name_is", "functional", "Omar")])),
        ("Fix the grammar: اسمي سالم and I work at Gulf Bank.",
         ("written", [_said("user", "works_at", "functional", "Gulf Bank")]))],
     "current": [_now("user", "Omar", "user")],
     "not_current": [_now("user", "سالم"), _now("user", "Gulf Bank", "user")]},
    {"id": "quoted-en-reported", "category": "quoted", "lang": "en", "turns": [
        ("I live in Paris.", ("written", [_said("user", "lives_in", "functional", "Paris")])),
        ("My sister said: I live in Lyon now.",
         ("written", [_said("sister", "lives_in", "functional", "Lyon")]))],
     "current": [_now("user", "Paris", "user")], "not_current": [_now("user", "Lyon")]},
    # ── temporal: the start a turn names, kept beside the fact ──────────────
    # The model is given the turn's day (framework audit F2 step 2) and works
    # out "since"; Kazma keeps a real date no later than the turn, as said,
    # and never moves the fact's place in time for it (W1).
    {"id": "temporal-en-started", "category": "temporal", "lang": "en", "turns": [
        ("I started working at Zain in March.",
         ("written", [_began(_said("user", "works_at", "functional", "Zain"), "2026-03")]))],
     "current": [_now("user", "Zain", started="2026-03")], "not_current": []},
    {"id": "temporal-ar-started", "category": "temporal", "lang": "ar", "turns": [
        ("بديت أشتغل في زين من شهر مارس.",
         ("written", [_began(_said("user", "works_at", "functional", "زين"), "2026-03")]))],
     "current": [_now("user", "زين", started="2026-03")], "not_current": []},
    {"id": "temporal-en-future-start", "category": "temporal", "lang": "en", "turns": [
        ("I have lived in Lisbon for years.",
         ("written", [_began(_said("user", "lives_in", "functional", "Lisbon"), "2031-01")]))],
     "current": [_now("user", "Lisbon", started=None)], "not_current": []},
    {"id": "temporal-ar-words-not-a-date", "category": "temporal", "lang": "ar", "turns": [
        ("ساكن في الدوحة من سنتين.",
         ("written", [_began(_said("user", "lives_in", "functional", "الدوحة"), "قبل سنتين")]))],
     "current": [_now("user", "الدوحة", started=None)], "not_current": []},
]

#: The write scenarios' version. 3: a correction only the model reads waits
#: for the user (``pending``, ``answer`` turns), and the ``model_read``
#: category (2026-11-18). 4: a model's reading never gets the user's trust by
#: itself (rule A of the second security review, 2026-11-18); its first
#: reading of a fact is Kazma's own and no question. 5: ``temporal`` (the
#: framework audit's part C2, 2026-11-20).
WRITES_VERSION = 5


def build_writes() -> dict[str, Any]:
    """The write scenarios, checked: every recorded reply names an extraction
    turn of the same text, every written reply is in the model's shape."""
    by_id = {tid: text for tid, _lang, text, _facts in EXTRACTION_TURNS}
    scenarios = []
    for sc in WRITE_SCENARIOS:
        turns = []
        for text, (kind, reply) in sc["turns"]:
            if kind == "recorded":
                if by_id.get(reply) != text:
                    raise ValueError(f"{sc['id']}: no extraction turn {reply!r} says {text!r}")
                turns.append({"user": text, "recorded": reply})
            elif kind == "written":
                for fact in reply:
                    if not all(fact.get(k) for k in ("subject", "predicate", "predicate_type", "object")):
                        raise ValueError(f"{sc['id']}: a written reply needs subject, predicate, type and object")
                    if set(fact) - {"subject", "predicate", "predicate_type", "object", "confidence", "since"}:
                        raise ValueError(f"{sc['id']}: a written reply holds only what the model's reply holds")
                turns.append({"user": text, "written": reply})
            elif kind == "answer":
                if reply not in ("confirm", "dismiss"):
                    raise ValueError(f"{sc['id']}: the user answers confirm or dismiss, not {reply!r}")
                turns.append({"answer": reply})
            else:
                raise ValueError(f"{sc['id']}: a turn's reply is recorded, written or an answer, not {kind!r}")
        built = {"id": sc["id"], "category": sc["category"], "lang": sc["lang"], "turns": turns,
                 "current": sc["current"], "not_current": sc["not_current"]}
        if sc.get("pending"):
            built["pending"] = sc["pending"]
        scenarios.append(built)
    ids = [s["id"] for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate write scenario id")
    return {"version": WRITES_VERSION, "scenarios": scenarios}


def _cmd_build() -> int:
    from kazma_core.memory.benchmark import load_dataset

    data = build()
    EXTRACTION.write_text(json.dumps(build_extraction(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    WRITES.write_text(json.dumps(build_writes(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
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


def _limit_cpu_threads() -> None:
    """Leave most of the CPU to whatever else runs on this machine.

    The live install shares it with the dev checkout: on 2026-09-27 two
    embedding runs at torch's default (every core) starved the live server
    until its guard restarted it twice. Set before the model loads.
    """
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))


def _cmd_vectors() -> int:
    from kazma_core.memory.benchmark import RecordingEmbedder, run_benchmark
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    _limit_cpu_threads()
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
        _limit_cpu_threads()
        report = run_benchmark(keep_results=details)
    else:
        replay = ReplayEmbedder(VECTORS)
        a, b = _patched(replay, replay.model)
        with a, b:
            report = run_benchmark(keep_results=details)
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=1))
        return 0
    print(f"{report['memories']} memories, {report['overall']['questions']} questions")
    for name, row in report["categories"].items():
        extra = "  ".join(f"{k} {v}" for k, v in row.items() if k not in ("n",))
        print(f"  {name:<11} n={row['n']:<3} {extra}")
    print("overall:", json.dumps(report["overall"]))
    held = report.get("second")
    if held:
        print(f"second set: {held['overall']['questions']} questions")
        for name, row in held["categories"].items():
            extra = "  ".join(f"{k} {v}" for k, v in row.items() if k not in ("n",))
            print(f"  {name:<11} n={row['n']:<3} {extra}")
        print("second-set overall:", json.dumps(held["overall"]))
    from kazma_core.memory.benchmark import gated_scores

    scores = gated_scores(report)
    for key, target in TARGETS.items():
        print(f"target {key}: {scores.get(key)} (target {target})")
    if details:
        for r in report.get("results", []):
            if not r["hit"]:
                print("  MISS", r)
    return 0


#: Where recall should get to, not a floor: injecting nothing for a question
#: nothing answers (Memory audit M24). The report shows the distance.
TARGETS = {"abstain.hit_rate": 0.6, "second.abstain.hit_rate": 0.6}

#: A reviewed trade (``lock --trade "why"``): a change may lower gated scores
#: only when every lowered score drops by at most TRADE_MAX_DROP, at least one
#: score rises by at least TRADE_MIN_GAIN, and the abstention scores never
#: drop -- they are the target the trade must not move away from. The trade is
#: recorded in thresholds.json (date, reason, what fell, what rose), so the
#: review sees it in the diff.
TRADE_MAX_DROP = 0.05
TRADE_MIN_GAIN = 0.05
_NEVER_TRADED = ("abstain.hit_rate", "second.abstain.hit_rate")


def trade_problems(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """Why a lock that lowers *old* to *new* is not an acceptable trade
    (empty when it is)."""
    problems = []
    lowered = {k: (v, new.get(k, -1.0)) for k, v in old.items() if new.get(k, -1.0) < v}
    raised = {k: (old.get(k, -1.0), v) for k, v in new.items() if v > old.get(k, -1.0)}
    for k, (was, now) in sorted(lowered.items()):
        if k in _NEVER_TRADED:
            problems.append(f"{k} falls ({was} -> {now}): abstention is never traded")
        elif was - now > TRADE_MAX_DROP:
            problems.append(f"{k} falls by {round(was - now, 4)} (> {TRADE_MAX_DROP})")
    if lowered and not any(now - was >= TRADE_MIN_GAIN for k, (was, now) in raised.items() if k in old):
        problems.append(f"nothing rises by {TRADE_MIN_GAIN} or more to pay for it")
    return problems


#: How the edge list is left (``lock --edge QUESTION "why"``): a hit that flips
#: when the recorded vectors move inside their rounding stops the lock
#: (benchmark audit R4, the owner's decision 3). Fix the question or the
#: ranking, or name the question here: the list says its margin, how often it
#: flipped and why it stays. Never move a threshold to save one question.
EDGE_RULE = "a flipping hit stops the lock; the edge list, with its margin named, is the only exit"


def _edge_lock(
    base: dict[str, Any], old: dict[str, Any], *, fresh: bool, name: list[tuple[str, str]] | None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """The lock's edge check: (the ``edges`` section to write, why the lock
    is refused, the questions no longer at the edge)."""
    import datetime as _dt

    from kazma_core.memory import benchmark

    check = benchmark.edge_check(VECTORS, base)
    data = benchmark.load_dataset()
    questions = {q["id"]: q for q in data["questions"] + data.get("second", [])}
    results = {r["id"]: r for r in list(base.get("results", []))
               + list((base.get("second") or {}).get("results", []))}
    listed = {} if fresh else {e["id"]: e for e in old.get("hits", [])}
    adding = dict(name or [])
    problems = [f"--edge {qid}: its hit does not flip in the {check['draws']} draws; there is nothing to name"
                for qid in sorted(adding) if qid not in check["flips"]]
    hits = []
    for qid, flipped in check["flips"].items():
        q, r = questions[qid], results[qid]
        why = adding.get(qid) or listed.get(qid, {}).get("why")
        if not why:
            problems.append(
                f"{qid} ({q['category']}, {q['text']!r}): its hit {'holds' if r['hit'] else 'misses'} by "
                f"{abs(r['margin'] or 0.0)} of recall's cut and flips in {flipped} of {check['draws']} draws "
                f"inside the recorded vectors' rounding. Fix the question or the ranking -- never move a "
                f"threshold to save it -- or name it: lock --edge {qid} \"why\"")
            continue
        hits.append({"id": qid, "category": q["category"], "question": q["text"], "hit": bool(r["hit"]),
                     "margin": r["margin"], "flipped": flipped, "why": why,
                     "since": listed.get(qid, {}).get("since") or _dt.date.today().isoformat()})
    section = {"draws": check["draws"], "seed": check["seed"], "move": check["move"],
               "largest_move": check["largest_move"], "hits": hits, "shown": check["shown"]}
    return section, problems, sorted(set(listed) - set(check["flips"]))


def _cmd_lock(rebaseline: bool, trade: str | None = None, new_recording: str | None = None,
              edges: list[tuple[str, str]] | None = None, prompt_changed: str | None = None) -> int:
    """Write the replayed scores as the ratchet; refuse to lower any of them.

    The ratchet names the dataset versions it was measured on (the recall
    dataset and the extraction dataset). A new dataset is a new instrument:
    its first scores are written with ``--rebaseline``, which resets only the
    part whose dataset changed and is refused while neither changed -- so it
    can never be used to lower the gate on the same data. The model's
    extraction pass follows its own rules (:func:`_extraction_lock`).
    """
    from kazma_core.memory.benchmark import (
        ReplayEmbedder,
        gated_scores,
        load_dataset,
        run_benchmark,
    )

    version = load_dataset()["version"]
    extraction_version = json.loads(EXTRACTION.read_text(encoding="utf-8"))["version"]
    writes_version = json.loads(WRITES.read_text(encoding="utf-8"))["version"]
    old = json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.is_file() else {}
    recall_new = old.get("dataset_version") != version
    extraction_new = (old.get("extraction") or {}).get("version") != extraction_version
    writes_new = (old.get("writes") or {}).get("version") != writes_version
    from kazma_core.memory.benchmark import load_speakers

    speakers_new = (old.get("speakers") or {}).get("version") != load_speakers()["version"]
    if speakers_new and not rebaseline:
        print("the ratchet does not measure this speakers case set: lock --rebaseline", file=sys.stderr)
        return 1
    if rebaseline and not (recall_new or extraction_new or writes_new or speakers_new):
        print(f"the ratchet already measures recall dataset v{version}, extraction dataset "
              f"v{extraction_version} and write scenarios v{writes_version}: nothing to rebaseline",
              file=sys.stderr)
        return 1
    if writes_new and not rebaseline:
        print(f"the ratchet measures write scenarios v{(old.get('writes') or {}).get('version')}, the "
              f"scenarios are v{writes_version}: lock --rebaseline", file=sys.stderr)
        return 1
    if (recall_new or extraction_new) and not rebaseline:
        if recall_new:
            print(f"the ratchet measures recall dataset v{old.get('dataset_version')}, the dataset is "
                  f"v{version}: record its vectors, then lock --rebaseline", file=sys.stderr)
        if extraction_new:
            print(f"the ratchet measures extraction dataset v{(old.get('extraction') or {}).get('version')}, "
                  f"the dataset is v{extraction_version}: lock --rebaseline", file=sys.stderr)
        return 1
    replay = ReplayEmbedder(VECTORS)
    a, b = _patched(replay, replay.model)
    with a, b:
        base = run_benchmark(keep_results=True)
    scores = gated_scores(base)
    old_scores = {} if recall_new else old.get("scores", {})
    lower = {k: (v, scores.get(k)) for k, v in old_scores.items() if scores.get(k, -1.0) < v}
    trades = list(old.get("trades", []))
    if lower and trade:
        problems = trade_problems(old_scores, scores)
        if problems:
            for line in problems:
                print(f"  {line}", file=sys.stderr)
            print("refusing the trade (see TRADE_RULE in scripts/memory_bench.py)", file=sys.stderr)
            return 1
        import datetime as _dt

        trades.append({
            "date": _dt.date.today().isoformat(), "dataset_version": version, "reason": trade,
            "lowered": {k: [was, now] for k, (was, now) in sorted(lower.items())},
            "raised": {k: [old_scores.get(k), v] for k, v in sorted(scores.items())
                       if k in old_scores and v > old_scores[k]},
        })
    elif lower:
        for k, (was, now) in sorted(lower.items()):
            print(f"  {k}: {now} < {was}", file=sys.stderr)
        print("refusing to lower the ratchet: fix the regression, or propose a reviewed "
              "trade with --trade \"why\"", file=sys.stderr)
        return 1
    edge, problems, settled = _edge_lock(base, old.get("edges") or {}, fresh=recall_new, name=edges)
    if problems:
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(f"refusing to lock: {EDGE_RULE}", file=sys.stderr)
        return 1
    from kazma_core.memory.benchmark import StaleRecordingError

    try:
        extraction, problems = _extraction_lock(old.get("extraction") or {}, fresh=extraction_new,
                                                new_recording=new_recording, prompt_changed=prompt_changed)
        writes, more = _writes_lock(old.get("writes") or {}, fresh=writes_new, new_recording=new_recording,
                                    pending=extraction.get("pending_recording"))
        problems += more
        speakers, more = _speakers_lock(old.get("speakers") or {}, fresh=speakers_new)
        problems += more
    except (StaleRecordingError, KeyError) as exc:
        problems = [str(exc)]
    if problems:
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print("refusing to lock the extraction scores", file=sys.stderr)
        return 1
    THRESHOLDS.write_text(
        json.dumps({"dataset_version": version, "scores": scores, "targets": TARGETS,
                    "trades": trades, "edges": edge,
                    "extraction": extraction, "writes": writes, "speakers": speakers,
                    # The pgvector path's floors (tests/test_memory_benchmark_pg.py),
                    # locked from a named CI run by `lock-pgvector`; same dataset only.
                    "pgvector": {} if recall_new else old.get("pgvector", {}),
                    # Every look at the sealed set, kept whatever the lock does.
                    **({"sealed": old["sealed"]} if old.get("sealed") else {})},
                   indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # What moved, in every part: the recall scores, then the extraction and
    # write-scenario parts (it said "0 raised" over a write scenario that
    # began to pass).
    moved = [(k, old_scores.get(k), v) for k, v in sorted(scores.items()) if v > old_scores.get(k, -1.0)]
    for part, new in (("extraction", extraction), ("writes", writes), ("speakers", speakers)):
        was = (old.get(part) or {}).get("scores") or {}
        moved += [(f"{part} {k}", was.get(k), v) for k, v in sorted(new.get("scores", {}).items())
                  if v != was.get(k)]
    now_pass = sorted(set((old.get("writes") or {}).get("failing") or []) - set(writes.get("failing") or []))
    print(f"{THRESHOLDS}: dataset v{version}, {len(moved)} changed")
    for k, was, now in moved:
        print(f"  {k}: {was} -> {now}")
    for sid in now_pass:
        print(f"  writes: {sid} passes now")
    for hit in edge["hits"]:
        print(f"  edge: {hit['id']} {'holds' if hit['hit'] else 'misses'} by {abs(hit['margin'] or 0.0)}, "
              f"flips in {hit['flipped']} of {edge['draws']} draws ({hit['why']})")
    for qid in settled:
        print(f"  edge: {qid} no longer flips; off the edge list")
    if edge["shown"]:
        print(f"  shown changes inside the rounding (listed in the lock): {edge['shown']}")
    return 0


def _declare_prompt_change(
    replies: dict[str, Any], pending: dict[str, Any] | None, why: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """The lock's declaration that the extraction prompt (or its message)
    changed and waits for its recording (``lock --prompt-changed "why"``),
    and why it is refused. One declaration covers every change until the
    recording: each change adds its date, reason and prompt to ``changes``;
    ``since`` is the first change's date."""
    import datetime as _dt

    from kazma_core.memory.belief_extractor import MESSAGE_SHAPE
    from kazma_core.memory.benchmark import _extraction_prompt_sha256, _recording_shape

    current, recorded = _extraction_prompt_sha256(), replies.get("prompt_sha256")
    shape, recorded_shape = MESSAGE_SHAPE, _recording_shape(replies)
    if current == recorded and shape == recorded_shape:
        return pending, ["--prompt-changed: the prompt and its message are the ones the recording answered"]
    if pending and pending.get("prompt_sha256") == current and int(pending.get("message_shape") or 1) == shape:
        return pending, ["--prompt-changed: this prompt is already declared"]
    keep = pending if pending and pending.get("replaces_prompt_sha256") == recorded else None
    today = _dt.date.today().isoformat()
    return {
        "prompt_sha256": current, "message_shape": shape,
        "replaces_prompt_sha256": recorded, "replaces_message_shape": recorded_shape,
        "since": (keep or {}).get("since") or today,
        "changes": [*((keep or {}).get("changes") or []),
                    {"date": today, "why": why, "prompt_sha256": current, "message_shape": shape}],
    }, []


def _relabelled(locked: dict[str, Any], identity: dict[str, Any]) -> bool:
    """The locked replies now say they answered another prompt, or were asked
    in another message form: a relabelled recording, never a new one."""
    return (locked.get("prompt_sha256") != identity["prompt_sha256"]
            or int(locked.get("message_shape") or 1) != int(identity.get("message_shape") or 1))


def _extraction_lock(
    old: dict[str, Any], *, fresh: bool = False, new_recording: str | None = None,
    prompt_changed: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The extraction part of the lock, and why it is refused (empty: it is not).

    *old* is the locked extraction part; *fresh* says the extraction dataset
    is new (``--rebaseline``), so today's scores are its first lock.

    * The quick pass is Kazma's own code: a ratchet, never lowered.
    * The model's pass is scored from one recording (benchmark audit F3). On
      the same recording its scores are compared exactly: only a change in
      the parser or the scorer moves them, and a drop is refused. A different
      recording is locked only on purpose (``--new-recording "why"``), and
      its scores replace the old ones exactly: one asking a turn has no
      spread to compare against, and the higher of two recordings, score by
      score, is a floor no recording reaches. The change is written down.
    * No recording, a prompt it did not answer, or a locked score missing
      from today's run: refused, never a shorter lock.
    * A prompt change waiting for its recording (framework audit part C2)
      is declared with ``--prompt-changed "why"`` (:func:`_declare_prompt_change`):
      until the recording, the model's pass replays the recording of the
      prompt before the change as a drift check, compared exactly, and the
      lock says so (``pending_recording``). A recording of today's prompt
      ends it.
    """
    import datetime as _dt

    from kazma_core.memory.benchmark import (
        RECORD_AGAIN,
        extraction_rests_on,
        extraction_scores,
        run_extraction_benchmark,
    )

    if not LLM_REPLIES.is_file():
        return {}, [f"no recording of the model's pass ({LLM_REPLIES.name}): it cannot be locked without "
                    f"one; {RECORD_AGAIN}"]
    replies = json.loads(LLM_REPLIES.read_text(encoding="utf-8"))
    pending, problems = old.get("pending_recording"), []
    if prompt_changed:
        pending, problems = _declare_prompt_change(replies, pending, prompt_changed)
    report = run_extraction_benchmark(llm_replies=replies, pending=pending)
    now = extraction_scores(report)
    identity = report["llm_recording"]
    was = {} if fresh else (old.get("scores") or {})
    history = list(old.get("recordings") or [])
    previous = report["llm_replay"] == "previous"
    if previous and new_recording:
        problems.append("--new-recording: the recording answered the prompt before the declared change; a new "
                        "recording answers today's prompt")
    problems += [f"{k}: locked, and missing from today's run" for k in was if k not in now]
    problems += [f"{k}: {now[k]} < {v}" for k, v in sorted(was.items())
                 if k.startswith("heuristic.") and k in now and now[k] < v]
    locked = old.get("recording") or {}
    same = locked.get("sha256") == identity["sha256"]
    if same and _relabelled(locked, identity):
        # The replies are the ones locked, and the file now says they answer
        # another prompt: a relabelled recording, never a new one.
        problems.append("the recording now says it answered another prompt than the one locked: a recording "
                        f"is not relabelled; {RECORD_AGAIN}")
    if fresh:
        if old.get("recording") and not same:
            history.append({"date": _dt.date.today().isoformat(),
                            "reason": f"a new extraction dataset (v{report['version']})",
                            "replaced": {"recording": old.get("recording"),
                                         "scores": {k: v for k, v in sorted((old.get("scores") or {}).items())
                                                    if k.startswith("llm.")}}})
    elif same:
        if new_recording:
            problems.append("--new-recording: the recording is the one already locked")
        problems += [f"{k}: {now[k]} < {v} on the same recording" for k, v in sorted(was.items())
                     if k.startswith("llm.") and k in now and now[k] < v]
    elif not new_recording:
        problems.append("the recording of the model's pass changed: a new recording is locked only on purpose, "
                        "with lock --new-recording \"why\" (its scores replace the old ones)")
    else:
        history.append({
            "date": _dt.date.today().isoformat(), "reason": new_recording,
            "replaced": {"recording": old.get("recording"),
                         "scores": {k: v for k, v in sorted(was.items()) if k.startswith("llm.")}},
            # The prompt changes this recording measures for the first time.
            **({"answers": old["pending_recording"]} if old.get("pending_recording") else {}),
        })
    return {"version": report["version"], "recording": identity, "scores": now,
            # What each row rests on, for the benchmarks page (decision 6).
            "rests_on": extraction_rests_on(report), "recordings": history,
            # A declared prompt change still waiting for its recording.
            **({"pending_recording": pending} if previous else {})}, problems


def _speakers_lock(old: dict[str, Any], *, fresh: bool = False) -> tuple[dict[str, Any], list[str]]:
    """The speakers case set's part of the lock (Spaces S1-11), and why it is
    refused: a ratchet on the same case set (no score falls; no line shown
    as the asker's that is another member's), reset only by ``--rebaseline``
    when the case set's version changes."""
    from kazma_core.memory.benchmark import SpeakerReplay, load_speakers, run_speaker_benchmark, speaker_scores

    replay = SpeakerReplay(VECTORS, SPEAKER_VECTORS)
    a, b = _patched(replay, replay.model)
    with a, b:
        now = speaker_scores(run_speaker_benchmark())
    was = {} if fresh else (old.get("scores") or {})
    problems = [f"speakers {k}: {now.get(k)} < {v}" for k, v in sorted(was.items())
                if k != "overall.misattributed" and now.get(k, -1.0) < v]
    if now.get("overall.misattributed", 0.0) > was.get("overall.misattributed", now.get("overall.misattributed")):
        problems.append(f"speakers: {now['overall.misattributed']} line(s) now show another member's fact as "
                        "the asker's")
    return {"version": load_speakers()["version"], "scores": now}, problems


def _writes_lock(
    old: dict[str, Any], *, fresh: bool = False, new_recording: str | None = None,
    pending: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The write scenarios' part of the lock, and why it is refused.

    The scenarios replay the extraction recording, so they follow its rules:
    on the same recording a ratchet (a scenario that passed must pass, and the
    list of those that fail is written down, so a pass traded for a failure
    shows); a new recording only with ``--new-recording``, its results
    replacing the old ones; a new version of the scenarios with
    ``--rebaseline``.
    """
    from kazma_core.memory.benchmark import run_write_scenarios, write_scores

    if not LLM_REPLIES.is_file():
        return {}, ["no recording of the model's pass: the write scenarios replay it"]
    report = run_write_scenarios(llm_replies=json.loads(LLM_REPLIES.read_text(encoding="utf-8")), pending=pending)
    now = write_scores(report)
    identity = report["llm_recording"]
    was = {} if fresh else (old.get("scores") or {})
    locked = old.get("recording") or {}
    same = locked.get("sha256") == identity["sha256"]
    problems = [f"writes {k}: locked, and missing from today's run" for k in was if k not in now]
    if same and _relabelled(locked, identity):
        problems.append("writes: the recording now says it answered another prompt than the one locked")
    if fresh or (not same and new_recording):
        pass
    elif not same:
        problems.append("the recording changed: the write scenarios follow it (lock --new-recording \"why\")")
    else:
        problems += [f"writes {k}: {now[k]} < {v}" for k, v in sorted(was.items()) if k in now and now[k] < v]
        newly = sorted(set(report["failing"]) - set(old.get("failing") or []))
        if newly:
            problems.append(f"writes: scenarios that passed now fail: {newly}")
    return {"version": report["version"], "recording": identity, "scores": now,
            "failing": report["failing"]}, problems


def _locked_pending() -> dict[str, Any] | None:
    """The lock's declared prompt change waiting for its recording, if any."""
    if not THRESHOLDS.is_file():
        return None
    return (json.loads(THRESHOLDS.read_text(encoding="utf-8")).get("extraction") or {}).get("pending_recording")


def _cmd_writes() -> int:
    from kazma_core.memory.benchmark import run_write_scenarios

    replies = json.loads(LLM_REPLIES.read_text(encoding="utf-8")) if LLM_REPLIES.is_file() else None
    report = run_write_scenarios(llm_replies=replies, pending=_locked_pending())
    for r in report["results"]:
        print(("pass  " if r["passed"] else "FAIL  ") + r["id"])
        for line in r["problems"]:
            print(f"        {line}")
    print(json.dumps(report["groups"], ensure_ascii=True, indent=1))
    return 0


def _cmd_extraction() -> int:
    from kazma_core.memory.benchmark import run_extraction_benchmark

    # The replies are recorded by scripts/memory_extraction_record.py, which
    # loads an install's .env to reach its model; this script stays env-free.
    replies = json.loads(LLM_REPLIES.read_text(encoding="utf-8")) if LLM_REPLIES.is_file() else None
    print(json.dumps(run_extraction_benchmark(llm_replies=replies, pending=_locked_pending()), ensure_ascii=True,
                     indent=1))
    return 0


#: The pgvector path's search modes (tests/test_memory_benchmark_pg.py MODES).
PGVECTOR_MODES = ("approximate", "exact")


def _measurement_from(text: str) -> dict[str, dict[str, float]]:
    """The pgvector measurement in *text*: the file the test writes
    (``KAZMA_TEST_MEMORY_BENCH_PG_OUT``), or a CI log holding its
    ``PGVECTOR_MEASUREMENT`` line (the last one wins)."""
    lines = [line.split("PGVECTOR_MEASUREMENT ", 1)[1] for line in text.splitlines()
             if "PGVECTOR_MEASUREMENT " in line]
    return json.loads(lines[-1] if lines else text)


def _cmd_lock_pgvector(source: str, run: str | None, mode: str) -> int:
    """Lock the pgvector path's floors from one measurement, naming the run
    it came from (benchmark audit R7, the owner's decision 5): the floors
    become ``verified``, and the test then fails on a difference both ways.
    *mode* is the search the floors hold (the other is kept for the record)."""
    import datetime as _dt

    if not str(run or "").strip():
        print("name the run the floors come from: lock-pgvector --from FILE --run \"<the CI run>\"",
              file=sys.stderr)
        return 1
    try:
        measurement = _measurement_from(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no pgvector measurement in {source}: {exc}", file=sys.stderr)
        return 1
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    missing = [m for m in PGVECTOR_MODES if not isinstance(measurement.get(m), dict)]
    if missing:
        print(f"the measurement has no {', '.join(missing)} run", file=sys.stderr)
        return 1
    floors = {k: float(v) for k, v in measurement[mode].items()}
    if set(floors) != set(locked.get("scores") or {}):
        print("the measurement's scores are not the lock's: another dataset version? run it again",
              file=sys.stderr)
        return 1
    other = next(m for m in PGVECTOR_MODES if m != mode)
    was = (locked.get("pgvector") or {}).get("floors") or {}
    locked["pgvector"] = {
        "status": "verified", "mode": mode, "run": run.strip(), "date": _dt.date.today().isoformat(),
        "measured": f"{run.strip()}, {mode} search", "floors": dict(sorted(floors.items())),
        f"{other}_for_the_record": dict(sorted((measurement[other] or {}).items())),
    }
    THRESHOLDS.write_text(json.dumps(locked, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    moved = [(k, was.get(k), v) for k, v in sorted(floors.items()) if was.get(k) != v]
    print(f"{THRESHOLDS.name}: pgvector floors from {run.strip()} ({mode} search), {len(moved)} changed")
    for k, before, now in moved:
        print(f"  {k}: {before} -> {now}")
    return 0


def _sealed_vectors(questions: list[dict[str, Any]], replay_model: str) -> str | None:
    """Record the sealed questions' vectors with the real model the first
    time (never the memories': they replay from the benchmark's recording).
    Returns why it cannot, or None."""
    from kazma_core.memory.benchmark import ReplayEmbedder, RecordingEmbedder, _text_key

    if SEALED_VECTORS.is_file():
        held = ReplayEmbedder(SEALED_VECTORS)
        if held.model != replay_model:
            return f"{SEALED_VECTORS.name} is {held.model}'s, the benchmark's vectors are {replay_model}'s"
        if all(_text_key(q["text"]) in held._index for q in questions):
            return None
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    _limit_cpu_threads()
    real = get_embedder()
    if real is None:
        return "the sealed questions have no recorded vectors and no embedder is configured (the rag extra)"
    model = get_embedding_model_name()
    if model != replay_model:
        return f"the embedder is {model}, the benchmark's vectors are {replay_model}'s"
    recorder = RecordingEmbedder(real)
    for q in questions:
        recorder.encode(q["text"])
    recorder.save(SEALED_VECTORS, model=model)
    return None


def _cmd_sealed(look: str | None, decided: bool = False) -> int:
    """Score the sealed set: the only way it is scored, and every look is
    logged in the lock (date, reason, commit, the file's hash, the scores). A
    look that decides a change (``--decided``) spends the set: the next look
    is refused until its questions are new (benchmark audit R8, the owner's
    decision 4)."""
    import datetime as _dt
    import hashlib
    import subprocess

    from kazma_core.memory.benchmark import (
        ReplayEmbedder,
        SpeakerReplay,
        gated_scores,
        load_dataset,
        run_benchmark,
        sealed_dataset,
        sealed_problems,
    )

    if not str(look or "").strip():
        print("a look at the sealed set needs its reason: sealed --look \"why\" (every look is logged)",
              file=sys.stderr)
        return 1
    if not SEALED.is_file():
        print(f"no sealed set yet ({SEALED.name}): docs/plans/MEMORY_SEALED_SET.md says who writes it",
              file=sys.stderr)
        return 1
    raw = SEALED.read_bytes()
    sealed = json.loads(raw.decode("utf-8"))
    data = load_dataset()
    problems = sealed_problems(sealed, data)
    if problems:
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print("the sealed set is not the one specified: it is not scored", file=sys.stderr)
        return 1
    file_sha = hashlib.sha256(raw).hexdigest()
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    section = locked.get("sealed") or {}
    looks = list(section.get("looks") or [])
    spent = [x for x in looks if x.get("decided") and x.get("file_sha256") == file_sha]
    if spent:
        print(f"the sealed set is spent: a change was decided by looking at it on {spent[-1]['date']} "
              f"({spent[-1]['reason']}); new questions, written by another session, are needed",
              file=sys.stderr)
        return 1
    replay = ReplayEmbedder(VECTORS)
    why_not = _sealed_vectors(sealed["questions"], replay.model)
    if why_not:
        print(f"cannot score the sealed set: {why_not}", file=sys.stderr)
        return 1
    embedder = SpeakerReplay(VECTORS, SEALED_VECTORS)
    a, b = _patched(embedder, replay.model)
    with a, b:
        report = run_benchmark(sealed_dataset(sealed, data))
    scores = {f"sealed.{k}": v for k, v in gated_scores(report).items()}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO), capture_output=True,
                                text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    looks.append({"date": _dt.date.today().isoformat(), "reason": look.strip(), "decided": bool(decided),
                  "commit": commit, "file_sha256": file_sha, "questions": len(sealed["questions"]),
                  "written_by": sealed.get("written_by"), "scores": dict(sorted(scores.items()))})
    locked["sealed"] = {"file": SEALED.name, "looks": looks}
    THRESHOLDS.write_text(json.dumps(locked, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sealed set: {len(sealed['questions'])} questions, look {len(looks)} logged in {THRESHOLDS.name}"
          + (" (decided: the set is now spent)" if decided else ""))
    for key in ("sealed.overall.hit_rate", "sealed.overall.mrr", "sealed.overall.precision"):
        print(f"  {key}: {scores.get(key)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("vectors")
    lock = sub.add_parser("lock")
    lock.add_argument("--rebaseline", action="store_true",
                      help="a new dataset version: its first scores become the ratchet")
    lock.add_argument("--trade", metavar="REASON",
                      help="lower some scores for a larger gain elsewhere, under TRADE_RULE; recorded")
    lock.add_argument("--new-recording", metavar="REASON",
                      help="lock a new recording of the model's extraction pass; its scores replace the old")
    lock.add_argument("--prompt-changed", metavar="REASON",
                      help="declare a change of the extraction prompt that waits for its recording; recorded")
    lock.add_argument("--edge", nargs=2, action="append", metavar=("QUESTION", "REASON"),
                      help="name a question whose hit flips inside the vectors' rounding (EDGE_RULE); recorded")
    pgv = sub.add_parser("lock-pgvector", help="lock the pgvector path's floors from a named run")
    pgv.add_argument("--from", dest="source", required=True,
                     help="the measurement file, or a CI log holding its PGVECTOR_MEASUREMENT line")
    pgv.add_argument("--run", help="the run the numbers come from (required, recorded)")
    pgv.add_argument("--mode", choices=PGVECTOR_MODES, default="approximate",
                     help="the search the floors hold (the product's is approximate today)")
    sealed = sub.add_parser("sealed", help="score the sealed set; every look is logged in the lock")
    sealed.add_argument("--look", metavar="REASON", help="why this look (required, logged)")
    sealed.add_argument("--decided", action="store_true",
                        help="a change was decided by this look: the set is spent after it")
    sub.add_parser("extraction")
    sub.add_parser("writes")
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
        return _cmd_lock(args.rebaseline, args.trade, args.new_recording,
                         [tuple(e) for e in args.edge] if args.edge else None, args.prompt_changed)
    if args.cmd == "lock-pgvector":
        return _cmd_lock_pgvector(args.source, args.run, args.mode)
    if args.cmd == "sealed":
        return _cmd_sealed(args.look, args.decided)
    if args.cmd == "extraction":
        return _cmd_extraction()
    if args.cmd == "writes":
        return _cmd_writes()
    return _cmd_run(args.real, args.json, args.details)


if __name__ == "__main__":
    raise SystemExit(main())
