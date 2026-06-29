# Kazma (كاظمه) — Master Architecture Blueprint

## 1. Project Vision
Kazma is an open-source, Arabic-first autonomous AI agent framework engineered for durable execution, local context management, and cultural awareness. Built to shift large language models from passive chatbots to active, goal-oriented agents, Kazma serves as a robust orchestration layer capable of managing complex, multi-step workflows. The framework is designed for high-performance edge execution and is the foundation for the Scaleway 100 Billion Token Draw submission.

## 2. Technical Stack
* **Language:** Python (Strict versioning <3.14 for wheel compatibility)
* **Dependency & Environment Manager:** `uv`
* **Agent Orchestration:** LangGraph
* **Vector Database:** `sqlite-vec` (High-speed local semantic retrieval)
* **Model Routing:** LiteLLM (Unified mapping for local and cloud providers)
* **Backend API:** FastAPI
* **Frontend/Dashboard:** HTMX + Jinja2 + Alpine.js (12-tab settings, SSE chat, Arabic RTL)
* **Tool Execution:** UnifiedToolExecutor (single registry unifying local built-in tools and MCP remote tools)
* **Internationalization (i18n):** Bilingual EN/AR system with cookie middleware, shared Jinja2Templates, 150+ Arabic translations, and 71 RTL CSS selectors; Cairo font for native typography
* **Testing:** Pytest (3,510+ tests, asyncio-native)

## 3. Hardware & Deployment Profile
* **Target Architecture:** Local edge inference, WSL2/Ubuntu Linux environments, and native Windows.
* **Hardware Optimization:** Tuned to maximize performance on high-end local rigs (e.g., Core i9-14900K, RTX 4090 24GB VRAM, 64GB RAM) for offline quant execution.
* **File Naming Conventions:** Standardized using the `balfaris` convention across system and configuration files.
* **Portability:** Runs on Linux, macOS, Windows, Docker, and WSL with zero modifications — no hardcoded home paths, no OS-specific hooks, no architecture assumptions.

## 4. Core Subsystems

### 4.1 Bootstrap & Environment Guardrails
A robust "fail-fast" initialization layer that bypasses systemd conflicts. It validates hardware capabilities, Python versions, and network paths before the agent loop initiates, ensuring a deterministic startup environment. Cross-platform bootstrapping is provided by `setup.sh` (POSIX: Linux, macOS, WSL) and `setup.ps1` (Windows PowerShell). All data and config paths are portable and user-writable — no hardcoded home folders — and every path can be overridden via environment variables.

### 4.2 Durable Execution Engine (The ReAct Loop)
The core reasoning loop (Reason + Act) powered by LangGraph. It leverages SQLite checkpointing to maintain persistent state management, allowing agents to recover from interruptions, maintain context over long sessions, and execute complex logic chains safely. Session state is persisted through a unified session-store layer that merges checkpoint and session metadata into a single coherent store, with write serialization to prevent corruption races.

### 4.3 Majlis Mode (Cultural Context)
A native cultural adaptation module ensuring the framework defaults to Arabic-first sensibilities. This includes Right-to-Left (RTL) configuration support, local dialect handling (e.g., Kuwaiti contexts), region-specific protocol formatting, and the Cairo font family for native Arabic typography. A complete internationalization (i18n) layer provides bilingual EN/AR rendering across the Web UI: cookie-based locale middleware, a shared Jinja2Templates instance, 150+ Arabic translations, and 71 RTL-aware CSS selectors.

### 4.4 Tool Registry & Execution (UnifiedToolExecutor + MCP)
An extensible skill architecture built on the **UnifiedToolExecutor**, which consolidates the three formerly separate tool registries (local built-ins, MCP remote tools, and the legacy agent-internal registry) into a single routing surface. The UnifiedToolExecutor interfaces securely with REST APIs, performs local OS file-system operations governed by Human-in-the-Loop (HITL) approval gates, and dynamically loads custom tool manifests from the `kazma-skills/` directory. HITL gates are tiered (safe / warning / danger) and surface an inline approve/deny panel in the Web UI.

### 4.5 Security & Observability
Integrated Role-Based Access Control (RBAC) and delegation security to sandbox agent permissions. Production-grade tracing allows developers to monitor the agent's internal "thought process" and execution paths. The Web UI communicates exclusively through a service-layer facade — zero private attributes of the agent or stores are read directly from templates, keeping a clean security and refactoring boundary between the presentation and domain layers.

