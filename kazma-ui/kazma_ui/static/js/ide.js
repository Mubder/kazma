/* ═══════════════════════════════════════════════════════
   Kazma IDE — Web transport for the transport-agnostic
   IdeService. File tree, CodeMirror 5 fromTextArea (vendored
   under static/vendor/codemirror — no CDN, works air-gapped;
   the textarea still shows the file if the bundle fails to
   load), save/run/git/grep/swarm.
   All writes/execs flow through /api/ide/* which reuses the
   shared HITL/safety chain — no parallel un-gated path.
   ═══════════════════════════════════════════════════════ */

/* CodeMirror instance must stay OFF the Alpine object (same hang as Monaco).
   File bytes are written to #ide-fallback first; CM wraps that textarea. */
var _ideCM = null;
function _ed() { return _ideCM; }

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
    cmReady: false,
    lspReady: false,
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
    reviewOpen: false,
    review: { id: '', files: [] },

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
      var self = this;
      window.kazmaOnSoftNavLeave = function () { self.destroy(); };
    },

    destroy() {
      try { if (this.chatStream && this.chatStream.abort) this.chatStream.abort(); } catch (e) {}
      this.chatStream = null;
      try { if (this._themeObs) this._themeObs.disconnect(); } catch (e) {}
      this._themeObs = null;
      try {
        if (this._onWinResize) window.removeEventListener('resize', this._onWinResize);
      } catch (e) {}
      this._onWinResize = null;
      try { if (this._lspDiagTimer) clearTimeout(this._lspDiagTimer); } catch (e) {}
      this._lspDiagTimer = null;
      this._lspAnnotations = [];
      this._lspBound = false;
      this.lspReady = false;
      try {
        if (_ideCM && typeof _ideCM.toTextArea === 'function') _ideCM.toTextArea();
      } catch (e) {}
      _ideCM = null;
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

    // ── Editor: textarea first, CodeMirror if present ──
    _cmTheme() {
      return document.documentElement.getAttribute('data-theme') === 'light'
        ? 'eclipse'
        : 'nord';
    },

    /* Indentation guides. CodeMirror 5 ships none, so draw one marker per
       indent level on each rendered line. Only visible lines fire renderLine,
       so this stays cheap on a big file. */
    _installIndentGuides(cm) {
      var unit = cm.getOption('indentUnit') || 4;
      // Must track .CodeMirror pre.CodeMirror-line's padding-left in ide.html.
      var LINE_PAD = 8;
      cm.on('renderLine', function (instance, line, elt) {
        var lead = /^[ \t]*/.exec(line.text || '')[0];
        if (!lead) return;
        var cols = 0;
        for (var i = 0; i < lead.length; i++) {
          cols += lead[i] === '\t' ? unit - (cols % unit) : 1;
        }
        var levels = Math.floor(cols / unit);
        if (levels < 1) return;
        var charW = instance.defaultCharWidth();
        for (var l = 0; l < levels; l++) {
          var guide = document.createElement('span');
          guide.className = 'cm-indent-guide';
          guide.style.left = LINE_PAD + l * unit * charW + 'px';
          elt.appendChild(guide);
        }
      });
    },

    initEditor() {
      var ta = document.getElementById('ide-fallback');
      var self = this;
      if (ta) {
        ta.addEventListener('input', function () {
          if (self.cmReady) return;
          self.dirty = ta.value !== self.originalContent;
          var tab = self._activeTab();
          if (tab && tab.dirty !== self.dirty) tab.dirty = self.dirty;
        });
      }
      if (!ta || !window.CodeMirror) return;
      try {
        _ideCM = window.CodeMirror.fromTextArea(ta, {
          lineNumbers: true,
          lineWrapping: false,
          indentUnit: 4,
          tabSize: 4,
          indentWithTabs: false,
          smartIndent: true,
          electricChars: true,
          matchBrackets: true,
          autoCloseBrackets: true,
          autoCloseTags: true,
          styleActiveLine: true,
          styleSelectedText: true,
          showTrailingSpace: true,
          highlightSelectionMatches: { annotateScrollbar: true, delay: 150 },
          foldGutter: true,
          gutters: [
            'CodeMirror-lint-markers',
            'CodeMirror-linenumbers',
            'CodeMirror-foldgutter',
          ],
          scrollbarStyle: 'overlay',
          rulers: [{ column: 100, lineStyle: 'dashed' }],
          // Sublime bindings: Ctrl-D multi-select, Ctrl-/ comment,
          // Alt-Up/Down move line, Ctrl-Shift-K delete line.
          keyMap: 'sublime',
          extraKeys: {
            'Ctrl-S': function () { self.save(); },
            'Cmd-S': function () { self.save(); },
            'Ctrl-F': 'findPersistent',
            'Cmd-F': 'findPersistent',
            'Ctrl-Space': function (cm) { self._complete(cm); },
            'Alt-G': 'jumpToLine',
            Tab: function (cm) {
              if (cm.somethingSelected()) return cm.indentSelection('add');
              return cm.execCommand('insertSoftTab');
            },
          },
          theme: self._cmTheme(),
          mode: 'null',
        });
        _ideCM.setSize('100%', '100%');
        self._installIndentGuides(_ideCM);
        _ideCM.on('change', function () {
          if (self._settingContent) return;
          var dirty = _ed().getValue() !== self.originalContent;
          if (self.dirty !== dirty) self.dirty = dirty;
          var tab = self._activeTab();
          if (tab && tab.dirty !== dirty) tab.dirty = dirty;
        });
        self.cmReady = true;
        // Opt-in and self-disabling: returns immediately unless /api/ide/lsp
        // reports enabled. Nothing below the editor depends on it.
        self._bindLsp();
        self._themeObs = new MutationObserver(function () {
          if (_ed()) _ed().setOption('theme', self._cmTheme());
        });
        self._themeObs.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ['data-theme'],
        });
      } catch (err) {
        console.warn('[ide] CodeMirror init failed — plain textarea', err);
        _ideCM = null;
        self.cmReady = false;
      }
    },

    getContent() {
      if (_ed() && typeof _ed().getValue === 'function') return _ed().getValue();
      var ta = document.getElementById('ide-fallback');
      return ta ? ta.value : '';
    },

    setContent(text) {
      text = text == null ? '' : String(text);
      this._settingContent = true;
      this.originalContent = text;
      this.dirty = false;
      try {
        var ta = document.getElementById('ide-fallback');
        if (ta) ta.value = text;
        if (_ed() && typeof _ed().setValue === 'function') {
          _ed().setValue(text);
          if (typeof _ed().clearHistory === 'function') _ed().clearHistory();
        }
      } finally {
        this._settingContent = false;
        this.dirty = false;
      }
      this._layoutEditor();
    },

    _layoutEditor() {
      if (!_ed() || typeof _ed().refresh !== 'function') return;
      var cm = _ed();
      requestAnimationFrame(function () {
        try { cm.refresh(); } catch (e) { /* ignore */ }
      });
    },

    /* Resolve a CodeMirror mode. The filename wins when we have one: meta.js
       knows far more extensions than the hand-map below, so Rust, Go, TOML,
       Dockerfile and friends light up without a table entry each. */
    _cmMode(lang, path) {
      var CM = window.CodeMirror;
      if (path && CM && CM.findModeByFileName) {
        var hit = CM.findModeByFileName(String(path).split(/[\\/]/).pop());
        if (hit && hit.mode !== 'null') return hit.mime || hit.mode;
      }
      var mapped = {
        python: 'python',
        javascript: 'javascript',
        typescript: 'javascript',
        json: 'application/json',
        html: 'htmlmixed',
        css: 'css',
        markdown: 'markdown',
        bash: 'shell',
        shell: 'shell',
        yaml: 'yaml',
        sql: 'sql',
        c: 'text/x-csrc',
        cpp: 'text/x-c++src',
        java: 'text/x-java',
        csharp: 'text/x-csharp',
      }[lang];
      if (mapped) return mapped;
      if (lang && CM && CM.findModeByName) {
        var byName = CM.findModeByName(lang);
        if (byName && byName.mode !== 'null') return byName.mime || byName.mode;
      }
      return 'null';
    },

    // ── LSP (completion / definition / diagnostics) ───────────────────────
    /* Ported from Monaco to CodeMirror when the editor was swapped. The server
       half is `/api/ide/lsp`; when it reports disabled, `lspReady` stays false
       and every entry point below degrades to plain-editor behaviour. */
    async _bindLsp() {
      if (this._lspBound) return;
      try {
        var st = await this._get('/api/ide/lsp');
        if (!st || !st.enabled) return;
      } catch (e) {
        return;
      }
      var cm = _ed();
      if (!cm) return;
      var self = this;
      this._lspBound = true;
      this.lspReady = true;
      this._lspAnnotations = [];
      // The lint addon owns the gutter markers; we only feed it. lintOnChange
      // is off because diagnostics arrive from the server on our own debounce.
      cm.setOption('lint', {
        lintOnChange: false,
        getAnnotations: function () { return self._lspAnnotations || []; },
      });
      this._scheduleLspDiagnostics();
    },

    /* Completion source behind Ctrl-Space. Falls back to CodeMirror's
       any-word hints when the server is off or returns nothing — a dumb
       popup beats an empty one. */
    _complete(cm) {
      var CM = window.CodeMirror;
      if (!CM || !CM.showHint || !cm) return;
      var self = this;
      var anyword = (CM.hint && CM.hint.anyword) || null;
      if (!this.lspReady) {
        if (anyword) CM.showHint(cm, anyword, { completeSingle: false });
        return;
      }
      CM.showHint(cm, function (editor, cb) {
        var cur = editor.getCursor();
        var token = editor.getTokenAt(cur);
        var start = /[\w.]/.test(token.string || '') ? token.start : cur.ch;
        self._lsp('complete', {
          line: cur.line,
          character: cur.ch,
          prefix: editor.getRange({ line: cur.line, ch: start }, cur),
        }).then(function (data) {
          var items = (data && data.items) || [];
          if (!items.length) return cb(anyword ? anyword(editor) : null);
          return cb({
            list: items.slice(0, 100).map(function (it) {
              return {
                text: it.insertText || it.label || '',
                displayText: it.label || it.insertText || '',
              };
            }),
            from: { line: cur.line, ch: start },
            to: cur,
          });
        }).catch(function () {
          cb(anyword ? anyword(editor) : null);
        });
      }, { completeSingle: false, async: true });
    },

    /* Jump to definition. A same-file target just moves the cursor; anything
       else goes through `open()` so the tab bar and the read path stay in
       charge of loading it. */
    async gotoDefinition() {
      var cm = _ed();
      if (!this.lspReady || !cm) return;
      var cur = cm.getCursor();
      var data = await this._lsp('definition', { line: cur.line, character: cur.ch });
      var locs = (data && data.locations) || [];
      if (!locs.length) return;
      var loc = locs[0];
      var pos = {
        line: Math.max(0, (loc.line || 1) - 1),
        ch: Math.max(0, (loc.character || 1) - 1),
      };
      if (this._lspSamePath(loc.path)) {
        cm.setCursor(pos);
        cm.scrollIntoView(pos, 120);
        cm.focus();
        return;
      }
      if (!loc.path) return;
      await this.open(loc.path);
      var opened = _ed();
      if (!opened) return;
      opened.setCursor(pos);
      opened.scrollIntoView(pos, 120);
    },

    _lspSamePath(rel) {
      var a = String(this.currentFile || '').replace(/\\/g, '/');
      var b = String(rel || '').replace(/\\/g, '/');
      return !!a && (a === b || a.endsWith('/' + b) || b.endsWith('/' + a));
    },

    async _lsp(method, extra) {
      extra = extra || {};
      try {
        return await this._post('/api/ide/lsp', {
          method: method,
          path: this.currentFile || '',
          content: this.getContent(),
          line: extra.line || 0,
          character: extra.character || 0,
          prefix: extra.prefix || '',
        });
      } catch (e) {
        return { ok: false };
      }
    },

    _scheduleLspDiagnostics() {
      var self = this;
      if (!this.lspReady || !this.currentFile) return;
      if (this._lspDiagTimer) clearTimeout(this._lspDiagTimer);
      this._lspDiagTimer = setTimeout(function () { self._refreshLspDiagnostics(); }, 400);
    },

    async _refreshLspDiagnostics() {
      if (!this.lspReady || !_ed()) return;
      var data = await this._lsp('diagnostics', {});
      this._applyLspDiagnostics((data && data.diagnostics) || []);
    },

    _applyLspDiagnostics(diags) {
      var cm = _ed();
      var CM = window.CodeMirror;
      if (!cm || !CM || !CM.Pos) return;
      this._lspAnnotations = (diags || []).map(function (d) {
        var line = Math.max(0, (d.line || 1) - 1);
        var ch = Math.max(0, (d.character || 1) - 1);
        return {
          from: CM.Pos(line, ch),
          to: CM.Pos(line, ch + 8),
          message: d.message || 'error',
          severity: d.severity === 'warning' ? 'warning' : 'error',
          source: d.source || 'kazma',
        };
      });
      if (cm.getOption('lint') && typeof cm.performLint === 'function') {
        try { cm.performLint(); } catch (e) { /* ignore */ }
      }
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
        this.tree = (data.files || []).map(function (f) {
          var name = String(f.name || '');
          f.ext = f.is_dir ? 'dir' : (name.split('.').pop() || '').toLowerCase();
          return f;
        });
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

    openFile(path) {
      return this.open(path);
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
      try {
        var data = await this._get('/api/ide/read?path=' + encodeURIComponent(path));
        if (!data.ok) {
          this.showResult('Read failed', data.error || 'Unknown error');
          return;
        }
        var filePath = data.path || path;
        var lang = data.lang || this._langFromName(filePath);
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
      }
    },

    // ── Multi-tab helpers ──
    _activeTab() {
      var self = this;
      return this.tabs.find(function (t) { return t.path === self.activeTabPath; }) || null;
    },

    // Save the current editor content + dirty state back into the tab.
    _captureToTab() {
      var tab = this._activeTab();
      if (!tab) return;
      tab.content = this.getContent();
      tab.dirty = this.dirty;
    },

    // Load a tab's saved state into the editor and update the UI.
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
      this.currentLang = (tab.lang && tab.lang !== 'plaintext')
        ? tab.lang
        : this._langFromName(tab.path);
      if (_ed() && typeof _ed().setOption === 'function') {
        try { _ed().setOption('mode', this._cmMode(this.currentLang, tab.path)); } catch (e) { /* ignore */ }
      }
      this.setContent(tab.content || '');
      this.originalContent = tab.original || '';
      this.dirty = !!tab.dirty;
      this._scheduleLspDiagnostics();
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
          : await window.confirm('"' + tab.name + '" has unsaved changes. Close anyway?');
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
            title: 'New file',
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
      return {py:'python',pyw:'python',js:'javascript',mjs:'javascript',cjs:'javascript',
              ts:'typescript',tsx:'typescript',jsx:'javascript',
              html:'html',htm:'html',css:'css',scss:'scss',less:'less',
              json:'json',md:'markdown',markdown:'markdown',
              sh:'shell',bash:'shell',zsh:'shell',ps1:'powershell',
              yml:'yaml',yaml:'yaml',toml:'ini',ini:'ini',
              sql:'sql',rs:'rust',go:'go',java:'java',kt:'kotlin',
              c:'c',h:'c',cpp:'cpp',hpp:'cpp',cs:'csharp',
              xml:'xml',svg:'xml',rb:'ruby',php:'php',
              dockerfile:'dockerfile'}[ext] || 'plaintext';
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
        : await window.confirm('Delete "' + this.currentFile + '"?\nThis cannot be undone.');
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

    reviewHunkHtml(file) {
      var esc = function (s) {
        return String(s || ' ').replace(/&/g, '&amp;').replace(/</g, '&lt;');
      };
      var diff = String((file && file.diff) || '');
      if (diff) {
        return diff.split('\n').slice(0, 120).map(function (ln) {
          var cls = 'hitl-diff-ctx';
          if (ln.charAt(0) === '+' && ln.charAt(1) !== '+') cls = 'hitl-diff-add';
          else if (ln.charAt(0) === '-' && ln.charAt(1) !== '-') cls = 'hitl-diff-del';
          else if (ln.indexOf('@@') === 0 || ln.indexOf('diff ') === 0 || ln.indexOf('---') === 0 || ln.indexOf('+++') === 0) cls = 'hitl-diff-meta';
          return '<div class="' + cls + '">' + esc(ln) + '</div>';
        }).join('');
      }
      var lines = [];
      String((file && file.before) || '').split('\n').slice(0, 40).forEach(function (ln) {
        lines.push('<div class="hitl-diff-del">- ' + esc(ln) + '</div>');
      });
      String((file && file.after) || '').split('\n').slice(0, 40).forEach(function (ln) {
        lines.push('<div class="hitl-diff-add">+ ' + esc(ln) + '</div>');
      });
      return lines.join('');
    },

    async openLatestReview() {
      try {
        var listed = await this._get('/api/ide/checkpoints');
        var items = (listed && listed.checkpoints) || [];
        if (!items.length) return;
        var cid = items[0].id;
        var data = await this._get('/api/ide/checkpoints/' + encodeURIComponent(cid) + '/review');
        if (!data || !data.ok) return;
        var changed = (data.files || []).filter(function (f) { return f.changed; });
        if (!changed.length) {
          this.reviewOpen = false;
          return;
        }
        var self = this;
        changed.forEach(function (f) {
          f.hunks = (f.hunks || []).map(function (h) {
            return {
              index: h.index,
              header: h.header,
              diff: h.diff,
              html: self.reviewHunkHtml({ diff: h.diff }),
            };
          });
        });
        this.review = { id: data.id, files: changed };
        this.reviewOpen = true;
      } catch (err) { /* ignore */ }
    },

    acceptReview() {
      this.reviewOpen = false;
    },

    async rejectHunk(path, hunkIndex) {
      if (!this.review.id || !path) return;
      this.busy = true;
      try {
        var data = await this._post(
          '/api/ide/checkpoints/' + encodeURIComponent(this.review.id) + '/restore-hunk',
          { path: path, hunk_index: hunkIndex }
        );
        if (data.ok) {
          this.toast('Hunk restored', true);
          await this.openLatestReview();
          if (this.currentFile) this.openFile(this.currentFile);
        } else {
          this.toast('Hunk restore failed', false);
        }
      } catch (err) {
        this.toast('Hunk restore failed', false);
      } finally {
        this.busy = false;
      }
    },

    async rejectFile(path) {
      if (!this.review.id || !path) return;
      var ok = window.kazmaConfirm
        ? await window.kazmaConfirm({
            title: 'Reject this file',
            message: 'Restore this file from the pre-patch checkpoint?',
            confirmText: 'Restore file',
            danger: true,
          })
        : true;
      if (!ok) return;
      this.busy = true;
      try {
        var data = await this._post(
          '/api/ide/checkpoints/' + encodeURIComponent(this.review.id) + '/restore-path',
          { path: path }
        );
        if (data.ok) {
          this.toast('Restored ' + path, true);
          await this.openLatestReview();
          if (this.currentFile) this.openFile(this.currentFile);
        } else {
          this.toast('Restore failed', false);
        }
      } catch (err) {
        this.toast('Restore failed', false);
      } finally {
        this.busy = false;
      }
    },

    async rejectReview() {
      var ok = window.kazmaConfirm
        ? await window.kazmaConfirm({
            title: 'Reject patches',
            message: 'Restore the pre-patch checkpoint? This overwrites files on disk.',
            confirmText: 'Restore',
            danger: true,
          })
        : true;
      if (!ok || !this.review.id) return;
      this.busy = true;
      try {
        var data = await this._post(
          '/api/ide/checkpoints/' + encodeURIComponent(this.review.id) + '/restore',
          {}
        );
        if (data.ok) {
          this.toast('Restored checkpoint', true);
          this.reviewOpen = false;
          if (this.currentFile) this.openFile(this.currentFile);
        } else {
          this.toast('Restore failed', false);
        }
      } catch (err) {
        this.toast('Restore failed', false);
      } finally {
        this.busy = false;
      }
    },

    // ── Restore last workspace file checkpoint (Hands 0.11) ──
    async restoreLastCheckpoint() {
      var ok = window.kazmaConfirm
        ? await window.kazmaConfirm({
            title: 'Restore checkpoint',
            message: 'Restore the last workspace file checkpoint? This overwrites files on disk.',
            confirmText: 'Restore',
          })
        : true;
      if (!ok) return;
      this.busy = true;
      try {
        var listed = await this._get('/api/ide/checkpoints');
        var items = (listed && listed.checkpoints) || [];
        if (!items.length) {
          this.toast('No checkpoints yet', false);
          return;
        }
        var cid = items[0].id;
        var data = await this._post('/api/ide/checkpoints/' + encodeURIComponent(cid) + '/restore', {});
        if (data.ok) {
          this.toast('Restored ' + cid.slice(0, 8), true);
          this.showResult('Restore', (data.paths || []).join('\n') || 'OK');
          if (this.currentFile) this.openFile(this.currentFile);
        } else {
          this.toast('Restore failed', false);
          this.showResult('Restore failed', data.error || 'Unknown error');
        }
      } catch (err) {
        this.toast('Restore failed', false);
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
            self.openLatestReview();
          },
          onError: function (errMsg) {
            self.chatMessages[asstIdx].content += '\n\n[!] ' + (errMsg || 'Stream error');
            self.chatBusy = false;
            self.chatStream = null;
          },
        });
      } catch (err) {
        self.chatMessages[asstIdx].content += '\n\n[!] ' + err;
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

    // After an agent tool call, if it wrote to a file that's open in a tab,
    // refresh that tab. Handles both the active file and files open in other
    // tabs (marks them stale so the user knows to refresh).
    async _maybeRefreshOpenFile(toolName, resultText) {
      var writeTools = ['file_write', 'file_delete'];
      if (writeTools.indexOf(toolName) === -1) return;

      // Try to extract the written file path from the tool result.
      // The agent's file_write/file_delete tools return text containing the
      // path. Fall back to the active file if we can't parse it.
      var writtenPath = this._extractPathFromResult(resultText) || this.currentFile;
      if (!writtenPath) return;

      // Find the tab matching the written path (may be the active tab or a
      // background tab).
      var affectedTab = this.tabs.find(function (t) { return t.path === writtenPath; });
      if (!affectedTab) return;  // file isn't open in any tab — nothing to do

      if (toolName === 'file_delete') {
        this.closeTabSilent(writtenPath);
        if (writtenPath !== this.currentFile) {
          this.toast(writtenPath + ' was deleted', false);
        } else {
          this.toast('Open file was deleted', false);
        }
        return;
      }

      // file_write: if it's the active tab and the user has no unsaved edits,
      // re-read and refresh the editor live.
      if (writtenPath === this.currentFile && !this.dirty) {
        try {
          var data = await this._get('/api/ide/read?path=' + encodeURIComponent(writtenPath));
          if (data.ok) {
            this.setContent(data.content || '');
            affectedTab.content = data.content || '';
            affectedTab.original = data.content || '';
            affectedTab.dirty = false;
            this.toast('Updated ' + writtenPath, true);
          }
        } catch (e) { /* non-fatal */ }
      } else if (writtenPath === this.currentFile && this.dirty) {
        // Active tab but user has unsaved edits — don't clobber.
        this.toast(writtenPath + ' changed on disk — save or discard to refresh', false);
      } else {
        // Background tab was edited — mark it stale so the user knows.
        affectedTab.original = '__STALE__';
        this.toast(writtenPath + ' was modified — switch to it and reload', true);
      }
    },

    // Best-effort extract a file path from a tool result string.
    _extractPathFromResult(resultText) {
      if (!resultText) return null;
      var text = typeof resultText === 'string' ? resultText : String(resultText);
      // The file_write tool returns "Wrote <path>" or similar.
      // The file_delete tool returns "Deleted: <path>".
      var match = text.match(/(?:Wrote|Written|Deleted?|Saved)[:\s]+([^\s,\n]+)/i);
      return match ? match[1].replace(/['"]/g, '') : null;
    },

    _renderMd(text) {
      if (window.KazmaStream && window.KazmaStream.markdown) {
        return window.KazmaStream.markdown(text || '');
      }
      return (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

    // ── Resizable panes ──────────────────────────────────────────
    startResize(pane, e) {
      e.preventDefault();
      var self = this;
      var layout = document.querySelector('.ide-layout');
      if (!layout) return;
      var startX = e.clientX;
      var startTree = parseInt(getComputedStyle(layout).getPropertyValue('--ide-tree-w') || '260');
      var startChat = parseInt(getComputedStyle(layout).getPropertyValue('--ide-chat-w') || '380');

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      function onMove(ev) {
        var dx = ev.clientX - startX;
        if (pane === 'tree') {
          var newTree = Math.max(160, Math.min(500, startTree + dx));
          layout.style.setProperty('--ide-tree-w', newTree + 'px');
        } else if (pane === 'chat') {
          var newChat = Math.max(250, Math.min(700, startChat - dx));
          layout.style.setProperty('--ide-chat-w', newChat + 'px');
        }
      }

      function onUp() {
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    },
  };
}
if (typeof window !== "undefined") window.ideApp = ideApp;
