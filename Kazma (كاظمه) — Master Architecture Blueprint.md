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
* **Frontend/Dashboard:** HTMX
* **Testing:** Pytest

## 3. Hardware & Deployment Profile
* **Target Architecture:** Local edge inference and WSL2/Ubuntu Linux environments.
* **Hardware Optimization:** Tuned to maximize performance on high-end local rigs (e.g., Core i9-14900K, RTX 4090 24GB VRAM, 64GB RAM) for offline quant execution.
* **File Naming Conventions:** Standardized using the `balfaris` convention across system and configuration files.

## 4. Core Subsystems

### 4.1 Bootstrap & Environment Guardrails
A robust "fail-fast" initialization layer (`setup.sh`) that bypasses systemd conflicts. It validates hardware capabilities, Python versions, and network paths before the agent loop initiates, ensuring a deterministic startup environment.

### 4.2 Durable Execution Engine (The ReAct Loop)
The core reasoning loop (Reason + Act) powered by LangGraph. It leverages SQLite checkpointing to maintain persistent state management, allowing agents to recover from interruptions, maintain context over long sessions, and execute complex logic chains safely.

### 4.3 Majlis Mode (Cultural Context)
A native cultural adaptation module ensuring the framework defaults to Arabic-first sensibilities. This includes Right-to-Left (RTL) configuration support, local dialect handling (e.g., Kuwaiti contexts), and region-specific protocol formatting.

### 4.4 Tool Registry & Execution (MCP)
An extensible skill architecture utilizing the Model Context Protocol (MCP). This allows the agent to securely interface with REST APIs, perform local OS file system operations, and dynamically load custom tool manifests from the `kazma-skills/` directory.

### 4.5 Security & Observability
Integrated Role-Based Access Control (RBAC) and delegation security to sandbox agent permissions. Production-grade tracing allows developers to monitor the agent's internal "thought process" and execution paths.

## 5. Development Roadmap & Phasing

### Phase 1: Foundation & Bootstrapping
* Configure `uv` environment synchronization and dependency locking.
* Establish `setup.sh` for resilient environment validation.
* Integrate Pytest suite for baseline CI/CD checks.
* Set up basic configuration mapping (`kazma.yaml`).

### Phase 2: Agentic Brain & Memory Pipeline
* Implement LangGraph state machines and checkpointer nodes.
* Connect `sqlite-vec` for high-speed local vector storage.
* Integrate LiteLLM to route between local offline models and cloud providers.
* Bind the asynchronous Event Bus to capture and structure input/output.

### Phase 3: Tooling & The Action Dispatcher
* Transition from a passive listener to an active ReAct reasoning loop.
* Build the `kazma-skills` manifest structure.
* Implement the Model Context Protocol (MCP) for tool binding.
* Engineer local file-system read/write capabilities governed by security protocols.

### Phase 4: Interface & Ecosystem
* Scaffold the FastAPI backend to expose agent controls via REST.
* Build the reactive HTMX frontend dashboard for lightweight interaction.
* Implement observability tracing (e.g., Langfuse or OpenTelemetry) into the UI.
* Refine the Majlis Mode UI to handle RTL properly in the browser.

### Phase 5: Submission & Release Prep
* Finalize Dockerization for multi-node deployments.
* Complete API documentation and architecture diagrams.
* Structure the final repository and use-case narratives for the Scaleway 100 Billion Token Draw entry.

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
* **الواجهة الأمامية/لوحة التحكم (Frontend/Dashboard):** HTMX
* **الاختبار (Testing):** Pytest

## 3. مواصفات الأجهزة والنشر (Hardware & Deployment Profile)
* **البنية المستهدفة:** الاستدلال المحلي على الحافة (Local edge inference) وبيئات WSL2/Ubuntu Linux.
* **تحسين الأجهزة (Hardware Optimization):** مُحسّن لزيادة الأداء إلى أقصى حد على الأجهزة المحلية المتطورة (مثل: Core i9-14900K, RTX 4090 24GB VRAM, 64GB RAM) لتشغيل النماذج المكممة (offline quant execution) بدون اتصال بالإنترنت.
* **معايير تسمية الملفات (File Naming Conventions):** موحدة باستخدام معيار `balfaris` عبر جميع ملفات النظام والإعدادات.

## 4. الأنظمة الفرعية الأساسية (Core Subsystems)

### 4.1 التهيئة وحواجز حماية البيئة (Bootstrap & Environment Guardrails)
طبقة تهيئة قوية تعتمد مبدأ "الفشل السريع" (fail-fast) عبر سكربت `setup.sh` الذي يتجاوز تعارضات systemd. تقوم هذه الطبقة بالتحقق من قدرات الأجهزة، ونسخ Python، ومسارات الشبكة قبل بدء حلقة الوكيل (agent loop)، مما يضمن بيئة تشغيل حتمية ومستقرة (deterministic environment).

