"""Knowledge Library retrieval benchmark (KQ01): build it, record it, run it.

  python scripts/knowledge_bench.py build            # the dataset JSON (deterministic)
  python scripts/knowledge_bench.py vectors          # record bge-m3's vectors for CI
  python scripts/knowledge_bench.py run [--real]     # score both paths; --real loads the model
  python scripts/knowledge_bench.py run --chroma     # the same, on the real chromadb
  python scripts/knowledge_bench.py lock             # raise the CI ratchet to today's scores

The corpus is four documentation libraries the way an operator ingests them:
a messaging API in English, an object store in English whose topics sit next
to the API's (keys, rate limits, retries, signed callbacks: the near misses),
an invoicing product's help in Arabic, and an operations runbook written the
way a Gulf team writes one -- Arabic sentences around English terms and
commands. Every question names the sections that answer it (``gold``) or none
(``abstain``: nothing should be shown). The runner is
``kazma_core.stores.knowledge_benchmark``; ``tests/test_knowledge_benchmark.py``
holds the scores from the recorded vectors to a ratchet.

Reads no install: the benchmark writes its own temporary Knowledge store, and
this script points the data directory at a temporary folder before Kazma is
imported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "kazma-core" / "kazma_core" / "stores" / "knowledge_benchmark_data" / "dataset.json"
FIXTURES = REPO / "tests" / "fixtures" / "knowledge_bench"
VECTORS = FIXTURES / "vectors.npz"
THRESHOLDS = FIXTURES / "thresholds.json"

#: Bump when a text of the corpus or a question changes: the recorded
#: vectors answer exactly these texts, and the ratchet names the version.
VERSION = 1

# ── The corpus: (library id, name, seed, [(page, url, title, [(section, heading, body)])]) ──

RELAY = "https://docs.relay.example"
ORBIT = "https://docs.orbit-storage.example"
HESAB = "https://help.hesab.example/ar"
OPS = "https://wiki.ops.example/runbook"

LIBRARIES: list[tuple[str, str, str, str, list[tuple[str, str, str, list[tuple[str, str, str]]]]]] = [
    ("relay_api", "Relay Messaging API", RELAY, "relay", [
        ("auth", f"{RELAY}/auth", "Authentication", [
            ("api_keys", "API keys",
             "Every request to the Relay API is authenticated with an API key. Create keys in the "
             "dashboard under Settings, Developers, API keys. Send the key in the `Authorization` "
             "header as `Bearer <key>`. A key is shown once, when it is created: keep it in a "
             "secrets manager, never in client-side code."),
            ("rotation", "Rotating keys",
             "An account can hold two active keys at the same time, so you can rotate without "
             "downtime: create the new key, deploy it, then revoke the old one. We recommend "
             "rotating every 90 days. A revoked key stops working within 60 seconds."),
            ("oauth", "OAuth for partner apps",
             "Partner applications that act for other Relay accounts use the OAuth 2.0 "
             "authorization code flow. Request only the scopes you need: `messages:send`, "
             "`messages:read` and `templates:manage`. Access tokens expire after one hour; use "
             "the refresh token to get a new one."),
            ("errors", "Authentication errors",
             "A missing or malformed key returns `401 Unauthorized`. A valid key without the "
             "required scope returns `403 Forbidden`, and the error body names the missing scope."),
        ]),
        ("rate_limits", f"{RELAY}/rate-limits", "Rate limits", [
            ("per_plan", "Limits per plan",
             "Each plan has a request rate limit per account: Starter allows 20 requests per "
             "second, Business 200 requests per second and Enterprise 1,000 requests per second. "
             "The limit applies across all API keys of the account."),
            ("burst", "Bursts",
             "Short bursts above the limit are absorbed by a token bucket: you may send up to twice "
             "your plan's rate for 10 seconds before requests are rejected."),
            ("too_many", "Handling 429 responses",
             "When you exceed the limit the API answers `429 Too Many Requests` with a "
             "`Retry-After` header giving the number of seconds to wait. Back off for at least "
             "that long; retrying sooner extends the block."),
            ("tiers", "Messaging tiers",
             "Separately from the request rate, a new sender may message at most 1,000 unique "
             "recipients in 24 hours. The tier rises to 10,000 and then 100,000 recipients once "
             "the sender keeps a high quality rating for 7 days."),
        ]),
        ("webhooks", f"{RELAY}/webhooks", "Webhooks", [
            ("subscribe", "Subscribing to events",
             "Register a webhook URL in the dashboard or with `POST /v2/webhooks`. The URL must use "
             "HTTPS with a valid certificate. Relay first sends a verification request carrying a "
             "`challenge` parameter, and your endpoint must answer with the same value within 5 "
             "seconds."),
            ("signatures", "Verifying signatures",
             "Every webhook request carries an `X-Relay-Signature` header: an HMAC-SHA256 of the "
             "raw request body, keyed with your app secret and encoded as hex. Compute it over the "
             "raw bytes before parsing the JSON, and compare in constant time."),
            ("retries", "Delivery and retries",
             "Your endpoint must answer with a 2xx status within 10 seconds. If it does not, Relay "
             "retries with exponential backoff for up to 72 hours, at most 12 attempts. After that "
             "the event is dropped and listed under failed deliveries in the dashboard."),
            ("events", "Event types",
             "Relay sends `message.sent`, `message.delivered`, `message.read` and `message.failed` "
             "events for outbound messages, and `message.received` for inbound ones. Events can "
             "arrive out of order: use the `timestamp` field to order them."),
        ]),
        ("messages", f"{RELAY}/messages", "Sending messages", [
            ("send_text", "Send a text message",
             "Send a message with `POST /v2/messages`, giving the recipient in E.164 format (for "
             "example `+96550000000`), `type: text` and the `body`. A text body may hold up to "
             "4,096 characters; a longer body is rejected with error 4003."),
            ("media", "Media messages",
             "Images may be JPEG or PNG up to 5 MB, video MP4 up to 16 MB, audio AAC or OGG up to "
             "16 MB and documents up to 100 MB. Upload the file first with `POST /v2/media` and "
             "send its id, or give a public HTTPS link."),
            ("status", "Message status",
             "A message moves through `accepted`, `sent`, `delivered` and `read`, or ends "
             "`failed`. Read receipts depend on the recipient's privacy settings, so `read` may "
             "never arrive even for a message that was read."),
            ("window", "The 24-hour window",
             "You may send free-form messages to a user only within 24 hours of their last message "
             "to you. Outside that window only approved template messages can be sent; a "
             "free-form message is refused with error 4701."),
        ]),
        ("templates", f"{RELAY}/templates", "Message templates", [
            ("create", "Creating a template",
             "Template names use lowercase letters, digits and underscores, up to 512 characters. "
             "Choose a category: `marketing`, `utility` or `authentication`. The category decides "
             "the price and which content is allowed."),
            ("review", "Template review",
             "New templates are reviewed before use, and most are approved within 24 hours. Common "
             "reasons for rejection are missing variable samples, a category that does not match "
             "the content, and links to shortened URLs."),
            ("variables", "Variables",
             "Placeholders are numbered `{{1}}`, `{{2}}` and so on, and a template may hold up to "
             "10 of them. Every placeholder needs a sample value when you submit the template; the "
             "real values are filled in when you send it."),
            ("languages", "Languages",
             "A template exists once per language code, such as `en_US`, `ar` or `fr`. Send the "
             "language code with the message. If the template has no translation for it, the send "
             "fails with error 4302; it never falls back to another language."),
        ]),
        ("errors", f"{RELAY}/errors", "Error codes", [
            ("format", "Error format",
             "Errors return a JSON body with `code`, `message` and `trace_id`. Quote the "
             "`trace_id` when you contact support: it lets us find the request in our logs."),
            ("common", "Common error codes",
             "4001: the phone number is not in E.164 format. 4003: the message body is too long. "
             "4290: you are being rate limited. 4302: the template has no translation for the "
             "requested language. 4701: a free-form message outside the 24-hour window. 1310: the "
             "recipient has opted out of messages from you."),
            ("retry_guidance", "When to retry",
             "Retry 5xx errors and 429 responses with exponential backoff and jitter. Do not retry "
             "other 4xx errors: the request itself is wrong and will fail again. Send an "
             "`Idempotency-Key` header so that a retried send is never delivered twice."),
        ]),
        ("pagination", f"{RELAY}/pagination", "Pagination", [
            ("cursor", "Cursor pagination",
             "List endpoints return at most `limit` items (default 25, maximum 100) and a "
             "`next_cursor`. Pass it as `cursor` to fetch the next page; a null `next_cursor` "
             "means you have reached the end."),
            ("filtering", "Filtering lists",
             "Filter message lists with `created_after` and `created_before` (ISO 8601 "
             "timestamps) and with `status`. Several filters combine with AND."),
        ]),
        ("sdks", f"{RELAY}/sdks", "SDKs and sandbox", [
            ("python", "Python SDK",
             "Install the Python SDK with `pip install relay-sdk`; it supports Python 3.9 and "
             "newer. Create a client with `relay.Client(api_key=...)`. The client retries 429 and "
             "5xx responses for you."),
            ("node", "Node.js SDK",
             "Install the Node.js SDK with `npm install @relay/sdk`. It needs Node.js 18 or newer "
             "and ships TypeScript types."),
            ("sandbox", "Sandbox",
             "Every account has a sandbox with test numbers. Messages sent from the sandbox are not "
             "delivered to real phones and do not count against your tier. Sandbox keys start with "
             "`rk_test_`, live keys with `rk_live_`."),
        ]),
    ]),
    ("orbit_storage", "Orbit Object Storage", ORBIT, "orbit", [
        ("buckets", f"{ORBIT}/buckets", "Buckets", [
            ("naming", "Naming rules",
             "Bucket names are 3 to 63 characters long, use lowercase letters, digits and hyphens, "
             "and must be unique across all Orbit accounts."),
            ("regions", "Regions",
             "Create a bucket in one region: `eu-west`, `us-east` or `me-central`. A bucket cannot "
             "move between regions; copy its objects to a new bucket instead."),
            ("versioning", "Versioning",
             "Turn on versioning to keep every version of an object. Deleting an object leaves a "
             "delete marker, and older versions can be restored until a lifecycle rule removes "
             "them."),
        ]),
        ("access", f"{ORBIT}/access", "Access keys and policies", [
            ("pairs", "Key pairs",
             "Orbit authenticates requests with an access key id and a secret key, signed with "
             "HMAC-SHA256 in the `Authorization` header. A user may hold two key pairs at once."),
            ("policies", "Bucket policies",
             "A bucket policy is a JSON document that allows or denies actions such as "
             "`orbit:GetObject` to users and keys. A deny always wins over an allow."),
        ]),
        ("limits", f"{ORBIT}/limits", "Request limits", [
            ("prefix_rates", "Request rates",
             "Orbit supports at least 3,500 write requests and 5,500 read requests per second for "
             "each key prefix. Spread heavy workloads across prefixes to go beyond that."),
            ("object_size", "Object size",
             "A single upload may be up to 5 GB. Larger objects, up to 5 TB, must use multipart "
             "upload with parts between 5 MB and 5 GB."),
        ]),
        ("lifecycle", f"{ORBIT}/lifecycle", "Lifecycle rules", [
            ("transitions", "Storage class transitions",
             "Lifecycle rules move objects to the `cold` class after a number of days, for example "
             "30, and to `archive` after 180. Reading an archived object back takes up to 12 "
             "hours."),
            ("expiration", "Expiration",
             "An expiration rule deletes objects a set number of days after they were created. "
             "Rules run once a day, so a deletion can lag by up to 24 hours."),
        ]),
        ("notifications", f"{ORBIT}/notifications", "Event notifications", [
            ("config", "Configuring notifications",
             "Orbit can call an HTTPS endpoint when an object is created or deleted. Notifications "
             "are delivered at least once, so make your handler idempotent; failed deliveries are "
             "retried for 24 hours."),
        ]),
    ]),
    ("hesab_help", "مساعدة حساب", HESAB, "hesab", [
        ("invoices", f"{HESAB}/invoices", "الفواتير", [
            ("create", "إنشاء فاتورة",
             "لإنشاء فاتورة جديدة افتح قائمة «الفواتير» واضغط «فاتورة جديدة»، ثم اختر العميل وأضف "
             "البنود بالكمية والسعر. يُعطى رقم الفاتورة تلقائيًا بالتسلسل ويبدأ بالبادئة INV، ويمكن "
             "تغيير البادئة من الإعدادات."),
            ("recurring", "الفواتير المتكررة",
             "الفاتورة المتكررة تُنشأ وتُرسل تلقائيًا كل أسبوع أو كل شهر أو كل سنة في التاريخ الذي "
             "تحدده. يمكنك إيقاف التكرار مؤقتًا دون أن تُحذف الفواتير السابقة."),
            ("cancel", "إلغاء فاتورة",
             "لا يمكن حذف فاتورة بعد إصدارها. لإلغائها أصدر إشعارًا دائنًا بالمبلغ نفسه من صفحة "
             "الفاتورة، فتظهر الفاتورة ملغاة في التقارير ويبقى سجلها محفوظًا."),
            ("reminders", "تذكير العملاء بالدفع",
             "يرسل النظام تذكيرًا بالبريد الإلكتروني قبل موعد الاستحقاق بثلاثة أيام، وتذكيرًا ثانيًا "
             "بعد التأخر بسبعة أيام. يمكنك تعديل نص التذكير أو إيقافه لكل عميل."),
        ]),
        ("tax", f"{HESAB}/tax", "الضريبة", [
            ("vat", "ضريبة القيمة المضافة",
             "تُحسب ضريبة القيمة المضافة بنسبة 15% افتراضيًا، ويمكن تحديد نسبة مختلفة لكل بند. يجب "
             "إدخال الرقم الضريبي للمنشأة في الإعدادات قبل إصدار أول فاتورة ضريبية."),
            ("einvoice", "الفاتورة الإلكترونية",
             "تحمل كل فاتورة ضريبية رمز QR يتضمن اسم البائع ورقمه الضريبي وتاريخ الفاتورة والمبلغ "
             "والضريبة، وتُرسل نسخة منها إلى بوابة هيئة الضرائب خلال 24 ساعة من إصدارها."),
            ("exempt", "الإعفاء والنسبة الصفرية",
             "تخضع الصادرات إلى خارج الدولة لنسبة صفرية، أما الخدمات المعفاة مثل بعض الخدمات "
             "المالية فلا تُضاف عليها ضريبة. اختر نوع الإعفاء لكل بند حتى يظهر في الإقرار الضريبي "
             "بشكل صحيح."),
        ]),
        ("payments", f"{HESAB}/payments", "المدفوعات", [
            ("online", "الدفع الإلكتروني",
             "اربط حسابك ببوابة الدفع من «الإعدادات ثم المدفوعات» ليتمكن العميل من الدفع ببطاقة مدى "
             "أو فيزا أو ماستركارد من رابط الفاتورة. تقتطع البوابة رسومًا قدرها 2.5% من كل عملية."),
            ("manual", "تسجيل دفعة يدوية",
             "إذا دفع العميل بتحويل بنكي أو نقدًا فسجّل الدفعة يدويًا من صفحة الفاتورة مع تاريخها "
             "وطريقتها، وأرفق إيصال التحويل إن وجد."),
            ("partial", "الدفعات الجزئية",
             "يمكن تسجيل أكثر من دفعة على الفاتورة الواحدة، ويظهر الرصيد المتبقي في أعلى الفاتورة "
             "حتى تُسدَّد بالكامل فتتحول حالتها إلى «مدفوعة»."),
            ("refund", "استرداد مبلغ",
             "يمكن استرداد دفعة إلكترونية خلال 30 يومًا من تاريخها من صفحة الدفعة. يصل المبلغ إلى "
             "بطاقة العميل خلال 5 إلى 10 أيام عمل حسب البنك."),
        ]),
        ("currencies", f"{HESAB}/currencies", "العملات", [
            ("multi", "الفوترة بعملات متعددة",
             "يمكنك إصدار فاتورة بالدولار أو اليورو أو أي عملة أخرى. يُجلب سعر الصرف تلقائيًا كل "
             "يوم، ولك أن تكتب سعرًا مختلفًا للفاتورة قبل إصدارها."),
            ("base", "العملة الأساسية",
             "تُحدَّد العملة الأساسية عند إنشاء الحساب، وتُعرض بها كل التقارير. لا يمكن تغييرها بعد "
             "إصدار أول فاتورة."),
        ]),
        ("reports", f"{HESAB}/reports", "التقارير", [
            ("pnl", "تقرير الأرباح والخسائر",
             "يعرض تقرير الأرباح والخسائر الإيرادات والمصروفات وصافي الربح لأي فترة تختارها، ويمكن "
             "مقارنة الفترة بالفترة السابقة."),
            ("aging", "أعمار الديون",
             "يقسم تقرير أعمار الديون المبالغ المستحقة على العملاء إلى فئات: أقل من 30 يومًا، ومن 31 "
             "إلى 60، ومن 61 إلى 90، وأكثر من 90 يومًا."),
            ("export", "تصدير البيانات",
             "يمكن تصدير أي تقرير أو قائمة فواتير إلى Excel أو CSV أو PDF من زر «تصدير» في أعلى "
             "الصفحة."),
        ]),
        ("users", f"{HESAB}/users", "المستخدمون", [
            ("roles", "صلاحيات المستخدمين",
             "هناك ثلاثة أدوار: المدير يملك كل الصلاحيات، والمحاسب يصدر الفواتير ويسجل الدفعات ولا "
             "يستطيع حذف البيانات أو تغيير الإعدادات، والمشاهد يطّلع على التقارير فقط."),
            ("invite", "إضافة مستخدم",
             "أضف مستخدمًا بدعوته عبر بريده الإلكتروني من «الإعدادات ثم المستخدمون». تتيح الباقة "
             "الأساسية خمسة مستخدمين، والباقة المتقدمة عددًا غير محدود."),
            ("twofa", "التحقق بخطوتين",
             "فعّل التحقق بخطوتين من ملفك الشخصي باستخدام تطبيق مصادقة. التحقق بخطوتين إلزامي لكل "
             "من يملك دور المدير."),
        ]),
        ("data", f"{HESAB}/data", "البيانات", [
            ("backup", "النسخ الاحتياطي اليومي",
             "تُؤخذ نسخة احتياطية من بياناتك كل يوم تلقائيًا، وتُحفظ كل نسخة 30 يومًا. لاستعادة "
             "نسخة سابقة تواصل مع الدعم الفني واذكر التاريخ المطلوب."),
            ("import", "استيراد البيانات",
             "يمكن استيراد العملاء والمنتجات من ملف Excel باستخدام القالب المتاح في صفحة الاستيراد. "
             "تُرفض الصفوف التي ينقصها اسم العميل وتظهر في تقرير الأخطاء."),
        ]),
    ]),
    ("ops_runbook", "Ops runbook", OPS, "ops", [
        ("deploy", f"{OPS}/deploy", "Deploy", [
            ("release", "Deploy to production",
             "قبل أي deploy للإنتاج لازم يكون الـ CI أخضر على فرع main، وفيه موافقة من شخصين على "
             "الـ pull request. بعدها شغّل `make release VERSION=x.y.z` من جهازك، والـ pipeline "
             "يبني الـ image ويرفعها للـ registry ويسوي rollout تدريجي."),
            ("rollback", "Rollback",
             "إذا طلع خطأ بعد الـ deploy لا تحاول تصلحه مباشرة على الإنتاج. ارجع للإصدار السابق "
             "بأمر `kubectl rollout undo deployment/api -n prod` وبعدين افتح incident."),
            ("freeze", "Deploy freeze",
             "ما في deploy يوم الخميس بعد الساعة 2 الظهر ولا في أيام الإجازات الرسمية، إلا hotfix "
             "لحادثة P1 بموافقة الـ on-call lead."),
            ("flags", "Feature flags",
             "الميزات الجديدة تنزل مطفية خلف feature flag في Flagset وتتفعل بالتدريج: 5% ثم 25% ثم "
             "100%. أي flag عمرها أكثر من 90 يوم لازم تنشال من الكود."),
        ]),
        ("oncall", f"{OPS}/oncall", "On-call", [
            ("rotation", "On-call rotation",
             "المناوبة أسبوعية وتبدأ يوم الأحد الساعة 9 الصبح بتوقيت الكويت. تقدر تبدّل مناوبتك مع "
             "زميل بشرط تحدّث الجدول في Beacon قبلها بـ 48 ساعة."),
            ("escalation", "Escalation",
             "إذا ما أحد رد على تنبيه P1 خلال 15 دقيقة يتصعّد تلقائيًا للـ team lead، وبعد 30 دقيقة "
             "للـ CTO."),
            ("handover", "Handover",
             "في نهاية المناوبة اكتب handover note في قناة #ops-handover، فيها الحوادث المفتوحة "
             "والتنبيهات المزعجة اللي تحتاج متابعة."),
        ]),
        ("database", f"{OPS}/database", "Database", [
            ("backups", "Database backups",
             "يصير `pg_dump` كامل لقاعدة الإنتاج كل ليلة الساعة 2 بتوقيت UTC، والنسخ تنحفظ في "
             "bucket اسمه `ops-backups` لمدة 14 يوم."),
            ("restore", "Restore",
             "للاسترجاع: نزّل آخر dump من `ops-backups`، استرجعه على خادم staging أول وتأكد من "
             "البيانات، وبعدها استرجعه على الإنتاج بأمر `pg_restore --clean`. الاسترجاع على الإنتاج "
             "يحتاج موافقة الـ CTO."),
            ("slow_queries", "Slow queries",
             "أي query تاخذ أكثر من 500 ms تنسجل في `pg_stat_statements`. راجع أبطأ عشر queries كل "
             "أسبوع وافتح ticket لأي وحدة تتكرر."),
            ("migrations", "Schema migrations",
             "الـ migrations لازم تكون backward compatible: أضف العمود أول، انشر الكود، وبعدين احذف "
             "القديم في release منفصل. ممنوع `ALTER TABLE` يقفل جدول كبير وقت الذروة."),
        ]),
        ("access", f"{OPS}/access", "Access", [
            ("vpn", "VPN",
             "الدخول لأي نظام داخلي يكون عن طريق WireGuard VPN. كل موظف ياخذ config خاص فيه من "
             "IT portal، والـ config ما ينشارك أبدًا."),
            ("ssh", "SSH access",
             "الـ SSH للخوادم يمر عبر الـ bastion host `bastion.ops.example` بمفاتيح فقط، والدخول "
             "بكلمة سر مقفل. المفاتيح تنضاف بطلب في IT portal."),
            ("secrets", "Secrets",
             "كلمات السر والمفاتيح محفوظة في Vault تحت المسار `secret/apps/<app-name>`. إذا موظف "
             "ترك الشركة، كل الـ secrets اللي كان يوصل لها تتغيّر خلال 24 ساعة."),
        ]),
        ("incidents", f"{OPS}/incidents", "Incidents", [
            ("severity", "Severity levels",
             "P1 يعني توقف كامل يأثر على العملاء، P2 أداء ضعيف أو ميزة مهمة واقفة، P3 مشكلة بسيطة "
             "لها حل مؤقت."),
            ("postmortem", "Postmortem",
             "بعد أي حادثة P1 أو P2 لازم ينكتب postmortem خلال 5 أيام عمل على القالب الـ "
             "blameless، ويتراجع في اجتماع الأربعاء."),
            ("status_page", "Status page",
             "حدّث الـ status page خلال 10 دقايق من بداية أي حادثة P1، وبعدها كل 30 دقيقة لين ما "
             "تنحل."),
        ]),
    ]),
]

# ── Questions: (id, text, category, [gold pieces], [near misses ranked below]) ──
#
# A gold piece is a section ref ("relay.webhooks.retries"), "a|b" when either
# section gives it. ``below``: sections that must not rank above the answer
# (another product's rate limit, another backup's retention). Categories:
# exact and paraphrase (English), arabic and arabic_paraphrase (Gulf and MSA
# wording), mixed (the runbook, asked in either language or both), cross (a
# question in one language about a page in the other), multi (two sections),
# abstain and abstain_ar (nothing in the libraries answers: nothing shown).

QUESTIONS: list[tuple[str, str, str, list[str], list[str]]] = [
    ("e01", "How long does Relay keep retrying webhook deliveries?", "exact",
     ["relay.webhooks.retries"], ["orbit.notifications.config"]),
    ("e02", "What is the request rate limit on the Business plan?", "exact",
     ["relay.rate_limits.per_plan"], ["orbit.limits.prefix_rates"]),
    ("e03", "Which header carries the webhook signature?", "exact", ["relay.webhooks.signatures"], []),
    ("e04", "What is the maximum size of a video message?", "exact", ["relay.messages.media"], []),
    ("e05", "How many variables can a message template hold?", "exact", ["relay.templates.variables"], []),
    ("e06", "What does error 4701 mean?", "exact", ["relay.errors.common|relay.messages.window"], []),
    ("e07", "How do I install the Python SDK?", "exact", ["relay.sdks.python"], []),
    ("e08", "What is the maximum page size for list endpoints?", "exact", ["relay.pagination.cursor"], []),
    ("e09", "How long does template review take?", "exact", ["relay.templates.review"], []),
    ("e10", "How large can a single Orbit upload be?", "exact", ["orbit.limits.object_size"], []),
    ("e11", "What are the bucket naming rules?", "exact", ["orbit.buckets.naming"], []),
    ("e12", "Which prefix do sandbox API keys start with?", "exact", ["relay.sdks.sandbox"], []),

    ("p01", "My server was down for a day, will I lose the events Relay tried to send me?", "paraphrase",
     ["relay.webhooks.retries"], ["orbit.notifications.config"]),
    ("p02", "How can I swap out a credential without any outage?", "paraphrase",
     ["relay.auth.rotation"], ["orbit.access.pairs"]),
    ("p03", "Can I message a customer who last wrote to us three days ago?", "paraphrase",
     ["relay.messages.window"], []),
    ("p04", "The API told me to slow down. How long should I pause?", "paraphrase",
     ["relay.rate_limits.too_many"], []),
    ("p05", "How do I confirm a callback really came from Relay and was not forged?", "paraphrase",
     ["relay.webhooks.signatures"], []),
    ("p06", "Why might I never see that a recipient opened my message?", "paraphrase",
     ["relay.messages.status"], []),
    ("p07", "How many different people can a brand-new sender reach per day?", "paraphrase",
     ["relay.rate_limits.tiers"], []),
    ("p08", "Is it safe to resend a request after a timeout without the customer getting it twice?",
     "paraphrase", ["relay.errors.retry_guidance"], []),
    ("p09", "How do I get my objects into cheaper storage automatically after a month?", "paraphrase",
     ["orbit.lifecycle.transitions"], []),
    ("p10", "Can I relocate a bucket to another geography?", "paraphrase", ["orbit.buckets.regions"], []),
    ("p11", "What happens if I send a template in a language it was never translated into?", "paraphrase",
     ["relay.templates.languages"], []),
    ("p12", "How long is a partner app's access token valid?", "paraphrase", ["relay.auth.oauth"], []),

    ("a01", "كيف أنشئ فاتورة جديدة؟", "arabic", ["hesab.invoices.create"], []),
    ("a02", "كم نسبة ضريبة القيمة المضافة الافتراضية؟", "arabic", ["hesab.tax.vat"], []),
    ("a03", "ما رسوم بوابة الدفع الإلكتروني؟", "arabic", ["hesab.payments.online"], []),
    ("a04", "كم يستغرق وصول المبلغ المسترد إلى بطاقة العميل؟", "arabic", ["hesab.payments.refund"], []),
    ("a05", "كم مستخدمًا تتيح الباقة الأساسية؟", "arabic", ["hesab.users.invite"], []),
    ("a06", "ما مدة حفظ النسخ الاحتياطية في حساب؟", "arabic", ["hesab.data.backup"],
     ["ops.database.backups"]),
    ("a07", "ما فئات تقرير أعمار الديون؟", "arabic", ["hesab.reports.aging"], []),
    ("a08", "هل يمكن تغيير العملة الأساسية؟", "arabic", ["hesab.currencies.base"], []),

    ("r01", "طلعت فاتورة غلط، أقدر أمسحها؟", "arabic_paraphrase", ["hesab.invoices.cancel"], []),
    ("r02", "العميل دفع نص المبلغ بس، شلون أسجلها؟", "arabic_paraphrase", ["hesab.payments.partial"], []),
    ("r03", "أبي النظام يرسل الفاتورة لحاله كل شهر", "arabic_paraphrase", ["hesab.invoices.recurring"], []),
    ("r04", "المحاسب عندنا يقدر يغيّر الإعدادات؟", "arabic_paraphrase", ["hesab.users.roles"], []),
    ("r05", "زبون حوّل لي على البنك، وين أسجل هالشي؟", "arabic_paraphrase", ["hesab.payments.manual"], []),
    ("r06", "أبي أطلع قائمة الفواتير ملف إكسل", "arabic_paraphrase", ["hesab.reports.export"], []),
    ("r07", "متى يذكّر النظام العميل إذا ما دفع؟", "arabic_paraphrase", ["hesab.invoices.reminders"], []),

    ("m01", "شلون أسوي rollback إذا خرب الـ deploy؟", "mixed", ["ops.deploy.rollback"], []),
    ("m02", "متى تبدأ مناوبة الـ on-call؟", "mixed", ["ops.oncall.rotation"], []),
    ("m03", "How long do we keep the production database dumps?", "mixed", ["ops.database.backups"],
     ["hesab.data.backup"]),
    ("m04", "إذا ما أحد رد على P1 alert، لمين يتصعّد؟", "mixed", ["ops.oncall.escalation"], []),
    ("m05", "Can I deploy on Thursday afternoon?", "mixed", ["ops.deploy.freeze"], []),
    ("m06", "وين محفوظة الـ secrets حق التطبيقات؟", "mixed", ["ops.access.secrets"], []),
    ("m07", "How do I SSH into a production server?", "mixed", ["ops.access.ssh"], []),
    ("m08", "متى لازم نكتب الـ postmortem؟", "mixed", ["ops.incidents.postmortem"], []),
    ("m09", "What counts as a slow query for us?", "mixed", ["ops.database.slow_queries"], []),

    ("c01", "كم مرة يعيد Relay إرسال الـ webhook إذا ما رد السيرفر؟", "cross", ["relay.webhooks.retries"], []),
    ("c02", "ما الحد الأقصى لطول الرسالة النصية؟", "cross", ["relay.messages.send_text"], []),
    ("c03", "How long does a refund take to reach the customer's card in Hesab?", "cross",
     ["hesab.payments.refund"], []),
    ("c04", "Is two-factor authentication mandatory for admins in Hesab?", "cross", ["hesab.users.twofa"], []),
    ("c05", "كيف أتحقق من توقيع الطلب القادم من Relay؟", "cross", ["relay.webhooks.signatures"], []),
    ("c06", "What VAT rate does Hesab apply by default?", "cross", ["hesab.tax.vat"], []),

    ("x01", "What are the limits on request rate and on unique recipients for a new sender?", "multi",
     ["relay.rate_limits.per_plan", "relay.rate_limits.tiers"], []),
    ("x02", "Which events does Relay send, and how fast must my endpoint answer them?", "multi",
     ["relay.webhooks.events", "relay.webhooks.retries"], []),
    ("x03", "كيف أسجل دفعة يدوية، وكيف أسترد دفعة إلكترونية؟", "multi",
     ["hesab.payments.manual", "hesab.payments.refund"], []),
    ("x04", "How do we roll back a bad deploy, and how soon must the status page be updated?", "multi",
     ["ops.deploy.rollback", "ops.incidents.status_page"], []),

    ("n01", "How do I delete my Relay account?", "abstain", [], []),
    ("n02", "How much does the Business plan cost per month?", "abstain", [], []),
    ("n03", "Does Relay support voice calls?", "abstain", [], []),
    ("n04", "How do I configure SAML single sign-on for the dashboard?", "abstain", [], []),
    ("n05", "What is the weather in Lisbon tomorrow?", "abstain", [], []),
    ("n06", "Give me a recipe for lentil soup.", "abstain", [], []),
    ("n07", "Which GraphQL query lists my contacts?", "abstain", [], []),
    ("n08", "How do I encrypt Orbit objects with my own keys?", "abstain", [], []),

    ("z01", "كيف أحذف حسابي في حساب نهائيًا؟", "abstain_ar", [], []),
    ("z02", "كم سعر الاشتراك السنوي في الباقة المتقدمة؟", "abstain_ar", [], []),
    ("z03", "هل يدعم النظام إدارة الرواتب؟", "abstain_ar", [], []),
    ("z04", "شنو أفضل مطعم مشاوي في الكويت؟", "abstain_ar", [], []),
    ("z05", "شلون أنزل تطبيق الجوال على App Store؟", "abstain_ar", [], []),
    ("z06", "كم يوم إجازة سنوية للموظف؟", "abstain_ar", [], []),
]

#: Held out (scored apart, never used to choose a threshold): new wordings of
#: the same kinds of question, and new questions nothing answers.
HELDOUT: list[tuple[str, str, str, list[str], list[str]]] = [
    ("h01", "What scopes can an OAuth partner app request?", "exact", ["relay.auth.oauth"], []),
    ("h02", "If my handler is slow to respond, when does Relay stop waiting for it?", "paraphrase",
     ["relay.webhooks.retries"], []),
    ("h03", "Which audio formats can I send?", "exact", ["relay.messages.media"], []),
    ("h04", "How many writes per second does Orbit allow on one key prefix?", "exact",
     ["orbit.limits.prefix_rates"], ["relay.rate_limits.per_plan"]),
    ("h05", "كيف أفعّل التحقق بخطوتين؟", "arabic", ["hesab.users.twofa"], []),
    ("h06", "أبغى أعرف ربحي هالسنة مقارنة بالسنة اللي فاتت", "arabic_paraphrase", ["hesab.reports.pnl"], []),
    ("h07", "ماذا يتضمن رمز QR في الفاتورة الضريبية؟", "arabic", ["hesab.tax.einvoice"], []),
    ("h08", "شلون أشغّل feature جديدة لنسبة صغيرة من المستخدمين؟", "mixed", ["ops.deploy.flags"], []),
    ("h09", "Where do I get my VPN config?", "mixed", ["ops.access.vpn"], []),
    ("h10", "متى نحذف عمود قديم من الجدول؟", "mixed", ["ops.database.migrations"], []),
    ("h11", "ما مدة صلاحية رمز الوصول لتطبيقات الشركاء؟", "cross", ["relay.auth.oauth"], []),
    ("h12", "Can I import my customers from Excel into Hesab?", "cross", ["hesab.data.import"], []),
    ("h13", "How do I export my Relay message history to BigQuery?", "abstain", [], []),
    ("h14", "Who won the football match yesterday?", "abstain", [], []),
    ("h15", "هل يمكن ربط حساب بمتجر سلة الإلكتروني؟", "abstain_ar", [], []),
    ("h16", "وين ألقى كلمة سر الواي فاي حق المكتب؟", "abstain_ar", [], []),
]


def _questions(rows: list[tuple[str, str, str, list[str], list[str]]], refs: set[str]) -> list[dict[str, Any]]:
    out = []
    for qid, text, category, gold, below in rows:
        for ref in [a for piece in gold for a in piece.split("|")] + list(below):
            if ref not in refs:
                raise ValueError(f"{qid} names {ref!r}, which no section is")
        if category.startswith("abstain") == bool(gold):
            raise ValueError(f"{qid}: an abstain question has no gold, every other one has")
        out.append({"id": qid, "text": text, "category": category, "gold": list(gold),
                    "below": list(below)})
    return out


def build() -> dict[str, Any]:
    """The dataset, deterministically, from the tables above."""
    libraries, refs = [], set()
    for lib_id, name, seed, short, pages in LIBRARIES:
        lib_pages = []
        for page, url, title, sections in pages:
            rows = []
            for key, heading, body in sections:
                ref = f"{short}.{page}.{key}"
                if ref in refs:
                    raise ValueError(f"duplicate section ref {ref}")
                refs.add(ref)
                rows.append({"ref": ref, "heading": heading, "body": body})
            lib_pages.append({"url": url, "title": title, "sections": rows})
        libraries.append({"id": lib_id, "name": name, "seed": seed, "pages": lib_pages})
    questions = _questions(QUESTIONS, refs)
    heldout = _questions(HELDOUT, refs)
    ids = [q["id"] for q in questions + heldout]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate question id")
    tuning = {q["text"] for q in questions}
    for q in heldout:
        if q["text"] in tuning:
            raise ValueError(f"{q['id']} repeats a tuning question")
    return {"version": VERSION, "libraries": libraries, "questions": questions, "heldout": heldout}


def _cmd_build() -> int:
    data = build()
    before = json.loads(DATASET.read_text(encoding="utf-8")) if DATASET.is_file() else None
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sections = sum(len(p["sections"]) for lib in data["libraries"] for p in lib["pages"])
    print(f"{DATASET}: {len(data['libraries'])} libraries, {sections} sections, "
          f"{len(data['questions'])} questions, {len(data['heldout'])} held out")
    if before is not None and before != json.loads(json.dumps(data, ensure_ascii=False)):
        lock = "lock" if before.get("version") == data["version"] else "lock --rebaseline"
        print("the dataset changed: re-record the vectors (python scripts/knowledge_bench.py "
              f"vectors), then lock the scores (python scripts/knowledge_bench.py {lock})")
    return 0


def _limit_cpu_threads() -> None:
    """Leave most of the CPU to the live install on this machine (see
    scripts/memory_bench.py: embedding at torch's default starved it)."""
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))