### 4.6 Service Facade & Web UI (Cross-Cutting)
All Web UI interactions are routed through a dedicated service-layer facade that exposes stable, validated operations to templates and SSE endpoints. This layer backs the 12-tab settings dashboard, the chat-model selector (with provider switch on save, SSE model passthrough, and API key validation), the HITL approval panel, session-history loading, the agents inspection page, and the swarm orchestration panel. The UI is fully bilingual (EN/AR) and dark-mode aware with WCAG-compliant dropdown contrast.

## 5. Development Roadmap & Phasing

### Phase 1: Foundation & Bootstrapping ✅ Complete
* ✅ Configure `uv` environment synchronization and dependency locking.
* ✅ Establish `setup.sh` (POSIX) and `setup.ps1` (Windows) for resilient environment validation.
* ✅ Integrate Pytest suite for baseline CI/CD checks (3,510+ tests).
* ✅ Set up basic configuration mapping (`kazma.yaml`).
* ✅ Portable, user-writable data paths with environment-variable overrides.

### Phase 2: Agentic Brain & Memory Pipeline ✅ Complete
* ✅ Implement LangGraph state machines and checkpointer nodes.
* ✅ Connect `sqlite-vec` for high-speed local vector storage.
* ✅ Integrate LiteLLM to route between local offline models and cloud providers.
* ✅ Bind the asynchronous Event Bus to capture and structure input/output.
* ✅ Unified session-store layer with write serialization.

### Phase 3: Tooling & The Action Dispatcher ✅ Complete
* ✅ Transition from a passive listener to an active ReAct reasoning loop.
* ✅ Build the `kazma-skills` manifest structure.
* ✅ Implement the Model Context Protocol (MCP) for tool binding.
* ✅ Engineer local file-system read/write capabilities governed by security protocols.
* ✅ Consolidate three legacy tool registries onto the **UnifiedToolExecutor**.

### Phase 4: Interface & Ecosystem ✅ Complete
* ✅ Scaffold the FastAPI backend to expose agent controls via REST.
* ✅ Build the reactive frontend dashboard (Jinja2 + Alpine.js, 12-tab settings, SSE chat).
* ✅ Implement observability tracing (e.g., Langfuse or OpenTelemetry) into the UI.
* ✅ Refine the Majlis Mode UI to handle RTL properly in the browser (Cairo font, 150+ translations, 71 RTL CSS selectors).
* ✅ Introduce the service-layer facade (zero private attribute access from UI).
* ✅ Bilingual EN/AR system with cookie middleware and shared Jinja2Templates.
* ✅ Dark-mode-aware UI with WCAG-compliant dropdown contrast.

### Phase 5: Submission & Release Prep ✅ Complete
* ✅ Finalize Dockerization for multi-node deployments.
* ✅ Complete API documentation and architecture diagrams.
* ✅ Structure the final repository and use-case narratives for the Scaleway 100 Billion Token Draw entry.

### Phase 6: Hardening & Security (In Progress — Post-Remediation Audit)
Identified by the post-remediation weak-points audit:

* **P0** — Add authentication to all API endpoints (settings, swarm, MCP, skills).
* **P0** — SSRF protection for URL-fetching tools (`read_url`, `vision_analyze`).
* **P0** — Add configurable CORS middleware to the FastAPI app.
* **P1** — Wrap blocking `web_search` call in `asyncio.to_thread`.
* **P1** — Bounded LRU eviction for `_thread_locks`, `_sessions`, `SessionManager`, `_checkpoint_locks`.
* **P1** — Error handlers must not leak `str(exc)` to clients.
* **P1** — Restrict `file_read.py` to the configured workspace root.
* **P2** — Resolve 208 remaining mypy type errors.
* **P2** — Add tests for `agent_runner.py`, `graph_builder.py`, and tool modules.
* **P2** — LLM call retry with backoff on the main graph path.
* **P2** — Improve SQLite "database is locked" error handling for concurrent access.

============================================================
============================================================

# كاظمه (Kazma) — المخطط المعماري الشامل للمشروع

## 1. رؤية المشروع (Project Vision)
كاظمه هو إطار عمل مفتوح المصدر (open-source) لوكلاء الذكاء الاصطناعي المستقلين (autonomous AI agents)، مصمم ليكون داعماً للغة العربية بالدرجة الأولى (Arabic-first)، ومهيأ للتنفيذ المتين (durable execution)، وإدارة السياق المحلي، والوعي الثقافي. تم بناء كاظمه لتحويل النماذج اللغوية الكبيرة (LLMs) من مجرد روبوتات محادثة سلبية إلى وكلاء نشطين وموجهين نحو الأهداف. يعمل الإطار كطبقة تنسيق (orchestration layer) قوية قادرة على إدارة سير عمل معقد ومتعدد الخطوات (multi-step workflows). تم تصميم الإطار ليعمل بأداء عالٍ على الحافة (edge execution) وهو الأساس للمشاركة في مسابقة Scaleway 100 Billion Token Draw.