### 4.2 محرك التنفيذ المتين (Durable Execution Engine / The ReAct Loop)
محرك التفكير الأساسي (Reason + Act) المدعوم بـ LangGraph. يستفيد من نقاط الحفظ (checkpointing) الخاصة بـ SQLite للحفاظ على الإدارة المستمرة للحالة (state management)، مما يسمح للوكلاء بالتعافي من الانقطاعات، والحفاظ على السياق خلال الجلسات الطويلة، وتنفيذ سلاسل منطقية معقدة بأمان.

### 4.3 وضع المجلس (Majlis Mode - Cultural Context)
وحدة تكييف ثقافي أصلية تضمن أن الإطار يميل إلى الحساسيات الثقافية العربية أولاً. يشمل ذلك دعم الاتجاه من اليمين إلى اليسار (RTL) في الإعدادات، والتعامل مع اللهجات المحلية (مثل السياق الكويتي)، وتنسيق البروتوكولات المخصصة للمنطقة.

### 4.4 سجل الأدوات والتنفيذ (Tool Registry & Execution - MCP)
بنية مهارات قابلة للتوسيع تستخدم بروتوكول Model Context Protocol (MCP). يسمح هذا للوكيل بالتفاعل الآمن مع واجهات برمجة التطبيقات (REST APIs)، وتنفيذ عمليات القراءة والكتابة على نظام التشغيل المحلي (local OS file system)، والتحميل الديناميكي لملفات الأدوات (tool manifests) المخصصة من مجلد `kazma-skills/`.

### 4.5 الأمان والمراقبة (Security & Observability)
تحكم مدمج بالوصول المبني على الأدوار (RBAC) وبروتوكولات تفويض أمنية لإنشاء بيئة معزولة (sandbox) لصلاحيات الوكيل. تتيح أدوات التتبع (tracing) بمستوى الإنتاج للمطورين مراقبة "عملية التفكير" الداخلية للوكيل ومسارات التنفيذ بدقة.

## 5. خارطة طريق التطوير والمراحل (Development Roadmap & Phasing)

### المرحلة 1: التأسيس والتهيئة (Phase 1: Foundation & Bootstrapping)
* إعداد مزامنة بيئة `uv` وتأمين الاعتمادات (dependency locking).
* إنشاء سكربت `setup.sh` للتحقق المرن من البيئة وتجاوز مشاكل التثبيت.
* دمج حزمة Pytest لفحوصات CI/CD الأساسية (979+ اختبار ناجح).
* إعداد ربط التكوينات الأساسية (`kazma.yaml`).

### المرحلة 2: العقل الوكيلي ومسار الذاكرة (Phase 2: Agentic Brain & Memory Pipeline)
* تنفيذ آلات الحالة (state machines) ونقاط الحفظ (checkpointer nodes) في LangGraph.
* ربط `sqlite-vec` لتخزين واسترجاع المتجهات المحلية بسرعة عالية.
* دمج LiteLLM لتوجيه الطلبات بين النماذج المحلية (offline) وموفري الخدمات السحابية.
* ربط ناقل الأحداث غير المتزامن (asynchronous Event Bus) لالتقاط وهيكلة المدخلات والمخرجات.

### المرحلة 3: الأدوات وموزع الإجراءات (Phase 3: Tooling & The Action Dispatcher)
* الانتقال من وضع المستمع السلبي (passive listener) إلى حلقة تفكير ReAct نشطة.
* بناء هيكل ملفات المهارات `kazma-skills`.
* تنفيذ بروتوكول MCP لربط الأدوات بمرونة.
* هندسة قدرات القراءة/الكتابة في نظام الملفات المحلي المحكومة ببروتوكولات الأمان.

### المرحلة 4: الواجهة والنظام البيئي (Phase 4: Interface & Ecosystem)
* بناء الواجهة الخلفية (FastAPI backend) للكشف عن أدوات التحكم بالوكيل عبر REST.
* بناء الواجهة الأمامية التفاعلية (reactive HTMX dashboard) لتفاعل خفيف وسريع.
* تنفيذ التتبع والمراقبة (observability tracing) مثل Langfuse أو OpenTelemetry داخل واجهة المستخدم.
* تحسين وضع المجلس (Majlis Mode) للتعامل مع الـ RTL بشكل صحيح في المتصفح.

### المرحلة 5: التجهيز للإصدار والمشاركة (Phase 5: Submission & Release Prep)
* وضع اللمسات الأخيرة على تحزيم Docker لعمليات النشر متعددة العقد (multi-node deployments).
* إكمال توثيق API والمخططات المعمارية.
* هيكلة المستودع النهائي وصياغة حالات الاستخدام لتقديمها في مسابقة Scaleway 100 Billion Token Draw.

