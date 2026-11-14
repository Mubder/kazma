"""``settings.alerts`` UI strings: Settings -> Alerts (the unified alerts
plan, Stage 3). ``kazma_ui.i18n`` merges every slice into ``TRANSLATIONS``.
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "settings.tab_alerts": {"en": "Alerts", "ar": "التنبيهات"},
    "settings.alerts.moved_hint": {
        "en": "Where alerts go and the server status messages are in Settings ->",
        "ar": "وجهة التنبيهات ورسائل حالة الخادم في الإعدادات ←",
    },
    "settings.alerts.load_error": {
        "en": "The alert settings could not be read. Reload the page; if it stays, the server log says why.",
        "ar": "تعذّرت قراءة إعدادات التنبيهات. أعد تحميل الصفحة؛ وإن بقي الأمر فسجل الخادم يذكر السبب.",
    },
    "settings.alerts.save_failed": {"en": "Not saved: {error}", "ar": "لم يُحفظ: {error}"},
    # 1. Where Kazma's alerts go
    "settings.alerts.routes_title": {"en": "Where Kazma's alerts go", "ar": "وجهة تنبيهات كاظمه"},
    "settings.alerts.routes_sub": {
        "en": "The chat apps that get problems and recoveries, from Kazma and its guard's start cards.",
        "ar": "تطبيقات المحادثة التي تصلها المشكلات وعودة الأمور إلى طبيعتها من كاظمه وبطاقات بدء التشغيل.",
    },
    "settings.alerts.routes_none": {
        "en": "None chosen: alerts go to every chat app that is set up.",
        "ar": "لم تختر شيئًا: تذهب التنبيهات إلى كل تطبيق محادثة مُعدّ.",
    },
    "settings.alerts.route_not_set_up": {"en": "Not set up yet", "ar": "غير مُعدّ بعد"},
    "settings.alerts.routes_setup": {
        "en": "A chat app not set up yet is set up in Providers & Connectors ->",
        "ar": "يُعدّ تطبيق المحادثة غير المُعدّ في المزوّدون والموصلات ←",
    },
    "settings.alerts.route_telegram": {"en": "Your chat with Kazma", "ar": "محادثتك مع كاظمه"},
    "settings.alerts.route_group": {"en": "The group Kazma posts to", "ar": "المجموعة التي ينشر فيها كاظمه"},
    "settings.alerts.route_alerts_group": {"en": "Your alerts group (below)", "ar": "مجموعة تنبيهاتك (أدناه)"},
    "settings.alerts.group_chat": {"en": "Alerts group (Telegram)", "ar": "مجموعة التنبيهات (Telegram)"},
    "settings.alerts.group_chat_hint": {
        "en": "Where the Telegram (group) route sends Kazma's alerts: the group's chat id, which Kazma posts when you add its bot to the group (it starts with -100). Empty: the swarm's output group. Put the same id in the guard's chat below and in Kazma Watch to get every alert in one place.",
        "ar": "حيث يرسل مسار Telegram (مجموعة) تنبيهات كاظمه: معرّف محادثة المجموعة، وينشره كاظمه حين تضيف بوته إلى المجموعة (يبدأ بـ ‎-100‎). فارغًا: مجموعة مخرجات السرب. ضع المعرّف نفسه في محادثة الحارس أدناه وفي المراقب لتصلك كل التنبيهات في مكان واحد.",
    },
    # Which Telegram bot sends the alerts (ops_alerts.ALERTS_BOT_KEY).
    "settings.alerts.bot_label": {"en": "Telegram bot that sends them", "ar": "بوت Telegram الذي يرسلها"},
    "settings.alerts.bot_chat_bot": {
        "en": "Kazma's chat bot (the one you talk to)",
        "ar": "بوت كاظمه للمحادثة (الذي تتحدث معه)",
    },
    "settings.alerts.bot_alerts_bot": {"en": "A separate bot for alerts", "ar": "بوت منفصل للتنبيهات"},
    "settings.alerts.bot_hint": {
        "en": "A separate bot keeps alerts out of your chats with Kazma. It can be Kazma Watch's bot: then every alert, from Kazma, its guard and Kazma Watch, arrives in one chat. The guard pages with it too.",
        "ar": "البوت المنفصل يُبعد التنبيهات عن محادثاتك مع كاظمه. ويمكن أن يكون بوت المراقب: فتصلك كل التنبيهات، من كاظمه وحارسه والمراقب، في محادثة واحدة. ويرسل الحارس عبره أيضًا.",
    },
    "settings.alerts.bot_token": {"en": "Alerts bot token", "ar": "رمز بوت التنبيهات"},
    "settings.alerts.bot_token_hint": {
        "en": "From BotFather. Kept in Kazma's vault and never shown again; leave it empty to keep the saved one.",
        "ar": "من BotFather. يُحفظ في خزنة كاظمه ولا يُعرض مرة أخرى؛ اتركه فارغًا لتبقي المحفوظ.",
    },
    "settings.alerts.bot_token_saved": {
        "en": "Saved; type a new one to replace it",
        "ar": "محفوظ؛ اكتب رمزًا جديدًا لتستبدله",
    },
    "settings.alerts.bot_chat": {"en": "Chat for alerts", "ar": "محادثة التنبيهات"},
    "settings.alerts.bot_chat_hint": {
        "en": "A number (your own chat's, or -100... for a group) or a public @channel. Send the bot /start from your account first, or add it to the group.",
        "ar": "رقم (رقم محادثتك، أو ‎-100...‎ لمجموعة) أو ‎@قناة‎ عامة. أرسل إلى البوت ‎/start‎ من حسابك أولًا، أو أضفه إلى المجموعة.",
    },
    "settings.alerts.route_alerts_bot": {"en": "The alerts bot's chat", "ar": "محادثة بوت التنبيهات"},
    "settings.alerts.route_name_alerts_bot": {"en": "the alerts bot", "ar": "بوت التنبيهات"},
    "settings.alerts.bot_saved_ok": {"en": "Alerts now come from {bot}.", "ar": "تصل التنبيهات الآن من {bot}."},
    "settings.alerts.bot_saved_chat_unseen": {
        "en": "Saved, but {bot} cannot see that chat yet: send it /start from your account, or add it to the group, then send a test alert.",
        "ar": "حُفظ، لكن {bot} لا يرى تلك المحادثة بعد: أرسل إليه ‎/start‎ من حسابك، أو أضفه إلى المجموعة، ثم أرسل تنبيهًا تجريبيًا.",
    },
    "settings.alerts.bot_saved_unchecked": {
        "en": "Saved. Telegram could not be asked about the bot just now: send a test alert to check it.",
        "ar": "حُفظ. تعذّر سؤال Telegram عن البوت الآن: أرسل تنبيهًا تجريبيًا لتتحقق منه.",
    },
    "settings.alerts.bot_refused": {
        "en": "Not saved: Telegram does not know that bot token. Copy it again from BotFather.",
        "ar": "لم يُحفظ: لا يعرف Telegram رمز البوت هذا. انسخه مرة أخرى من BotFather.",
    },
    "settings.alerts.guard_via_alerts_bot": {
        "en": "The alerts bot, to the alerts chat above",
        "ar": "بوت التنبيهات، إلى محادثة التنبيهات أعلاه",
    },
    "settings.alerts.guard_chat_by_bot": {
        "en": "With a separate alerts bot, the guard pages from it, to the alerts chat above.",
        "ar": "مع بوت تنبيهات منفصل، يرسل الحارس عبره إلى محادثة التنبيهات أعلاه.",
    },
    "settings.alerts.route_discord": {"en": "The Discord channel Kazma posts to", "ar": "قناة Discord التي ينشر فيها كاظمه"},
    "settings.alerts.route_slack": {"en": "The Slack channel Kazma posts to", "ar": "قناة Slack التي ينشر فيها كاظمه"},
    "settings.alerts.test_alert_failed": {
        "en": "Not delivered to {routes}: its Test button (Providers & Connectors) says why.",
        "ar": "لم يصل إلى {routes}: زر «اختبار» الخاص به (المزوّدون والموصلات) يوضح السبب.",
    },
    "settings.alerts.test_alert_bot_failed": {
        "en": "The alerts bot did not take it, so Kazma's chat bot sent it. Check the bot's token, and that the bot can see its chat.",
        "ar": "لم يستقبله بوت التنبيهات، فأرسله بوت كاظمه للمحادثة. تحقق من رمز البوت، ومن أن البوت يرى محادثته.",
    },
    "settings.alerts.test_alert_nowhere": {
        "en": "No alert route sends anywhere. Set up a chat app in Providers & Connectors, then save a route here.",
        "ar": "لا يرسل أي مسار تنبيهات إلى أي مكان. أعِدّ تطبيق محادثة في المزوّدون والموصلات، ثم احفظ مسارًا هنا.",
    },
    # 3. The guard's pager
    "settings.alerts.guard_title": {"en": "The guard's pager", "ar": "منبّه الحارس"},
    "settings.alerts.guard_sub": {
        "en": "Pages you on Telegram when Kazma itself is down.",
        "ar": "يُرسل إليك عبر Telegram حين يتوقف كاظمه نفسه.",
    },
    "settings.alerts.guard_bot": {"en": "Bot", "ar": "البوت"},
    "settings.alerts.guard_bot_ready": {"en": "Kazma's Telegram bot", "ar": "بوت كاظمه في Telegram"},
    "settings.alerts.guard_bot_missing": {
        "en": "No Telegram bot yet (Providers & Connectors -> Platform connectors)",
        "ar": "لا يوجد بوت Telegram بعد (المزوّدون والموصلات ← موصلات المنصات)",
    },
    "settings.alerts.guard_running": {"en": "Guard", "ar": "الحارس"},
    "settings.alerts.guard_is_running": {"en": "Running", "ar": "يعمل"},
    "settings.alerts.guard_is_silent": {
        "en": "Not running (no heartbeat for a minute)",
        "ar": "لا يعمل (لا نبضة منذ دقيقة)",
    },
    "settings.alerts.guard_none": {
        "en": "No record: Kazma was not started by its guard",
        "ar": "لا سجل: لم يشغّل الحارسُ كاظمه",
    },
    "settings.alerts.guard_chat": {"en": "Telegram chat", "ar": "محادثة Telegram"},
    "settings.alerts.guard_chat_hint": {
        "en": "The chat the guard pages: a number (like 123456789, or -100... for a group) or a public @name. Kazma's own alerts use it too when no chat app can take them.",
        "ar": "المحادثة التي يرسل إليها الحارس: رقم (مثل 123456789، أو ‎-100...‎ لمجموعة) أو ‎@اسم‎ عام. وتستخدمها تنبيهات كاظمه أيضًا حين لا يستطيع أي تطبيق محادثة استقبالها.",
    },
    "settings.alerts.guard_chat_fallback": {
        "en": "Empty: the guard pages the swarm group chat ({chat}).",
        "ar": "فارغ: يرسل الحارس إلى محادثة مجموعة السرب ({chat}).",
    },
    "settings.alerts.guard_env": {
        "en": "The guard's environment sets its chat (KAZMA_GUARD_TELEGRAM_CHAT): this setting does not change where it pages.",
        "ar": "بيئة الحارس تحدد محادثته (KAZMA_GUARD_TELEGRAM_CHAT): هذا الإعداد لا يغيّر وجهة رسائله.",
    },
    "settings.alerts.guard_no_page": {"en": "No page since the guard started.", "ar": "لا رسالة منذ بدء الحارس."},
    "settings.alerts.guard_last_ok": {"en": "Last page delivered {when}.", "ar": "وصلت آخر رسالة {when}."},
    "settings.alerts.guard_last_held": {
        "en": "Last page not sent {when}: the guard could not read its bot or chat, so it holds the page until it can.",
        "ar": "لم تُرسل آخر رسالة {when}: لم يستطع الحارس قراءة البوت أو المحادثة، فهو يحتفظ بالرسالة حتى يستطيع.",
    },
    "settings.alerts.guard_last_failed": {
        "en": "Last page not delivered {when}: {error}",
        "ar": "لم تصل آخر رسالة {when}: {error}",
    },
    "settings.alerts.test_page": {"en": "Send a test page", "ar": "إرسال رسالة تجريبية"},
    "settings.alerts.test_page_sending": {"en": "Sending…", "ar": "جارٍ الإرسال…"},
    "settings.alerts.guard_test_waiting": {
        "en": "Asked the guard; waiting for its answer…",
        "ar": "طُلب من الحارس؛ بانتظار رده…",
    },
    "settings.alerts.guard_test_no_guard": {
        "en": "Kazma was not started by its guard, so nothing can send the test page. Start Kazma through its guard.",
        "ar": "لم يشغّل الحارسُ كاظمه، فلا شيء يرسل الرسالة التجريبية. شغّل كاظمه عبر حارسه.",
    },
    "settings.alerts.guard_test_silent": {
        "en": "The guard is not running (no heartbeat for a minute).",
        "ar": "الحارس لا يعمل (لا نبضة منذ دقيقة).",
    },
    "settings.alerts.guard_test_too_old": {
        "en": "The running guard is older than test pages: restart the guard once (Restart the guard, in the operations guide).",
        "ar": "الحارس العامل أقدم من ميزة الرسائل التجريبية: أعد تشغيل الحارس مرة واحدة («أعد تشغيل الحارس» في دليل التشغيل).",
    },
    "settings.alerts.guard_test_not_written": {
        "en": "The request could not be written beside the guard's files.",
        "ar": "تعذّرت كتابة الطلب بجانب ملفات الحارس.",
    },
    "settings.alerts.guard_test_failed": {
        "en": "The test page was not asked for: {error}",
        "ar": "لم يُطلب إرسال الرسالة التجريبية: {error}",
    },
    "settings.alerts.guard_test_sent": {
        "en": "The guard sent the test page to chat {chat}. Check Telegram.",
        "ar": "أرسل الحارس الرسالة التجريبية إلى المحادثة {chat}. تحقّق من Telegram.",
    },
    "settings.alerts.guard_test_expired": {
        "en": "The guard took the request too late and did not send it. Try again.",
        "ar": "تلقّى الحارس الطلب متأخرًا فلم يرسله. حاول مجددًا.",
    },
    "settings.alerts.guard_test_unanswered": {
        "en": "The guard did not answer. Is it running? Check its log (guard.log).",
        "ar": "لم يرد الحارس. هل يعمل؟ راجع سجله (guard.log).",
    },
    "settings.alerts.guard_test_not_sent": {
        "en": "The guard could not send the test page: {error}",
        "ar": "تعذّر على الحارس إرسال الرسالة التجريبية: {error}",
    },
    # 4. Kazma Watch
    "settings.alerts.watch_title": {"en": "Kazma Watch", "ar": "مراقب كاظمه"},
    "settings.alerts.watch_not_paired": {
        "en": "Not paired: nothing outside this machine is watching it. Paste Kazma Watch's pairing token once to pair.",
        "ar": "غير مربوط: لا شيء خارج هذا الجهاز يراقبه. الصق رمز الربط الخاص بالمراقب مرة واحدة للربط.",
    },
    "settings.alerts.watch_setup_hint": {
        "en": "No Kazma Watch yet? Run this on the computer Kazma runs on: it puts one on your Cloudflare "
              "account (the free plan is enough) and pairs this install with it.",
        "ar": "ليس لديك Kazma Watch بعد؟ شغّل هذا الأمر على الجهاز الذي يعمل عليه Kazma: يضع واحدًا في حسابك على "
              "Cloudflare (تكفي الخطة المجانية) ويربط هذا التثبيت به.",
    },
    "settings.alerts.watch_token": {"en": "Pairing token", "ar": "رمز الربط"},
    "settings.alerts.watch_token_hint": {
        "en": "Kazma Watch's PAIRING_TOKEN. Used once to pair and never kept.",
        "ar": "رمز PAIRING_TOKEN الخاص بالمراقب. يُستخدم مرة واحدة للربط ولا يُحفظ.",
    },
    "settings.alerts.watch_url": {"en": "Kazma Watch's address", "ar": "عنوان المراقب"},
    "settings.alerts.watch_pair": {"en": "Pair", "ar": "ربط"},
    "settings.alerts.watch_pairing": {"en": "Pairing…", "ar": "جارٍ الربط…"},
    "settings.alerts.watch_paired": {
        "en": "Paired. The guard sends its first heartbeat within a minute.",
        "ar": "تم الربط. يرسل الحارس أول نبضة خلال دقيقة.",
    },
    "settings.alerts.watch_paired_untold": {
        "en": "Paired, but Kazma Watch was not told the time zone yet: press \"Tell Kazma Watch\".",
        "ar": "تم الربط، لكن المراقب لم يُبلَّغ بالمنطقة الزمنية بعد: اضغط «إبلاغ المراقب».",
    },
    "settings.alerts.watch_zone": {"en": "Times on its cards", "ar": "الأوقات في بطاقاته"},
    "settings.alerts.watch_zone_untold": {
        "en": "Not told yet: its cards use UTC. Press \"Tell Kazma Watch\".",
        "ar": "لم يُبلَّغ بعد: بطاقاته بتوقيت UTC. اضغط «إبلاغ المراقب».",
    },
    "settings.alerts.watch_zone_stale": {
        "en": "{zone} (Kazma's is now {ours}: press \"Tell Kazma Watch\")",
        "ar": "{zone} (منطقة كاظمه الآن {ours}: اضغط «إبلاغ المراقب»)",
    },
    "settings.alerts.watch_email": {"en": "Email the pages to", "ar": "أرسل الرسائل بالبريد إلى"},
    "settings.alerts.watch_email_hint": {
        "en": "Used once Kazma Watch's email channel is set up. Leave empty for none.",
        "ar": "يُستخدم حين تُعدّ قناة البريد لدى المراقب. اتركه فارغًا إن لم ترده.",
    },
    "settings.alerts.watch_tell": {"en": "Tell Kazma Watch", "ar": "إبلاغ المراقب"},
    "settings.alerts.watch_told": {
        "en": "Kazma Watch now writes times in {zone}.",
        "ar": "صار المراقب يكتب الأوقات بتوقيت {zone}.",
    },
    "settings.alerts.watch_unpair": {"en": "Unpair", "ar": "فك الربط"},
    "settings.alerts.watch_unpair_title": {
        "en": "Stop Kazma Watch watching this install?",
        "ar": "إيقاف مراقبة المراقب لهذا التثبيت؟",
    },
    "settings.alerts.watch_unpair_message": {
        "en": "Kazma Watch stops checking this machine and paging you about it. You can pair again later.",
        "ar": "يتوقف المراقب عن فحص هذا الجهاز وعن مراسلتك بشأنه. يمكنك الربط مجددًا لاحقًا.",
    },
    "settings.alerts.watch_unpair_forget": {
        "en": "Also delete what Kazma Watch kept about it (its incidents and uptime)",
        "ar": "احذف أيضًا ما احتفظ به المراقب عنه (حوادثه ومدة تشغيله)",
    },
    "settings.alerts.watch_unpaired": {
        "en": "Unpaired. Kazma Watch no longer watches this install.",
        "ar": "فُكّ الربط. لم يعد المراقب يراقب هذا التثبيت.",
    },
    "settings.alerts.watch_test_result": {
        "en": "Kazma Watch sent a test page. Telegram: {telegram}. Email: {email}.",
        "ar": "أرسل المراقب رسالة تجريبية. Telegram: {telegram}. البريد: {email}.",
    },
    "settings.alerts.channel_sent": {"en": "took it", "ar": "استلمها"},
    "settings.alerts.channel_failed": {"en": "refused it", "ar": "رفضها"},
    "settings.alerts.channel_off": {"en": "not set up", "ar": "غير مُعدّ"},
    "settings.alerts.channel_failed_because": {"en": "refused it: {reason}", "ar": "رفضها: {reason}"},
    "settings.alerts.channel_off_no_address": {
        "en": "no address yet: enter one above and press Save",
        "ar": "لا عنوان بعد: اكتبه أعلاه واضغط حفظ",
    },
    "settings.alerts.email_reason_sender": {
        "en": "alerts.kazma.ai is not verified for sending in Cloudflare yet",
        "ar": "لم يُوثَّق alerts.kazma.ai للإرسال في Cloudflare بعد",
    },
    "settings.alerts.email_reason_recipient": {
        "en": "Cloudflare does not allow sending to that address",
        "ar": "لا يسمح Cloudflare بالإرسال إلى هذا العنوان",
    },
    "settings.alerts.email_reason_suppressed": {
        "en": "that address bounced before, so Cloudflare blocks it",
        "ar": "ارتدّت رسالة إلى هذا العنوان من قبل، فيحجبه Cloudflare",
    },
    "settings.alerts.email_reason_limit": {
        "en": "Cloudflare's sending limit is reached",
        "ar": "بلغ الإرسال حدّ Cloudflare",
    },
    "settings.alerts.email_reason_other": {
        "en": "Cloudflare refused it ({code})",
        "ar": "رفضها Cloudflare ({code})",
    },
    "settings.alerts.watch_err_bad_token": {
        "en": "Kazma Watch refused the pairing token. Paste its PAIRING_TOKEN again.",
        "ar": "رفض المراقب رمز الربط. الصق رمز PAIRING_TOKEN الخاص به مجددًا.",
    },
    "settings.alerts.watch_err_bad_url": {
        "en": "Kazma Watch's address must start with https://.",
        "ar": "يجب أن يبدأ عنوان المراقب بـ https://.",
    },
    "settings.alerts.watch_err_unreachable": {
        "en": "Kazma Watch did not answer. Check the address and try again.",
        "ar": "لم يرد المراقب. تحقّق من العنوان وحاول مجددًا.",
    },
    "settings.alerts.watch_err_refused": {"en": "Kazma Watch refused the request.", "ar": "رفض المراقب الطلب."},
    "settings.alerts.watch_err_not_restricted": {
        "en": "The pairing file could not be kept to this account, so the key was not written.",
        "ar": "تعذّر قصر ملف الربط على هذا الحساب، فلم يُكتب المفتاح.",
    },
    "settings.alerts.watch_err_not_paired": {"en": "This install is not paired.", "ar": "هذا التثبيت غير مربوط."},
    "settings.alerts.watch_err_too_soon": {
        "en": "A test page was just sent. Wait 20 seconds.",
        "ar": "أُرسلت رسالة تجريبية للتو. انتظر 20 ثانية.",
    },
    "settings.alerts.watch_err_bad_timezone": {
        "en": "Kazma Watch does not know Kazma's time zone. Check it in Settings -> Memory.",
        "ar": "لا يعرف المراقب المنطقة الزمنية لكاظمه. راجعها في الإعدادات ← الذاكرة.",
    },
    "settings.alerts.watch_err_bad_email": {
        "en": "The email address was refused: it looks like name@example.com.",
        "ar": "رُفض عنوان البريد: يكون بالشكل name@example.com.",
    },
    "settings.alerts.watch_err_other": {"en": "Kazma Watch said: {code}", "ar": "قال المراقب: {code}"},
    # 5. Notices
    "settings.alerts.notices_title": {"en": "Notices", "ar": "الإشعارات"},
    "settings.alerts.notices_sub": {
        "en": "Good news and information: a crawl finished, a check passing again.",
        "ar": "الأخبار الجيدة والمعلومات: اكتمل زحف، أو عاد فحص ينجح.",
    },
    "settings.alerts.notices_label": {"en": "Send notices", "ar": "إرسال الإشعارات"},
    "settings.alerts.notices_chats": {
        "en": "To the chat apps too",
        "ar": "إلى تطبيقات المحادثة أيضًا",
    },
    "settings.alerts.notices_bell": {
        "en": "Only to the bell and the daily digest",
        "ar": "إلى الجرس والملخص اليومي فقط",
    },
    "settings.alerts.notices_hint": {
        "en": "Problems always go to the chat apps, and so does the note that one is over.",
        "ar": "المشكلات تذهب دائمًا إلى تطبيقات المحادثة، وكذلك رسالة انتهائها.",
    },
    # 6. Recent alerts
    "settings.alerts.recent_title": {"en": "Recent alerts", "ar": "التنبيهات الأخيرة"},
    "settings.alerts.recent_sub": {
        "en": "The last 7 days, from Kazma, its guard and Kazma Watch.",
        "ar": "آخر 7 أيام، من كاظمه وحارسه والمراقب.",
    },
    "settings.alerts.refresh": {"en": "Refresh", "ar": "تحديث"},
    "settings.alerts.recent_failed": {
        "en": "The recent alerts could not be read.",
        "ar": "تعذّرت قراءة التنبيهات الأخيرة.",
    },
    "settings.alerts.recent_none": {
        "en": "No alerts in the last 7 days.",
        "ar": "لا تنبيهات في آخر 7 أيام.",
    },
    "settings.alerts.recent_partial": {
        "en": "Could not read the alerts of: {names}.",
        "ar": "تعذّرت قراءة تنبيهات: {names}.",
    },
    "settings.alerts.col_when": {"en": "When", "ar": "الوقت"},
    "settings.alerts.col_sender": {"en": "From", "ar": "المرسل"},
    "settings.alerts.col_alert": {"en": "Alert", "ar": "التنبيه"},
    "settings.alerts.col_taken": {"en": "Delivered", "ar": "الوصول"},
    "settings.alerts.source_guard": {"en": "Guard", "ar": "الحارس"},
    "settings.alerts.source_watch": {"en": "Kazma Watch", "ar": "مراقب كاظمه"},
    "settings.alerts.sev_info": {"en": "Info", "ar": "معلومة"},
    "settings.alerts.sev_success": {"en": "Good news", "ar": "خبر جيد"},
    "settings.alerts.sev_warn": {"en": "Warning", "ar": "تحذير"},
    "settings.alerts.sev_error": {"en": "Error", "ar": "خطأ"},
    "settings.alerts.sev_critical": {"en": "Critical", "ar": "حرج"},
    "settings.alerts.taken_yes": {"en": "Delivered", "ar": "وصل"},
    "settings.alerts.taken_no": {"en": "Not delivered", "ar": "لم يصل"},
    "settings.alerts.taken_kept": {"en": "Bell and digest only", "ar": "الجرس والملخص فقط"},
    "settings.alerts.taken_unknown": {"en": "Unknown", "ar": "غير معروف"},
    # A repeat the cooldown held back, counted on the alert before it.
    "settings.alerts.held.zero": {"en": "no repeats held back", "ar": "لا تكرارات محجوبة"},
    "settings.alerts.held.one": {"en": "{n} repeat held back", "ar": "تكرار واحد محجوب"},
    "settings.alerts.held.two": {"en": "{n} repeats held back", "ar": "تكراران محجوبان"},
    "settings.alerts.held.few": {"en": "{n} repeats held back", "ar": "{n} تكرارات محجوبة"},
    "settings.alerts.held.many": {"en": "{n} repeats held back", "ar": "{n} تكرارًا محجوبًا"},
    "settings.alerts.held.other": {"en": "{n} repeats held back", "ar": "{n} تكرار محجوب"},
}