## 2. الحزمة التقنية (Technical Stack)
* **اللغة:** Python (تحديد صارم للنسخة <3.14 لضمان توافق الـ wheel).
* **إدارة البيئة والاعتمادات (Dependency & Environment Manager):** `uv`
* **تنسيق الوكيل (Agent Orchestration):** LangGraph
* **قاعدة بيانات المتجهات (Vector Database):** `sqlite-vec` (استرجاع دلالي محلي عالي السرعة).
* **توجيه النماذج (Model Routing):** LiteLLM (توجيه موحد لموفري النماذج السحابية والمحلية).
* **الواجهة الخلفية (Backend API):** FastAPI
* **الواجهة الأمامية/لوحة التحكم (Frontend/Dashboard):** HTMX + Jinja2 + Alpine.js (12 تبويب إعدادات، دردشة SSE، دعم العربية RTL).
* **تنفيذ الأدوات (Tool Execution):** UnifiedToolExecutor (سجل موحد يجمع الأدوات المحلية المدمجة وأدوات MCP البعيدة في سطح توجيه واحد).
* **التدويل (Internationalization / i18n):** نظام ثنائي اللغة (EN/AR) مع middleware قائم على الكوكيز، Jinja2Templates مشتركة، أكثر من 150 ترجمة عربية، و71 محدد RTL في CSS، مع خط Cairo للطباعة العربية الأصلية.
* **الاختبار (Testing):** Pytest (أكثر من 3,510 اختبار، أصلي لـ asyncio).

## 3. مواصفات الأجهزة والنشر (Hardware & Deployment Profile)
* **البنية المستهدفة:** الاستدلال المحلي على الحافة (Local edge inference)، بيئات WSL2/Ubuntu Linux، و Windows الأصلي.
* **تحسين الأجهزة (Hardware Optimization):** مُحسّن لزيادة الأداء إلى أقصى حد على الأجهزة المحلية المتطورة (مثل: Core i9-14900K, RTX 4090 24GB VRAM, 64GB RAM) لتشغيل النماذج المكممة (offline quant execution) بدون اتصال بالإنترنت.
* **معايير تسمية الملفات (File Naming Conventions):** موحدة باستخدام معيار `balfaris` عبر جميع ملفات النظام والإعدادات.
* **قابلية النقل (Portability):** يعمل على Linux و macOS و Windows و Docker و WSL بدون أي تعديلات، بدون مسارات home مكتوبة بشكل ثابت، وبدون خطافات خاصة بنظام التشغيل.

## 4. الأنظمة الفرعية الأساسية (Core Subsystems)

### 4.1 التهيئة وحواجز حماية البيئة (Bootstrap & Environment Guardrails)
طبقة تهيئة قوية تعتمد مبدأ "الفشل السريع" (fail-fast) تتجاوز تعارضات systemd. تقوم هذه الطبقة بالتحقق من قدرات الأجهزة، ونسخ Python، ومسارات الشبكة قبل بدء حلقة الوكيل (agent loop)، مما يضمن بيئة تشغيل حتمية ومستقرة (deterministic environment). يتم توفير التهيئة عبر منصات متعددة عبر `setup.sh` (POSIX: Linux و macOS و WSL) و`setup.ps1` (Windows PowerShell). جميع مسارات البيانات والإعدادات محمولة وقابلة للكتابة من قبل المستخدم — لا توجد مسارات home مكتوبة بشكل ثابت — ويمكن تجاوز كل مسار عبر متغيرات البيئة.

### 4.2 محرك التنفيذ المتين (Durable Execution Engine / The ReAct Loop)
محرك التفكير الأساسي (Reason + Act) المدعوم بـ LangGraph. يستفيد من نقاط الحفظ (checkpointing) الخاصة بـ SQLite للحفاظ على الإدارة المستمرة للحالة (state management)، مما يسمح للوكلاء بالتعافي من الانقطاعات، والحفاظ على السياق خلال الجلسات الطويلة، وتنفيذ سلاسل منطقية معقدة بأمان. يتم الاحتفاظ بحالة الجلسة عبر طبقة موحدة لمخزن الجلسات (unified session-store) تدمج نقاط الحفظ وبيانات الجلسة في مخزن متماسك واحد، مع تسلسل عمليات الكتابة لمنع سباقات الفساد.

