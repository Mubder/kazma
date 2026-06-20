# Kazma - 🇰🇼 - كاظمة

**Status: ALPHA — Stable Architecture, Experimental API**

Autonomous AI agent framework — Python 3.11+, asyncio-native, sqlite-vec only.

![Tests](https://img.shields.io/badge/tests-979_passing-green)
![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-MIT-blue)
![Status](https://img.shields.io/badge/status-production_ready-brightgreen)

---

## 🌍 Overview

Kazma is a production-grade, domain-agnostic, open-source framework for building reliable AI agents. We built Kazma because the gap between "cool demo" and "reliable agent" is enormous.

**Core Pillars:**

*   **Durable Execution**: Built on LangGraph/SQLite, Kazma supports checkpointing that survives SIGKILL. Your agents resume mid-task, never losing state.
*   **Context Authority**: Implements a strict 80% compaction loop, preventing context window exhaustion and hallucination spirals.
*   **Cultural Moat**: Native support for Arabic (MSA/Gulf dialects) with a "Majlis Mode" protocol for culturally appropriate conversational pacing.
*   **MCP Interoperability**: Native Model Context Protocol (MCP) support — access 177,000+ ecosystem tools with zero vendor lock-in.

---

## 🇰🇼 نظرة عامة (Arabic Overview)

كاظمة هو إطار عمل مفتوح المصدر ومستقل لبناء وكلاء ذكاء اصطناعي (AI Agents) موثوقين وقابلين للتطوير. تم تصميم كاظمة للبيئات التي تتطلب دقة عالية وتوافقاً ثقافياً.

**لماذا كاظمة؟**

*   **التنفيذ المتين**: حفظ تلقائي للحالة؛ إذا تعطل النظام، يكمل الوكيل عمله من نفس النقطة.
*   **سلطة السياق**: آلية ذكية لتلخيص المحادثات والحفاظ على المعلومات المهمة.
*   **هوية ثقافية**: دعم أصيل للغة العربية (الفصحى واللهجة الكويتية) مع بروتوكول "المجلس".
*   **تكامل MCP**: توافق كامل مع بروتوكول سياق النموذج (MCP).

---

## 🏗 Architecture
