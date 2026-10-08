/* Slash-command catalog for the Web composer (extracted from chat.js).
   chat.js reads window.KAZMA_SLASH_COMMANDS; keep this file loaded first.

   A command's description is interface text: it comes from the catalog in
   the page's language through window.kazmaT (base.html defines it before
   this script), the English here being the fallback. The menu used to hold
   English only, so typing "/" in an Arabic chat opened twenty English lines
   (live, 2026-09-28).
*/
(function (root) {
  "use strict";
  function d(key, en) {
    var f = root.kazmaT;
    return typeof f === "function" ? f(key, en) : en;
  }
  root.KAZMA_SLASH_COMMANDS = [
    { cmd: '/yolo', desc: d('chat.slash.yolo', 'Skip danger-tool approvals for this session (TTL)') },
    { cmd: '/yolo off', desc: d('chat.slash.yolo_off', 'Restore HITL approvals + clear tool grants') },
    { cmd: '/yolo status', desc: d('chat.slash.yolo_status', 'Show YOLO / grant status for this session') },
    { cmd: '/long', desc: d('chat.slash.long', 'Show iteration budget + HITL status') },
    { cmd: '/long on', desc: d('chat.slash.long_on', 'Research budget (40 rounds) — HITL still on') },
    { cmd: '/long mission', desc: d('chat.slash.long_mission', 'Run until done (hard wall ~500 rounds)') },
    { cmd: '/long yolo', desc: d('chat.slash.long_yolo', 'Research budget AND skip danger-tool approvals') },
    { cmd: '/unrestricted', desc: d('chat.slash.unrestricted', 'Mission + YOLO — finish this job, don’t ask') },
    { cmd: '/unrestricted off', desc: d('chat.slash.unrestricted_off', 'Restore Settings budget + HITL') },
    { cmd: '/long off', desc: d('chat.slash.long_off', 'Budget only off (HITL unchanged)') },
    { cmd: '/plan', desc: d('chat.slash.plan', 'Show plan-mode status (inspect then propose)') },
    { cmd: '/plan on', desc: d('chat.slash.plan_on', 'Plan mode — write/exec tools blocked until /plan go') },
    { cmd: '/plan go', desc: d('chat.slash.plan_go', 'Approve the plan and execute (HITL still on)') },
    { cmd: '/plan off', desc: d('chat.slash.plan_off', 'Leave plan mode') },
    { cmd: '/new', desc: d('chat.slash.new', 'Start a new chat session') },
    { cmd: '/reset', desc: d('chat.slash.reset', 'Clear this conversation history') },
    { cmd: '/steer', insert: '/steer ', desc: d('chat.slash.steer', 'Queue a note for the running task — edit, then Enter') },
    { cmd: '/steer!', insert: '/steer! ', desc: d('chat.slash.steer_bang', 'Pause the running task and inject a requirement') },
    { cmd: '/abort', desc: d('chat.slash.abort', 'Stop and abandon the running task') },
    { cmd: '/help', desc: d('chat.slash.help', 'List available slash commands') },
  ];
  var catalogNode = root.document && root.document.getElementById('kazma-command-catalog');
  if (catalogNode) {
    var catalog = JSON.parse(catalogNode.textContent);
    catalog.forEach(function (entry) {
      var cmd = '/' + entry.name;
      var existing = root.KAZMA_SLASH_COMMANDS.find(function (row) { return row.cmd === cmd; });
      var row = existing || {
        cmd: cmd,
        insert: cmd + (entry.arguments ? ' ' : ''),
        desc: root.KAZMA_LANG === 'ar' ? entry.description_ar : entry.description,
      };
      if (entry.arguments) row.desc += ' · ' + entry.arguments;
      if (entry.page) row.desc += ' · ' + d('chat.slash.page_action', 'Manage on its page');
      if (entry.admin_options) row.desc += ' · ' + d('chat.slash.admin_options', 'Some options require an administrator');
      if (!existing) root.KAZMA_SLASH_COMMANDS.push(row);
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
