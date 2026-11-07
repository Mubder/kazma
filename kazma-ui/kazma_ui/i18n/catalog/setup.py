"""The first-run setup guide (``/setup``, ``kazma_ui/setup_api.py``)."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "setup.title": {"en": "Set up Kazma", "ar": "إعداد كاظمه"},
    "setup.steps_label": {"en": "Setup steps", "ar": "خطوات الإعداد"},
    "setup.step_welcome": {"en": "Welcome", "ar": "الترحيب"},
    "setup.step_model": {"en": "Model", "ar": "النموذج"},
    "setup.step_extras": {"en": "Extras", "ar": "الإضافات"},
    "setup.step_done": {"en": "Done", "ar": "تم"},
    "setup.loading": {"en": "Reading Kazma's setup…", "ar": "جارٍ قراءة إعداد كاظمه…"},
    "setup.load_failed": {"en": "Could not read Kazma's setup.", "ar": "تعذّرت قراءة إعداد كاظمه."},
    "setup.retry": {"en": "Try again", "ar": "حاول مرة أخرى"},
    # Step 1: what Kazma is, in its own words (docs/docs/guide/what-is-kazma.md).
    "setup.welcome_title": {"en": "Welcome to Kazma", "ar": "مرحبًا بك في كاظمه"},
    "setup.welcome_lead": {
        "en": "The self-hosted AI agent that asks before it acts.",
        "ar": "وكيل الذكاء الاصطناعي الذي تستضيفه بنفسك، والذي يستأذنك قبل أن يتصرف.",
    },
    "setup.welcome_body": {
        "en": "Kazma runs on this computer with the AI model you choose. This guide connects that model, "
              "then shows a few optional extras. It takes a few minutes, and everything can be changed "
              "later in Settings.",
        "ar": "تعمل كاظمه على هذا الجهاز بنموذج الذكاء الاصطناعي الذي تختاره. يربط هذا الدليل ذلك النموذج، "
              "ثم يعرض بعض الإضافات الاختيارية. يستغرق بضع دقائق، ويمكن تغيير كل شيء لاحقًا من الإعدادات.",
    },
    "setup.language": {"en": "Language", "ar": "اللغة"},
    "setup.already_ready": {"en": "Kazma is set up and uses:", "ar": "كاظمه جاهزة وتستخدم:"},
    "setup.start": {"en": "Start", "ar": "ابدأ"},
    "setup.change_model": {"en": "Change the model", "ar": "غيّر النموذج"},
    "setup.open_chat": {"en": "Open the chat", "ar": "افتح المحادثة"},
    # Step 2: the model.
    "setup.model_title": {"en": "Where should Kazma's model run?", "ar": "أين يعمل نموذج كاظمه؟"},
    "setup.model_lead": {
        "en": "Kazma thinks with an AI model. Use one from a cloud provider, or one running on this computer.",
        "ar": "تفكّر كاظمه بنموذج ذكاء اصطناعي. استخدم نموذجًا من مزوّد سحابي، أو نموذجًا يعمل على هذا الجهاز.",
    },
    "setup.cloud_title": {"en": "A cloud provider", "ar": "مزوّد سحابي"},
    "setup.cloud_body": {
        "en": "OpenAI, Anthropic, Google Gemini, DeepSeek and others. You need an API key from the provider, "
              "and you pay it for what you use.",
        "ar": "OpenAI وAnthropic وGoogle Gemini وDeepSeek وغيرها. تحتاج إلى مفتاح API من المزوّد، "
              "وتدفع له مقابل ما تستخدمه.",
    },
    "setup.local_title": {"en": "A model on this computer", "ar": "نموذج على هذا الجهاز"},
    "setup.local_body": {
        "en": "Ollama or LM Studio, installed and running here. Nothing leaves this computer and there is "
              "nothing to pay, but it needs a capable computer.",
        "ar": "Ollama أو LM Studio، مثبّت ويعمل هنا. لا يغادر شيء هذا الجهاز ولا شيء تدفعه، "
              "لكنه يحتاج إلى جهاز قوي.",
    },
    "setup.provider": {"en": "Provider", "ar": "المزوّد"},
    "setup.other_providers": {
        "en": "Azure OpenAI, AWS Bedrock and endpoints of your own need more than a key:",
        "ar": "يحتاج Azure OpenAI وAWS Bedrock والعناوين الخاصة بك إلى أكثر من مفتاح:",
    },
    "setup.other_providers_link": {"en": "set them up in Settings → Providers", "ar": "أعدّها من الإعدادات ← المزوّدون"},
    "setup.get_key": {"en": "Get an API key from this provider", "ar": "احصل على مفتاح API من هذا المزوّد"},
    "setup.api_key": {"en": "API key", "ar": "مفتاح API"},
    "setup.key_placeholder": {"en": "Paste the key here", "ar": "الصق المفتاح هنا"},
    "setup.key_hint": {
        "en": "Kazma keeps the key encrypted on this computer and never shows it again. If you saved one "
              "for this provider before, you can leave this empty.",
        "ar": "تحفظ كاظمه المفتاح مشفّرًا على هذا الجهاز ولا تعرضه مرة أخرى. وإن كنت حفظت مفتاحًا لهذا "
              "المزوّد من قبل، فيمكنك ترك الخانة فارغة.",
    },
    "setup.check_key": {"en": "Check the key and list the models", "ar": "تحقّق من المفتاح واعرض النماذج"},
    "setup.local_looking": {
        "en": "Looking for Ollama and LM Studio on this computer…",
        "ar": "جارٍ البحث عن Ollama وLM Studio على هذا الجهاز…",
    },
    "setup.server_running": {"en": "Running", "ar": "يعمل"},
    "setup.server_not_running": {"en": "Not running on this computer", "ar": "لا يعمل على هذا الجهاز"},
    "setup.use_server": {"en": "Use it", "ar": "استخدمه"},
    "setup.download": {"en": "Download it", "ar": "نزّله"},
    "setup.local_hint": {
        "en": "Install one and start it, download a model in it, then look again. In Ollama a model is "
              "downloaded with a command such as the one below; LM Studio also needs its local server "
              "started (Developer, then Start Server).",
        "ar": "ثبّت أحدهما وشغّله، ونزّل فيه نموذجًا، ثم ابحث مرة أخرى. في Ollama يُنزَّل النموذج بأمر مثل "
              "الأمر أدناه؛ ويحتاج LM Studio أيضًا إلى تشغيل خادمه المحلي (Developer ثم Start Server).",
    },
    "setup.look_again": {"en": "Look again", "ar": "ابحث مرة أخرى"},
    "setup.model": {"en": "Model", "ar": "النموذج"},
    "setup.model_hint": {"en": "Pick one from the list, or type its name.", "ar": "اختر واحدًا من القائمة، أو اكتب اسمه."},
    "setup.models_found.zero": {"en": "No models found.", "ar": "لم يُعثر على نماذج."},
    "setup.models_found.one": {"en": "1 model found.", "ar": "عُثر على نموذج واحد."},
    "setup.models_found.two": {"en": "2 models found.", "ar": "عُثر على نموذجين."},
    "setup.models_found.few": {"en": "{n} models found.", "ar": "عُثر على {n} نماذج."},
    "setup.models_found.many": {"en": "{n} models found.", "ar": "عُثر على {n} نموذجًا."},
    "setup.models_found.other": {"en": "{n} models found.", "ar": "عُثر على {n} نموذج."},
    "setup.no_local_models": {
        "en": "This server has no models yet. Download one in it, then look again.",
        "ar": "لا نماذج في هذا الخادم بعد. نزّل واحدًا فيه، ثم ابحث مرة أخرى.",
    },
    "setup.use_model": {"en": "Use this model", "ar": "استخدم هذا النموذج"},
    "setup.listing": {"en": "Asking for the models…", "ar": "جارٍ طلب قائمة النماذج…"},
    "setup.saving": {"en": "Saving…", "ar": "جارٍ الحفظ…"},
    "setup.checking": {"en": "Sending the model a short test message…", "ar": "جارٍ إرسال رسالة اختبار قصيرة إلى النموذج…"},
    "setup.check_ok": {"en": "Kazma got an answer from the model. It is ready.", "ar": "تلقّت كاظمه ردًا من النموذج. إنها جاهزة."},
    "setup.check_failed": {"en": "The model did not answer:", "ar": "لم يُجب النموذج:"},
    "setup.list_failed": {"en": "Could not list the models:", "ar": "تعذّر عرض النماذج:"},
    "setup.save_failed": {"en": "Could not save:", "ar": "تعذّر الحفظ:"},
    "setup.err.api_key_required": {"en": "Enter the provider's API key.", "ar": "أدخل مفتاح API الخاص بالمزوّد."},
    "setup.err.unknown_provider": {"en": "Kazma does not know this provider.", "ar": "لا تعرف كاظمه هذا المزوّد."},
    "setup.err.save_failed": {"en": "The settings could not be saved.", "ar": "تعذّر حفظ الإعدادات."},
    "setup.err.not_configured": {"en": "This provider is not set up yet.", "ar": "هذا المزوّد غير مُعدّ بعد."},
    "setup.err.timeout": {"en": "No answer within a minute.", "ar": "لا رد خلال دقيقة."},
    "setup.err.network": {"en": "Kazma did not answer. Is it still running?", "ar": "لم تُجب كاظمه. هل ما زالت تعمل؟"},
    "setup.back": {"en": "Back", "ar": "رجوع"},
    "setup.next": {"en": "Next", "ar": "التالي"},
    # Step 3: optional extras.
    "setup.extras_title": {"en": "Optional extras", "ar": "إضافات اختيارية"},
    "setup.extras_lead": {
        "en": "Kazma works now. These can wait; each is in Settings whenever you want it.",
        "ar": "كاظمه تعمل الآن. يمكن أن تنتظر هذه، وكل منها في الإعدادات متى أردته.",
    },
    "setup.extra_chat": {"en": "Talk to Kazma from Telegram, Discord or Slack", "ar": "تحدّث مع كاظمه من تيليجرام أو ديسكورد أو سلاك"},
    "setup.extra_chat_body": {
        "en": "Add a chat app's bot; its Test says what is missing.",
        "ar": "أضف بوت تطبيق المحادثة؛ وزر «اختبار» يخبرك بما ينقص.",
    },
    "setup.extra_about": {"en": "Tell Kazma about yourself", "ar": "عرّف كاظمه بنفسك"},
    "setup.extra_about_body": {
        "en": "Memory → About me: a few lines Kazma reads before every answer.",
        "ar": "الذاكرة ← عني: بضعة أسطر تقرؤها كاظمه قبل كل رد.",
    },
    "setup.extra_mail": {"en": "Connect your mail and calendar", "ar": "اربط بريدك وتقويمك"},
    "setup.extra_mail_body": {
        "en": "Gmail, Outlook or any IMAP account, and Google or Outlook Calendar.",
        "ar": "Gmail أو Outlook أو أي حساب IMAP، وتقويم Google أو Outlook.",
    },
    "setup.extra_parts": {"en": "See what was installed", "ar": "اطّلع على ما ثُبّت"},
    "setup.extra_parts_body": {
        "en": "Settings → Packages: each part of Kazma, and how to add the rest.",
        "ar": "الإعدادات ← الحزم: كل جزء من كاظمه، وكيف تضيف الباقي.",
    },
    # Step 4: done.
    "setup.done_title": {"en": "Kazma is ready", "ar": "كاظمه جاهزة"},
    "setup.done_lead": {"en": "Kazma uses this model:", "ar": "تستخدم كاظمه هذا النموذج:"},
    "setup.try_title": {"en": "Try asking:", "ar": "جرّب أن تسأل:"},
    # Worded without addressing Kazma as he or she.
    "setup.try_1": {"en": "What can you do?", "ar": "ما الذي يمكنك فعله؟"},
    "setup.try_2": {"en": "Remind me tomorrow at 9 to call the bank.", "ar": "أريد تذكيرًا غدًا الساعة 9 بالاتصال بالبنك."},
    "setup.try_3": {"en": "Summarize this page: https://kazma.ai", "ar": "أريد ملخّصًا لهذه الصفحة: https://kazma.ai"},
    # Where the guide is opened again (Settings, the page search).
    "setup.reopen": {"en": "Setup guide", "ar": "دليل الإعداد"},
    "setup.reopen_hint": {
        "en": "Choose or change Kazma's model, step by step.",
        "ar": "اختر نموذج كاظمه أو غيّره، خطوة بخطوة.",
    },
}