def _replay():
    from kazma_core.memory.benchmark import ReplayEmbedder

    return ReplayEmbedder(VECTORS)


def _cmd_vectors() -> int:
    from kazma_core.memory.benchmark import RecordingEmbedder
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name
    from kazma_core.stores.knowledge_benchmark import run_benchmark

    _limit_cpu_threads()
    real = get_embedder()
    if real is None:
        print("no embedder is configured: install the rag extra (bge-m3)", file=sys.stderr)
        return 2
    model = get_embedding_model_name()
    recorder = RecordingEmbedder(real)
    report = run_benchmark(embedder=recorder)
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    n = recorder.save(VECTORS, model=model)
    print(f"{VECTORS}: {n} vectors from {model}")
    print(json.dumps(report["explicit"]["overall"], indent=1))
    return 0


def _print_part(title: str, part: dict[str, Any]) -> None:
    print(title)
    for name, row in part["categories"].items():
        extra = "  ".join(f"{k} {v}" for k, v in row.items() if k != "n")
        print(f"  {name:<18} n={row['n']:<3} {extra}")
    print("  overall:", json.dumps(part["overall"]))


def _cmd_run(real: bool, chroma: bool, as_json: bool, details: bool) -> int:
    from kazma_core.stores.knowledge_benchmark import run_profiles

    client = None
    if chroma:
        from kazma_core.memory.chroma_local import serial_index_inserts

        serial_index_inserts()  # before Chroma's thread pool first runs
        import chromadb
        from chromadb.config import Settings

        client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False, allow_reset=True))
    if real:
        from kazma_core.memory.embedder import get_embedder

        _limit_cpu_threads()
        embedder = get_embedder()
    else:
        embedder = _replay()
    report = run_profiles(embedder=embedder, client=client, keep_results=details)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    for profile, part in report["profiles"].items():
        print(f"== {profile} ({part['chunks']} chunks)")
        for path in ("explicit", "inject"):
            _print_part(f"-- {path}", part[path])
            _print_part(f"-- {path}, held out", part["heldout"][path])
        if details:
            for path in ("explicit", "inject"):
                for r in part[path].get("results", []) + part["heldout"][path].get("results", []):
                    if not r["hit"]:
                        print(f"  MISS {path}", r)
    return 0


