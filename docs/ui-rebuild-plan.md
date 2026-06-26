# Kazma Web UI Rebuild Plan
## ✅ COMPLETED — June 26, 2026

### What Was Built
- **12 settings tabs**: Services, Models, Agent, Connectors, MCP, Skills, Appearance, Shortcuts, Account, Tools, System, Import/Export
- **9 built-in providers**: OpenAI, Anthropic, DeepSeek, Google, xAI, OpenRouter, Ollama, LM Studio, Custom
- **Real model discovery**: Fetch models from providers with API key authentication
- **Settings persistence**: SQLite config_store for all settings
- **Alpine.js + Jinja2**: Clean, responsive UI with dark theme

### Architecture
- **Framework**: Alpine.js + HTMX (already have these)
- **CSS**: Custom design system in kazma.css
- **Templates**: Jinja2 with components
- **State**: Alpine.js stores + SSE for real-time

### Phase 1: Core UI Foundation (Worker 1 - core)
**Files to create/modify:**
- `templates/base.html` - Master layout with sidebar, header, content area
- `templates/components/sidebar.html` - Navigation with sections
- `templates/components/header.html` - Top bar with actions
- `templates/components/modal.html` - Reusable modal system
- `templates/components/toast.html` - Notification system
- `static/css/kazma.css` - Complete design system
- `static/js/app.js` - Core Alpine stores, utilities

**Features:**
- Responsive sidebar (collapsible)
- Dark/light theme with CSS variables
- Modal system for settings, prompts
- Toast notifications
- Keyboard shortcuts (Ctrl+K search, etc.)

### Phase 2: Settings Panel (Worker 2 - bridge)
**Files to create/modify:**
- `templates/settings.html` - Full settings with 12+ tabs
- `static/js/settings.js` - Settings logic
- `static/js/providers.js` - Provider management
- `static/js/models.js` - Model discovery and testing

**Settings Tabs:**
1. Services/Providers - Add/remove providers, test connections
2. Models - Model registry, defaults, parameters
3. Agent - System prompt, personality, tools
4. Connectors - Telegram, Discord, Slack, Email
5. MCP - MCP server management
6. Skills - Skill browser and installer
7. Appearance - Theme, colors, layout
8. Shortcuts - Keyboard shortcuts
9. Account - Password, API tokens
10. Tools - Tool registry
11. System - Logs, backups, reset
12. Import/Export - YAML config

### Phase 3: Interactive Features (Worker 3 - ux)
**Files to create/modify:**
- `templates/chat.html` - Full chat interface
- `templates/dashboard.html` - Metrics and monitoring
- `templates/workspace.html` - Project workspace
- `static/js/chat.js` - Chat logic with streaming
- `static/js/dashboard.js` - Real-time metrics
- `static/js/swarm.js` - Swarm management UI

**Features:**
- Chat with streaming responses
- File upload/download
- Code execution results
- Real-time metrics (tokens, cost, latency)
- Swarm worker management
- Session history with search

### Priority Order
1. Base UI foundation (sidebar, theme, modals)
2. Settings panel with real functionality
3. Chat interface with streaming
4. Dashboard and monitoring
5. Swarm management UI
6. Polish and testing