### 4.3 وضع المجلس (Majlis Mode - Cultural Context)
وحدة تكييف ثقافي أصلية تضمن أن الإطار يميل إلى الحساسيات الثقافية العربية أولاً. يشمل ذلك دعم الاتجاه من اليمين إلى اليسار (RTL) في الإعدادات، والتعامل مع اللهجات المحلية (مثل السياق الكويتي)، وتنسيق البروتوكولات المخصصة للمنطقة، وعائلة خط Cairo للطباعة العربية الأصلية. يوفر نظام التدويل (i18n) الكامل عرضًا ثنائي اللغة (EN/AR) عبر واجهة الويب: middleware للغة قائم على الكوكيز، نسخة Jinja2Templates مشتركة، أكثر من 150 ترجمة عربية، و71 محدد CSS يدعم الـ RTL.

### 4.4 سجل الأدوات والتنفيذ (Tool Registry & Execution - UnifiedToolExecutor + MCP)
بنية مهارات قابلة للتوسيع مبنية على **UnifiedToolExecutor**، الذي يدمج السجلات الثلاثة المنفصلة سابقًا (الأدوات المحلية المدمجة، وأدوات MCP البعيدة، والسجل الداخلي القديم للوكيل) في سطح توجيه واحد. يتفاعل UnifiedToolExecutor بشكل آمن مع واجهات برمجة التطبيقات (REST APIs)، وينفذ عمليات القراءة والكتابة على نظام التشغيل المحلي المحكومة ببوابات الموافقة البشرية (HITL)، ويحمل ديناميكيًا ملفات الأدوات المخصصة من مجلد `kazma-skills/`. بوابات HITL مصنفة (آمن / تحذير / خطر) وتظهر لوحة موافقة/رفض مدمجة في واجهة الويب.

### 4.5 الأمان والمراقبة (Security & Observability)
تحكم مدمج بالوصول المبني على الأدوار (RBAC) وبروتوكولات تفويض أمنية لإنشاء بيئة معزولة (sandbox) لصلاحيات الوكيل. تتيح أدوات التتبع (tracing) بمستوى الإنتاج للمطورين مراقبة "عملية التفكير" الداخلية للوكيل ومسارات التنفيذ بدقة. تتواصل واجهة الويب حصريًا من خلال واجهة طبقة خدمة (service-layer facade) — لا يتم قراءة أي سمات خاصة (private) للوكيل أو المخازن مباشرة من القوالب، مما يحافظ على حد أمني واضح للإعادة الهيكلة بين طبقة العرض وطبقة المجال.

### 4.6 واجهة الخدمة وواجهة الويب (Service Facade & Web UI - Cross-Cutting)
جميع تفاعلات واجهة الويب تمر عبر واجهة طبقة خدمة مخصصة تعرض عمليات مستقرة ومتحقق منها للقوالب ونقاط نهاية SSE. تدعم هذه الطبقة لوحة الإعدادات بـ 12 تبويبً، ومحدد نموذج الدردشة (مع تبديل الموفر عند الحفظ، تمرير النموذج عبر SSE، والتحقق من مفتاح API)، ولوحة موافقة HITL، تحميل سجل الجلسات، صفحة فحص الوكلاء، ولوحة تنسيق السرب (swarm). الواجهة ثنائية اللغة بالكامل (EN/AR) وتدعم الوضع الداكن مع تباين قوائم منسدلة متوافق مع WCAG.

## 5. خارطة طريق التطوير والمراحل (Development Roadmap & Phasing)

### المرحلة 1: التأسيس والتهيئة (Phase 1: Foundation & Bootstrapping) ✅ مكتملة
* ✅ إعداد مزامنة بيئة `uv` وتأمين الاعتمادات (dependency locking).
* ✅ إنشاء سكربتات `setup.sh` (POSIX) و`setup.ps1` (Windows) للتحقق المرن من البيئة وتجاوز مشاكل التثبيت.
* ✅ دمج حزمة Pytest لفحوصات CI/CD الأساسية (أكثر من 3,510 اختبار ناجح).
* ✅ إعداد ربط التكوينات الأساسية (`kazma.yaml`).
* ✅ مسارات بيانات محمولة وقابلة للكتابة من قبل المستخدم مع إمكانية التجاوز عبر متغيرات البيئة.