def _cmd_lock(rebaseline: bool) -> int:
    """Write today's replayed scores as the ratchet; refuse to lower any.

    The ratchet names the dataset version it was measured on. A new dataset
    is a new instrument: its first scores are written with ``--rebaseline``,
    which is refused while the version is unchanged, so it can never lower
    the gate on the same data.
    """
    from kazma_core.stores.knowledge_benchmark import gated_scores, load_dataset, run_profiles

    version = load_dataset()["version"]
    scores = gated_scores(run_profiles(embedder=_replay()))
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
    THRESHOLDS.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLDS.write_text(
        json.dumps({"dataset_version": version, "scores": scores}, indent=1, ensure_ascii=False) + "\n",
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
    run.add_argument("--chroma", action="store_true", help="serve the vectors from the real chromadb")
    run.add_argument("--json", action="store_true")
    run.add_argument("--details", action="store_true", help="list every miss")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return _cmd_build()
    # Nothing an install holds is read or written: settings, the embedder's
    # overrides and the Knowledge store all resolve under a temporary folder.
    with tempfile.TemporaryDirectory(prefix="kazma-kb-bench-", ignore_cleanup_errors=True) as data_dir, \
            mock.patch.dict(os.environ, {"KAZMA_DATA_DIR": data_dir}):
        os.environ.pop("KAZMA_VECTOR_PATH", None)
        if args.cmd == "vectors":
            return _cmd_vectors()
        if args.cmd == "lock":
            return _cmd_lock(args.rebaseline)
        return _cmd_run(args.real, args.chroma, args.json, args.details)


if __name__ == "__main__":
    raise SystemExit(main())
