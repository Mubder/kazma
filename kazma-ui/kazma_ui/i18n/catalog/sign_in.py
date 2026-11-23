"""Stronger sign-in (Spaces S4-02): a second factor (My account and the
sign-in page), each person's sessions and invitation links (People), and
the invitation page."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── The sign-in page's code step ──────────────────────────────────────
    "login.code": {"en": "Code", "ar": "الرمز"},
    "login.code_help": {
        "en": "Enter the code from your authenticator app, or one of your recovery codes.",
        "ar": "أدخل الرمز من تطبيق المصادقة، أو أحد رموز الاسترداد.",
    },
    "login.verify": {"en": "Sign in", "ar": "تسجيل الدخول"},
    "login.code_wrong": {"en": "That code is not right.", "ar": "هذا الرمز غير صحيح."},
    "login.code_paused": {
        "en": "Too many wrong codes: codes for this account are not checked for a while. Try again later.",
        "ar": "رموز خاطئة كثيرة: لا تُفحص رموز هذا الحساب لفترة. حاول لاحقًا.",
    },
    "login.secret_in_browser": {
        "en": "The install's secret is a key for programs. In a browser, sign in here with it and your code.",
        "ar": "سرّ التثبيت مفتاح للبرامج. في المتصفح، سجّل الدخول هنا به وبرمزك.",
    },
    "login.everyone_signs_in": {
        "en": "More than one person has an account on this Kazma, so everyone signs in, the owner too, "
              "on this computer as anywhere. The owner signs in with their password or the install's "
              "secret (KAZMA_SECRET in its .env).",
        "ar": "لأكثر من شخص حساب على Kazma هذا، لذلك يسجّل الجميع الدخول، والمالك أيضًا، على هذا "
              "الحاسوب كما في أي مكان. يسجّل المالك الدخول بكلمة مروره أو بسرّ التثبيت "
              "(KAZMA_SECRET في ملف .env الخاص به).",
    },

    # ── The invitation page ───────────────────────────────────────────────
    "invite.page_title": {"en": "Invitation · Kazma", "ar": "دعوة · Kazma"},
    "invite.checking": {"en": "Checking the link…", "ar": "جارٍ التحقق من الرابط…"},
    "invite.title": {"en": "You're invited to Kazma", "ar": "أنت مدعو إلى Kazma"},
    "invite.blurb": {
        "en": "Choose the name you sign in with and a password. Your account gets a space of its own.",
        "ar": "اختر الاسم الذي تسجّل به الدخول وكلمة مرور. يحصل حسابك على مساحة خاصة به.",
    },
    "invite.name": {"en": "Name", "ar": "الاسم"},
    "invite.password": {"en": "Password", "ar": "كلمة المرور"},
    "invite.password_hint": {"en": "At least 8 characters", "ar": "8 أحرف على الأقل"},
    "invite.confirm": {"en": "Password again", "ar": "كلمة المرور مرة أخرى"},
    "invite.create": {"en": "Create my account", "ar": "أنشئ حسابي"},
    "invite.done": {
        "en": "Your account is ready. Sign in with the name and password you chose.",
        "ar": "حسابك جاهز. سجّل الدخول بالاسم وكلمة المرور اللذين اخترتهما.",
    },
    "invite.sign_in": {"en": "Sign in", "ar": "تسجيل الدخول"},
    "invite.closed": {
        "en": "This invitation does not work any more. Ask for a new one.",
        "ar": "هذه الدعوة لم تعد تعمل. اطلب دعوة جديدة.",
    },
    "invite.err_mismatch": {"en": "The passwords are not the same.", "ar": "كلمتا المرور غير متطابقتين."},
    "invite.err_weak_password": {"en": "A password is at least 8 characters.", "ar": "كلمة المرور 8 أحرف على الأقل."},
    "invite.err_bad_name": {"en": "A name is 1 to 64 characters.", "ar": "الاسم من حرف واحد إلى 64 حرفًا."},
    "invite.err_name_taken": {"en": "That name is taken. Choose another.", "ar": "هذا الاسم مستخدم. اختر اسمًا آخر."},
    "invite.err_not_ready": {
        "en": "This Kazma is not ready for a new person yet. Ask whoever invited you.",
        "ar": "Kazma هذا غير جاهز لشخص جديد بعد. اسأل من دعاك.",
    },
    "invite.err_failed": {"en": "That did not work. Try again.", "ar": "لم ينجح ذلك. حاول مرة أخرى."},

    # ── My account: the second factor ────────────────────────────────────
    "sign_in.title": {"en": "Second factor", "ar": "عامل التحقق الثاني"},
    "sign_in.intro": {
        "en": "After your password, Kazma asks for a code from an authenticator app on your phone. "
              "Someone who learns your password still cannot sign in.",
        "ar": "بعد كلمة المرور، يطلب Kazma رمزًا من تطبيق مصادقة على هاتفك. "
              "من يعرف كلمة مرورك لا يستطيع تسجيل الدخول مع ذلك.",
    },
    "sign_in.load_failed": {"en": "Your second factor cannot be read right now.", "ar": "لا يمكن قراءة عامل التحقق الثاني الآن."},
    "sign_in.on": {"en": "On", "ar": "مفعّل"},
    "sign_in.off": {"en": "Off", "ar": "غير مفعّل"},
    "sign_in.turn_on": {"en": "Turn on", "ar": "تفعيل"},
    "sign_in.scan_or_type": {
        "en": "Add this key to your authenticator app (open the link on your phone, or type the key), "
              "then enter the code it shows. The key is shown once.",
        "ar": "أضف هذا المفتاح إلى تطبيق المصادقة (افتح الرابط على هاتفك، أو اكتب المفتاح)، "
              "ثم أدخل الرمز الذي يظهره. يظهر المفتاح مرة واحدة.",
    },
    "sign_in.open_in_app": {"en": "Open in the app", "ar": "افتح في التطبيق"},
    "sign_in.first_code": {"en": "Code from the app", "ar": "الرمز من التطبيق"},
    "sign_in.confirm": {"en": "Turn on", "ar": "تفعيل"},
    "sign_in.recovery_once": {
        "en": "Your recovery codes, shown once. Keep them somewhere safe: each signs in once if you lose your phone.",
        "ar": "رموز الاسترداد، تظهر مرة واحدة. احفظها في مكان آمن: كل رمز يسجّل الدخول مرة واحدة إن فقدت هاتفك.",
    },
    "sign_in.copy_codes": {"en": "Copy the codes", "ar": "انسخ الرموز"},
    "sign_in.codes_kept": {"en": "I kept them", "ar": "حفظتها"},
    "sign_in.off_code": {"en": "A code to turn it off", "ar": "رمز لإيقافه"},
    "sign_in.turn_off": {"en": "Turn off", "ar": "إيقاف"},
    "sign_in.on_since": {"en": "On since {when}.", "ar": "مفعّل منذ {when}."},
    "sign_in.recovery_left.zero": {"en": "No recovery codes left.", "ar": "لم يتبقَّ أي رمز استرداد."},
    "sign_in.recovery_left.one": {"en": "1 recovery code left.", "ar": "تبقّى رمز استرداد واحد."},
    "sign_in.recovery_left.two": {"en": "{n} recovery codes left.", "ar": "تبقّى رمزا استرداد."},
    "sign_in.recovery_left.few": {"en": "{n} recovery codes left.", "ar": "تبقّت {n} رموز استرداد."},
    "sign_in.recovery_left.many": {"en": "{n} recovery codes left.", "ar": "تبقّى {n} رمز استرداد."},
    "sign_in.recovery_left.other": {"en": "{n} recovery codes left.", "ar": "تبقّى {n} رمز استرداد."},
    "sign_in.turned_on": {"en": "Your second factor is on.", "ar": "تم تفعيل عامل التحقق الثاني."},
    "sign_in.turned_off": {"en": "Your second factor is off.", "ar": "تم إيقاف عامل التحقق الثاني."},
    "sign_in.copied": {"en": "Copied", "ar": "تم النسخ"},
    "sign_in.err_wrong_code": {"en": "That code is not right.", "ar": "هذا الرمز غير صحيح."},
    "sign_in.err_already_on": {"en": "A second factor is on already.", "ar": "عامل التحقق الثاني مفعّل بالفعل."},
    "sign_in.err_not_on": {"en": "No second factor is on.", "ar": "لا يوجد عامل تحقق ثانٍ مفعّل."},
    "sign_in.err_nothing_begun": {"en": "Start again: no second factor is waiting.", "ar": "ابدأ من جديد: لا يوجد عامل تحقق ثانٍ بالانتظار."},
    "sign_in.err_too_long_ago": {"en": "That was too long ago. Start again.", "ar": "مرّ وقت طويل. ابدأ من جديد."},
    "sign_in.err_no_vault": {
        "en": "A second factor is kept in the vault, and this install has none.",
        "ar": "يُحفظ عامل التحقق الثاني في الخزنة، وهذا التثبيت ليس فيه خزنة.",
    },
    "sign_in.err_own_sign_in": {"en": "Set this from your own sign-in.", "ar": "اضبط هذا من تسجيل دخولك أنت."},
    "sign_in.err_unavailable": {"en": "This cannot be read right now. Try again.", "ar": "لا يمكن قراءة هذا الآن. حاول مرة أخرى."},
    "sign_in.err_no_session": {"en": "That session has ended already.", "ar": "انتهت تلك الجلسة بالفعل."},
    "sign_in.err_bad_hours": {"en": "An invitation lasts 1 to 72 hours.", "ar": "تدوم الدعوة من ساعة إلى 72 ساعة."},
    "sign_in.err_spaces_off": {"en": "Invitations work once Spaces is on.", "ar": "تعمل الدعوات بعد تفعيل المساحات."},
    "sign_in.err_not_ready": {
        "en": "This install is not ready for a second person yet.",
        "ar": "هذا التثبيت غير جاهز لشخص ثانٍ بعد.",
    },
    "sign_in.err_failed": {"en": "That did not work. Try again.", "ar": "لم ينجح ذلك. حاول مرة أخرى."},
    "sign_in.err_paused": {
        "en": "Too many wrong codes: codes are not checked for a while. Try again later.",
        "ar": "رموز خاطئة كثيرة: لا تُفحص الرموز لفترة. حاول لاحقًا.",
    },
    "sign_in.err_session_only": {
        "en": "This is done from a signed-in admin's session, never with the install's secret or a token.",
        "ar": "يُنفَّذ هذا من جلسة مسؤول مسجّل الدخول، لا بسرّ التثبيت ولا برمز.",
    },
    "sign_in.err_own_in_my_account": {
        "en": "Your own second factor is turned off in My account, with a code.",
        "ar": "يُوقَف عامل التحقق الثاني الخاص بك من «حسابي»، برمز.",
    },
    "sign_in.paused_note": {
        "en": "Wrong codes paused the checks of your codes at {when}. If that was not you, change your password.",
        "ar": "أوقفت رموز خاطئة فحص رموزك في {when}. إن لم تكن أنت، غيّر كلمة مرورك.",
    },
    "sign_in.auto_login_note": {
        "en": "On this computer Kazma doesn't ask for the code: it signs you in by itself here "
              "(the auto-login). Elsewhere it asks.",
        "ar": "على هذا الحاسوب لا يطلب Kazma الرمز: يسجّل دخولك بنفسه هنا (الدخول التلقائي). "
              "في أي مكان آخر يطلبه.",
    },
    "sign_in.install_secret_note": {
        "en": "The install's secret (KAZMA_SECRET, in the install's .env) is a full key for programs: "
              "the second factor does not cover it. Keep it as you keep a password, and never put it "
              "in a browser.",
        "ar": "سرّ التثبيت (KAZMA_SECRET، في ملف .env الخاص بالتثبيت) مفتاح كامل للبرامج: لا يشمله "
              "عامل التحقق الثاني. احفظه كما تحفظ كلمة مرور، ولا تضعه في متصفح أبدًا.",
    },
    "sign_in.via_this_computer": {"en": "This computer (auto-login)", "ar": "هذا الحاسوب (دخول تلقائي)"},
    "sign_in.via_local_network": {"en": "The local network (auto-login)", "ar": "الشبكة المحلية (دخول تلقائي)"},
    "sign_in.via_secret_header": {"en": "A browser with the install's secret", "ar": "متصفح بسرّ التثبيت"},
    "sign_in.via_auto": {"en": "Auto-login", "ar": "دخول تلقائي"},
    "sign_in.via_signed_in": {"en": "Signed in", "ar": "تسجيل دخول"},

    # ── My account and People: sessions ─────────────────────────────────
    "sign_in.end": {"en": "End", "ar": "إنهاء"},
    "sign_in.end_mine_title": {"en": "End this session?", "ar": "إنهاء هذه الجلسة؟"},
    "sign_in.end_mine_confirm": {"en": "That device signs in again.", "ar": "يسجّل ذلك الجهاز الدخول من جديد."},
    "sign_in.session_ended": {"en": "That session has ended.", "ar": "انتهت تلك الجلسة."},
    "sign_in.end_one_title": {"en": "End this session of {name}?", "ar": "إنهاء هذه الجلسة لـ {name}؟"},
    "sign_in.end_one_confirm": {
        "en": "They sign in again on that device. They are told the next time they sign in.",
        "ar": "يسجّل الدخول من جديد على ذلك الجهاز، ويُبلَّغ بذلك عند تسجيل دخوله القادم.",
    },
    "sign_in.people_second_factor": {"en": "Second factor:", "ar": "عامل التحقق الثاني:"},
    "sign_in.people_sessions": {"en": "Sessions", "ar": "الجلسات"},
    "sign_in.people_sessions_of": {"en": "The sessions of {name}", "ar": "جلسات {name}"},
    "sign_in.sessions_failed": {"en": "Their sessions cannot be read right now.", "ar": "لا يمكن قراءة جلساته الآن."},
    "sign_in.reset": {"en": "Reset second factor", "ar": "إعادة ضبط عامل التحقق الثاني"},
    "sign_in.reset_of": {"en": "Reset the second factor of {name}", "ar": "إعادة ضبط عامل التحقق الثاني لـ {name}"},
    "sign_in.reset_title": {"en": "Reset {name}'s second factor?", "ar": "إعادة ضبط عامل التحقق الثاني لـ {name}؟"},
    "sign_in.reset_confirm": {
        "en": "They sign in with their password alone until they turn a new one on. They are told the next time they sign in.",
        "ar": "يسجّل الدخول بكلمة المرور وحدها حتى يفعّل عاملًا جديدًا، ويُبلَّغ بذلك عند تسجيل دخوله القادم.",
    },
    "sign_in.reset_done": {"en": "Their second factor is off.", "ar": "تم إيقاف عامل التحقق الثاني لديه."},

    # ── People: invitations ──────────────────────────────────────────────
    "sign_in.invitations_title": {"en": "Invitations", "ar": "الدعوات"},
    "sign_in.invitations_help": {
        "en": "A link that makes one account, then stops working. It runs out after the hours you choose (72 at most). "
              "Send it to the person yourself; it is shown once.",
        "ar": "رابط يُنشئ حسابًا واحدًا ثم يتوقف عن العمل، وينتهي بعد عدد الساعات الذي تختاره (72 كحد أقصى). "
              "أرسله إلى الشخص بنفسك؛ يظهر مرة واحدة.",
    },
    "sign_in.invite_name": {"en": "Their name (they can change it)", "ar": "اسمه (يستطيع تغييره)"},
    "sign_in.invite_hours": {"en": "Works for (hours)", "ar": "يعمل لمدة (ساعات)"},
    "sign_in.invite_make": {"en": "Make an invitation link", "ar": "أنشئ رابط دعوة"},
    "sign_in.invite_link_once": {"en": "The link, shown once:", "ar": "الرابط، يظهر مرة واحدة:"},
    "sign_in.copy_link": {"en": "Copy the link", "ar": "انسخ الرابط"},
    "sign_in.invitations_failed": {"en": "The invitations cannot be read right now.", "ar": "لا يمكن قراءة الدعوات الآن."},
    "sign_in.invitations_none": {"en": "No invitations yet.", "ar": "لا توجد دعوات بعد."},
    "sign_in.invite_open": {"en": "Open", "ar": "مفتوحة"},
    "sign_in.invite_used": {"en": "Used", "ar": "مستخدمة"},
    "sign_in.invite_revoked": {"en": "Revoked", "ar": "ملغاة"},
    "sign_in.invite_expired": {"en": "Ran out", "ar": "منتهية"},
    "sign_in.invite_until": {"en": "Until {when}", "ar": "حتى {when}"},
    "sign_in.revoke": {"en": "Revoke", "ar": "إلغاء"},
    "sign_in.revoke_title": {"en": "Revoke this invitation?", "ar": "إلغاء هذه الدعوة؟"},
    "sign_in.revoke_confirm": {"en": "The link stops working now.", "ar": "يتوقف الرابط عن العمل الآن."},

    # ── Notices told to the person ───────────────────────────────────────
    "sign_in.notice_reset_title": {"en": "Your second factor was reset", "ar": "تمت إعادة ضبط عامل التحقق الثاني لديك"},
    "sign_in.notice_reset_body": {
        "en": "The admin turned your second factor off on {when}: you sign in with your password alone until you "
              "turn a new one on in My account. If you did not ask for this, tell them.",
        "ar": "أوقف المسؤول عامل التحقق الثاني لديك في {when}: تسجّل الدخول بكلمة المرور وحدها حتى تفعّل عاملًا "
              "جديدًا في حسابي. إن لم تطلب ذلك، أخبره.",
    },
    "sign_in.notice_session_title": {"en": "One of your sessions was ended", "ar": "أُنهيت إحدى جلساتك"},
    "sign_in.notice_session_body": {
        "en": "The admin ended one of your sessions on {when}: you sign in again on that device. "
              "If you did not expect this, tell them.",
        "ar": "أنهى المسؤول إحدى جلساتك في {when}: تسجّل الدخول من جديد على ذلك الجهاز. إن لم تتوقع ذلك، أخبره.",
    },
    "sign_in.notice_paused_title": {"en": "Sign-ins to your account were paused", "ar": "أُوقفت محاولات الدخول إلى حسابك مؤقتًا"},
    "sign_in.notice_paused_body": {
        "en": "On {when}, so many wrong passwords were tried for your account that sign-ins to it were paused for a "
              "while, except from places you signed in from before. If that was not you, change your password and "
              "turn on a second factor.",
        "ar": "في {when}، جُرّبت كلمات مرور خاطئة كثيرة لحسابك، فأُوقفت محاولات الدخول إليه لفترة، إلا من الأماكن التي "
              "سجّلت الدخول منها من قبل. إن لم يكن ذلك أنت، غيّر كلمة المرور وفعّل عامل تحقق ثانيًا.",
    },
    "sign_in.notice_codes_paused_title": {
        "en": "Wrong codes paused your second factor",
        "ar": "أوقفت رموز خاطئة عامل التحقق الثاني لديك",
    },
    "sign_in.notice_codes_paused_body": {
        "en": "On {when}, five wrong codes were tried for your account, so its codes were not checked for a while, "
              "from every place. Someone who tries codes has your password: if that was not you, change it now.",
        "ar": "في {when}، جُرّبت خمسة رموز خاطئة لحسابك، فلم تُفحص رموزه لفترة، من كل مكان. من يجرّب الرموز يعرف "
              "كلمة مرورك: إن لم يكن ذلك أنت، غيّرها الآن.",
    },
}