### المرحلة 2: العقل الوكيلي ومسار الذاكرة (Phase 2: Agentic Brain & Memory Pipeline) ✅ مكتملة
* ✅ تنفيذ آلات الحالة (state machines) ونقاط الحفظ (checkpointer nodes) في LangGraph.
* ✅ ربط `sqlite-vec` لتخزين واسترجاع المتجهات المحلية بسرعة عالية.
* ✅ دمج LiteLLM لتوجيه الطلبات بين النماذج المحلية (offline) وموفري الخدمات السحابية.
* ✅ ربط ناقل الأحداث غير المتزامن (asynchronous Event Bus) لالتقاط وهيكلة المدخلات والمخرجات.
* ✅ طبقة موحدة لمخزن الجلسات مع تسلسل عمليات الكتابة.

### المرحلة 3: الأدوات وموزع الإجراءات (Phase 3: Tooling & The Action Dispatcher) ✅ مكتملة
* ✅ الانتقال من وضع المستمع السلبي (passive listener) إلى حلقة تفكير ReAct نشطة.
* ✅ بناء هيكل ملفات المهارات `kazma-skills`.
* ✅ تنفيذ بروتوكول MCP لربط الأدوات بمرونة.
* ✅ هندسة قدرات القراءة/الكتابة في نظام الملفات المحلي المحكومة ببروتوكولات الأمان.
* ✅ دمج السجلات الثلاثة القديمة على **UnifiedToolExecutor**.

### المرحلة 4: الواجهة والنظام البيئي (Phase 4: Interface & Ecosystem) ✅ مكتملة
* ✅ بناء الواجهة الخلفية (FastAPI backend) للكشف عن أدوات التحكم بالوكيل عبر REST.
* ✅ بناء الواجهة الأمامية التفاعلية (Jinja2 + Alpine.js، 12 تبويب إعدادات، دردشة SSE).
* ✅ تنفيذ التتبع والمراقبة (observability tracing) مثل Langfuse أو OpenTelemetry داخل واجهة المستخدم.
* ✅ تحسين وضع المجلس (Majlis Mode) للتعامل مع الـ RTL بشكل صحيح في المتصفح (خط Cairo، أكثر من 150 ترجمة، 71 محدد RTL في CSS).
* ✅ إدخال واجهة طبقة الخدمة (service facade) — لا وصول للسمات الخاصة من واجهة المستخدم.
* ✅ نظام ثنائي اللغة (EN/AR) مع middleware للكوكيز وJinja2Templates مشتركة.
* ✅ واجهة تدعم الوضع الداكن مع تباين قوائم منسدلة متوافق مع WCAG.

### المرحلة 5: التجهيز للإصدار والمشاركة (Phase 5: Submission & Release Prep) ✅ مكتملة
* ✅ وضع اللمسات الأخيرة على تحزيم Docker لعمليات النشر متعددة العقد (multi-node deployments).
* ✅ إكمال توثيق API والمخططات المعمارية.
* ✅ هيكلة المستودع النهائي وصياغة حالات الاستخدام لتقديمها في مسابقة Scaleway 100 Billion Token Draw.

### المرحلة 6: التحصين والأمان (Phase 6: Hardening & Security) — قيد التقدم (مراجعة ما بعد المعالجة)
تم تحديدها بواسطة مراجعة النقاط الضعيفة بعد المعالجة:

* **P0** — إضافة المصادقة إلى جميع نقاط نهاية API (الإعدادات، السرب، MCP، المهارات).
* **P0** — حماية SSRF لأدوات جلب عناوين URL (`read_url`، `vision_analyze`).
* **P0** — إضافة middleware لـ CORS قابل للتكوين إلى تطبيق FastAPI.
* **P1** — تغليف استدعاء `web_search` الحظر في `asyncio.to_thread`.
* **P1** — إخلاء LRU محدود لـ `_thread_locks`، `_sessions`، `SessionManager`، `_checkpoint_locks`.
* **P1** — يجب ألا تسرب معالجات الأخطاء `str(exc)` إلى العملاء.
* **P1** — تقييد `file_read.py` على جذر مساحة العمل المكوّن.
* **P2** — حل 208 أخطاء أنواع mypy المتبقية.
* **P2** — إضافة اختبارات لـ `agent_runner.py`، `graph_builder.py`، ووحدات الأدوات.
* **P2** — إعادة محاولة استدعاءات LLM مع backoff على مسار الرسم البياني الرئيسي.
* **P2** — تحسين معالجة خطأ "database is locked" في SQLite للوصول المتزامن.

