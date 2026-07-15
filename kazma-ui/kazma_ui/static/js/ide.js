/* ═══════════════════════════════════════════════════════
   Kazma IDE — Web transport for the transport-agnostic
   IdeService. File tree, CodeMirror editor (CDN, with a
   graceful <textarea> fallback), save/run/git/grep/swarm.
   All writes/execs flow through /api/ide/* which reuses the
   shared HITL/safety chain — no parallel un-gated path.
   ═══════════════════════════════════════════════════════ */

function ideApp() {
  return {
    // ── State ──
    tree: [],
    treePath: '',
    currentFile: '',
    currentLang: 'plaintext',
    originalContent: '',
    dirty: false,
    busy: false,
    command: '',
    grepPattern: '',
    grepGlob: '*.py',
    swarmInstruction: '',
    result: '',
    resultTitle: '',
    cm: null,
    cmReady: false,
    skills: [],
    selectedSkill: '',
    // ── Multi-tab state ──
    tabs: [],
    activeTabPath: '',
    // ── Chat panel state ──
    chatOpen: true,
    chatMessages: [],
    chatInput: '',
    chatBusy: false,
    chatSessionId: '',
    chatStream: null,

    // ── i18n-safe toast ──
    toast(msg, ok) {
      if (window.KazmaStream && window.KazmaStream.toast) {
        window.KazmaStream.toast(msg, ok ? 'success' : 'error', 4000);
      }
    },

    // ── Init ──
    init() {
      this.initEditor();
      this.loadTree('');
      this.loadSkills();
      this.initChat();
    },

    // ── Chat bootstrap (shared by init + toggleChat) ──
    initChat() {
      if (this.chatSessionId) return;
      // Dedicated IDE thread (separate from the main /chat history).
      this.chatSessionId = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID() : ('ide-' + Date.now());
      this.chatMessages.push({
        role: 'system',
        content: 'Ask about the open file, request edits, or run commands. ' +
                 'The agent knows your workspace, repo, and tools.',
      });
    },

    // ── Coding skills (refactor/tests/lint/review → swarm) ──
    async loadSkills() {
      try {
        var data = await this._get('/api/ide/skills');
        if (data.ok) {
          this.skills = data.skills || [];
          if (this.skills.length) {
            this.selectedSkill = this.skills[0].name;
          }
        }
      } catch (err) {
        // Non-fatal — skills are optional.
      }
    },

    async runSkill() {
      if (!this.currentFile || !this.selectedSkill) return;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/skill', {
          skill: this.selectedSkill,
          path: this.currentFile,
        });
        if (data.ok) {
          this.showResult('Skill: ' + this.selectedSkill,
            'Task ID: ' + (data.task_id || '(unknown)'));
          this.toast(this.selectedSkill + ' dispatched', true);
        } else {
          this.showResult('Skill failed', data.error || 'Unknown error');
          this.toast('Skill failed', false);
        }
      } catch (err) {
        this.toast('Skill failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Editor (CodeMirror with textarea fallback) ──
    initEditor() {
      var ta = this.$refs.editor;
      if (!ta) return;
      if (typeof window.CodeMirror === 'function') {
        try {
          this.cm = window.CodeMirror.fromTextArea(ta, {
            lineNumbers: true,
            theme: 'material-darker',
            mode: 'text/plain',
            lineWrapping: true,
            indentUnit: 4,
          });
          var self = this;
          this.cm.on('change', function () {
            self.dirty = self.cm.getValue() !== self.originalContent;
            // Keep the active tab's dirty flag in sync so the tab bar
            // dot indicator updates live as the user types.
            var tab = self._activeTab();
            if (tab) tab.dirty = self.dirty;
          });
          this.cmReady = true;
          return;
        } catch (err) {
          console.warn('[ide] CodeMirror init failed, falling back to textarea', err);
          this.cm = null;
          this.cmReady = false;
        }
      }
      // Fallback: raw <textarea>
      var self2 = this;
      ta.addEventListener('input', function () {
        self2.dirty = ta.value !== self2.originalContent;
      });
    },

    getContent() {
      return this.cm ? this.cm.getValue() : (this.$refs.editor ? this.$refs.editor.value : '');
    },

    setContent(text) {
      if (this.cm) {
        this.cm.setValue(text);
      } else if (this.$refs.editor) {
        this.$refs.editor.value = text;
      }
      this.originalContent = text;
      this.dirty = false;
    },

    _cmMode(lang) {
      return {
        python: 'python',
        javascript: 'javascript',
        typescript: 'text/typescript',
        json: 'application/json',
        html: 'htmlmixed',
        css: 'css',
        markdown: 'markdown',
        bash: 'shell',
        yaml: 'yaml',
      }[lang] || 'text/plain';
    },

    // ── HTTP helpers ──
    async _get(url) {
      var r = await fetch(url);
      return r.json();
    },
    async _post(url, body) {
      var r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      return r.json();
    },

    // ── File tree (reuses the read-only workspace API) ──
    async loadTree(path) {
      this.busy = true;
      try {
        var data = await this._get('/api/workspace/files?path=' + encodeURIComponent(path || ''));
        this.tree = data.files || [];
        this.treePath = data.path || '';
      } catch (err) {
        this.toast('Failed to list files', false);
      } finally {
        this.busy = false;
      }
    },

    navigate(item) {
      if (item.is_dir) {
        this.loadTree(item.path);
      } else {
        this.open(item.path);
      }
    },

    goUp() {
      if (!this.treePath) return;
      var parts = this.treePath.split('/');
      parts.pop();
      this.loadTree(parts.join('/'));
    },

    // ── Open ──
    async open(path) {
      // If the file is already open in a tab, just switch to it (no re-read).
      var existing = this.tabs.find(function (t) { return t.path === path; });
      if (existing) {
        this.switchTab(path);
        return;
      }
      this.busy = true;
      try {
        var data = await this._get('/api/ide/read?path=' + encodeURIComponent(path));
        if (!data.ok) {
          this.showResult('Read failed', data.error || 'Unknown error');
          return;
        }
        var filePath = data.path || path;
        var lang = data.lang || 'plaintext';
        // Capture any edits in the current tab before creating a new one.
        this._captureToTab();
        this.tabs.push({
          path: filePath,
          name: filePath.split('/').pop(),
          lang: lang,
          content: data.content || '',
          original: data.content || '',
          dirty: false,
        });
        this.activeTabPath = filePath;
        this._loadFromTab(this._activeTab());
      } catch (err) {
        this.toast('Open failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Multi-tab helpers ──
    _activeTab() {
      var self = this;
      return this.tabs.find(function (t) { return t.path === self.activeTabPath; }) || null;
    },

    // Save the current CodeMirror content + dirty state back into the tab.
    _captureToTab() {
      var tab = this._activeTab();
      if (!tab) return;
      tab.content = this.getContent();
      tab.dirty = this.dirty;
    },

    // Load a tab's saved state into CodeMirror and update the UI.
    _loadFromTab(tab) {
      if (!tab) {
        this.currentFile = '';
        this.currentLang = 'plaintext';
        this.originalContent = '';
        this.setContent('');
        return;
      }
      this.activeTabPath = tab.path;
      this.currentFile = tab.path;
      this.currentLang = tab.lang;
      if (this.cm) {
        this.cm.setOption('mode', this._cmMode(tab.lang));
      }
      this.setContent(tab.content || '');
      this.originalContent = tab.original || '';
      this.dirty = !!tab.dirty;
    },

    switchTab(path) {
      if (path === this.activeTabPath) return;
      this._captureToTab();
      var tab = this.tabs.find(function (t) { return t.path === path; });
      if (tab) {
        this._loadFromTab(tab);
      }
    },

    async closeTab(path) {
      var idx = this.tabs.findIndex(function (t) { return t.path === path; });
      if (idx === -1) return;
      var tab = this.tabs[idx];
      // Warn if the tab being closed has unsaved edits.
      if ((path === this.activeTabPath ? this.dirty : tab.dirty)) {
        var ok = window.kazmaConfirm
          ? await window.kazmaConfirm({
              title: 'Close tab',
              message: '"' + tab.name + '" has unsaved changes. Close anyway?',
              confirmText: 'Close', danger: true,
            })
          : window.confirm('"' + tab.name + '" has unsaved changes. Close anyway?');
        if (!ok) return;
      }
      this.tabs.splice(idx, 1);
      if (path === this.activeTabPath) {
        // Activate the neighbor tab, or clear the editor.
        var next = this.tabs[idx] || this.tabs[idx - 1] || null;
        if (next) {
          this._loadFromTab(next);
        } else {
          this.currentFile = '';
          this.setContent('');
          this.activeTabPath = '';
        }
      }
    },

    // ── New file (uses the unified kazmaPrompt dialog) ──
    async newFile() {
      var name = window.kazmaPrompt
        ? await window.kazmaPrompt({
            title: '📄 New file',
            message: 'Path (relative to workspace root)',
            placeholder: 'e.g. src/new_module.py',
            defaultValue: 'new_file.py',
            confirmText: 'Create',
          })
        : window.prompt('New file path:', 'new_file.py');
      if (!name || !name.trim()) return;
      name = name.trim();
      this._captureToTab();
      this.tabs.push({
        path: name,
        name: name.split('/').pop(),
        lang: this._langFromName(name),
        content: '',
        original: '',
        dirty: false,
      });
      this._loadFromTab(this.tabs[this.tabs.length - 1]);
      this.toast('New file — press Save to create it', true);
    },

    _langFromName(name) {
      var ext = (name.split('.').pop() || '').toLowerCase();
      return {py:'python',js:'javascript',ts:'typescript',html:'html',css:'css',
              json:'json',md:'markdown',sh:'bash',yml:'yaml',yaml:'yaml',
              toml:'toml',sql:'sql',rs:'rust',go:'go'}[ext] || 'plaintext';
    },

    // ── Delete current file (HITL-gated) ──
    async deleteFile() {
      if (!this.currentFile) return;
      var ok = window.kazmaConfirm
        ? await window.kazmaConfirm({
            title: 'Delete file',
            message: 'Delete "' + this.currentFile + '"?\nThis cannot be undone.',
            confirmText: 'Delete', danger: true,
          })
        : window.confirm('Delete "' + this.currentFile + '"?\nThis cannot be undone.');
      if (!ok) return;
      var delPath = this.currentFile;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/delete', { path: delPath });
        if (data.ok) {
          this.toast('Deleted ' + delPath, true);
          // Remove the tab for the deleted file.
          this.closeTabSilent(delPath);
          this.loadTree(this.treePath);
        } else {
          this.showResult('Delete failed', data.error || 'Unknown error');
          this.toast('Delete failed (approval may be pending)', false);
        }
      } catch (err) {
        this.toast('Delete failed', false);
      } finally {
        this.busy = false;
      }
    },

    // Close a tab without the unsaved-changes confirmation (for deletion).
    closeTabSilent(path) {
      var idx = this.tabs.findIndex(function (t) { return t.path === path; });
      if (idx === -1) {
        // Not in a tab (e.g. agent deleted it) — just clear if active.
        if (path === this.activeTabPath) {
          this.currentFile = ''; this.setContent(''); this.activeTabPath = '';
        }
        return;
      }
      this.tabs.splice(idx, 1);
      if (path === this.activeTabPath) {
        var next = this.tabs[idx] || this.tabs[idx - 1] || null;
        if (next) { this._loadFromTab(next); }
        else { this.currentFile = ''; this.setContent(''); this.activeTabPath = ''; }
      }
    },

    // ── Save (HITL-gated via file_write) ──
    async save() {
      if (!this.currentFile) return;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/write', {
          path: this.currentFile,
          content: this.getContent(),
        });
        if (data.ok) {
          var saved = this.getContent();
          this.originalContent = saved;
          this.dirty = false;
          // Sync the active tab.
          var tab = this._activeTab();
          if (tab) { tab.original = saved; tab.content = saved; tab.dirty = false; }
          this.toast('Saved ' + this.currentFile, true);
          this.showResult('Save', data.output || 'OK');
        } else {
          this.showResult('Save failed', data.error || data.output || 'Unknown error');
          this.toast('Save failed (approval may be pending)', false);
        }
      } catch (err) {
        this.toast('Save failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Run current file ──
    async runFile() {
      if (!this.currentFile) return;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/runfile', { path: this.currentFile });
        this.showResult('Run: ' + this.currentFile, data.ok ? data.output : (data.error || data.output));
      } catch (err) {
        this.toast('Run failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Run arbitrary command ──
    async runCommand() {
      var cmd = (this.command || '').trim();
      if (!cmd) return;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/run', { command: cmd });
        this.showResult('$ ' + cmd, data.ok ? data.output : (data.error || data.output));
      } catch (err) {
        this.toast('Command failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Git ──
    async gitCmd(sub) {
      this.busy = true;
      try {
        var data = await this._post('/api/ide/git', { subcommand: sub });
        this.showResult('git ' + sub, data.ok ? (data.output || '(clean)') : (data.error || data.output));
      } catch (err) {
        this.toast('Git failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Diff current editor vs saved-on-disk ──
    async showDiff() {
      if (!this.currentFile) return;
      this.busy = true;
      try {
        var data = await this._post('/api/ide/diff', {
          path: this.currentFile,
          old: this.originalContent,
          new: this.getContent(),
        });
        this.showResult('Diff: ' + this.currentFile,
          data.ok ? (data.changed ? data.diff : '(no changes)') : (data.error || ''));
      } catch (err) {
        this.toast('Diff failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Grep ──
    async grep() {
      var pat = (this.grepPattern || '').trim();
      if (!pat) return;
      this.busy = true;
      try {
        var url = '/api/ide/grep?pattern=' + encodeURIComponent(pat) +
                  '&glob=' + encodeURIComponent(this.grepGlob || '*');
        var data = await this._get(url);
        this.showResult('Grep: ' + pat,
          data.ok ? ((data.matches || []).join('\n') || '(no matches)') : (data.error || ''));
      } catch (err) {
        this.toast('Grep failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Send to swarm (uses current file as context) ──
    async sendToSwarm() {
      var instr = (this.swarmInstruction || '').trim();
      if (!instr) return;
      this.busy = true;
      try {
        var ctx = this.currentFile
          ? ('File: ' + this.currentFile + '\n\n' + this.getContent())
          : '';
        var data = await this._post('/api/ide/swarm', {
          instruction: instr,
          pattern: 'auto',
          context: ctx,
        });
        if (data.ok) {
          this.showResult('Swarm dispatched', 'Task ID: ' + (data.task_id || '(unknown)'));
          this.toast('Sent to swarm', true);
          this.swarmInstruction = '';
        } else {
          this.showResult('Swarm failed', data.error || 'Unknown error');
          this.toast('Swarm dispatch failed', false);
        }
      } catch (err) {
        this.toast('Swarm failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Results panel ──
    showResult(title, text) {
      this.resultTitle = title;
      this.result = (text === undefined || text === null) ? '' : String(text);
    },

    // ════════════════════════════════════════════════════════════════
    // AI CHAT PANEL (reuses /api/chat/stream — no parallel path)
    // ════════════════════════════════════════════════════════════════

    toggleChat() {
      this.chatOpen = !this.chatOpen;
      if (this.chatOpen) {
        this.initChat();
        var self = this;
        this.$nextTick(function () {
          self.scrollChat();
          var inp = document.getElementById('ide-chat-input');
          if (inp) inp.focus();
        });
      }
    },

    _chatContext() {
      // Build the IDE context preamble so the agent knows what file is open.
      if (!this.currentFile) return '';
      return 'The user has this file open in the IDE:\nFile: ' + this.currentFile +
             '\nLanguage: ' + this.currentLang;
    },

    async sendChat() {
      var msg = (this.chatInput || '').trim();
      if (!msg || this.chatBusy) return;
      // Require KazmaStream (loaded via streaming.js in ide.html).
      if (!window.KazmaStream || !window.KazmaStream.sse) {
        this.toast('Chat streaming unavailable (streaming.js not loaded)', false);
        return;
      }

      // Push the user bubble + reserve an assistant bubble to stream into.
      this.chatMessages.push({ role: 'user', content: msg });
      var asstIdx = this.chatMessages.push({ role: 'assistant', content: '' }) - 1;
      this.chatInput = '';
      this.chatBusy = true;
      var self = this;

      try {
        this.chatStream = window.KazmaStream.sse('/api/chat/stream', {
          message: msg,
          session_id: this.chatSessionId,
          context: this._chatContext(),
        }, {
          onToken: function (data) {
            self.chatMessages[asstIdx].content += (data.content || '');
            self.scrollChat();
          },
          onToolCall: function (data) {
            self.chatMessages.splice(asstIdx + 1, 0, {
              role: 'tool_call',
              tool: data.tool_name || 'tool',
              args: data.inputs || {},
            });
            asstIdx++;
            self.scrollChat();
          },
          onToolResult: function (data) {
            self.chatMessages.splice(asstIdx + 1, 0, {
              role: 'tool_result',
              tool: data.tool_name || 'tool',
              result: data.result || '',
            });
            asstIdx++;
            self.scrollChat();
            // If the agent wrote to the currently-open file AND the user
            // has no unsaved local edits, silently refresh the editor so
            // the chat→edit loop closes. Never clobber the user's work.
            self._maybeRefreshOpenFile(data.tool_name, data.result);
          },
          onApprovalRequired: function (data) {
            self.chatMessages.splice(asstIdx + 1, 0, {
              role: 'approval',
              thread_id: data.thread_id,
              tool: data.tool,
              message: data.message,
            });
            asstIdx++;
            self.scrollChat();
          },
          onDone: function () {
            self.chatBusy = false;
            self.chatStream = null;
          },
          onError: function (errMsg) {
            self.chatMessages[asstIdx].content += '\n\n⚠️ ' + (errMsg || 'Stream error');
            self.chatBusy = false;
            self.chatStream = null;
          },
        });
      } catch (err) {
        self.chatMessages[asstIdx].content += '\n\n⚠️ ' + err;
        self.chatBusy = false;
        self.chatStream = null;
      }
    },

    abortChat() {
      if (this.chatStream && this.chatStream.abort) {
        this.chatStream.abort();
      }
      this.chatBusy = false;
      this.chatStream = null;
    },

    clearChat() {
      this.chatMessages = [];
      // New thread on clear.
      this.chatSessionId = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID() : ('ide-' + Date.now());
    },

    chatKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendChat();
      }
    },

    scrollChat() {
      var box = document.getElementById('ide-chat-messages');
      if (box) box.scrollTop = box.scrollHeight;
    },

    _fmtToolArgs(args) {
      try { return (typeof args === 'string') ? args : JSON.stringify(args, null, 2); }
      catch (e) { return String(args); }
    },

    // After an agent tool call, if it wrote to the open file and the user
    // has no unsaved edits, refresh the editor to show the change.
    async _maybeRefreshOpenFile(toolName, resultText) {
      if (!this.currentFile) return;
      var writeTools = ['file_write', 'file_delete'];
      if (writeTools.indexOf(toolName) === -1) return;
      // Don't clobber unsaved user edits.
      if (this.dirty) {
        this.toast('File changed on disk — save or discard to refresh', false);
        return;
      }
      if (toolName === 'file_delete') {
        // The open file was deleted — clear the editor + tab.
        this.closeTabSilent(this.currentFile);
        this.toast('Open file was deleted', false);
        return;
      }
      // file_write: re-read the open file silently.
      try {
        var data = await this._get('/api/ide/read?path=' + encodeURIComponent(this.currentFile));
        if (data.ok) {
          this.setContent(data.content || '');
          // Sync the active tab so the saved state matches.
          var tab = this._activeTab();
          if (tab) {
            tab.content = data.content || '';
            tab.original = data.content || '';
            tab.dirty = false;
          }
          this.toast('Updated ' + this.currentFile, true);
        }
      } catch (e) { /* non-fatal */ }
    },

    _renderMd(text) {
      if (window.KazmaStream && window.KazmaStream.markdown) {
        return window.KazmaStream.markdown(text || '');
      }
      return (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
  };
}
