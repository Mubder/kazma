/* Slash-command catalog for the Web composer (extracted from chat.js).
   chat.js reads window.KAZMA_SLASH_COMMANDS; keep this file loaded first.
*/
(function (root) {
  "use strict";
  root.KAZMA_SLASH_COMMANDS = [
    { cmd: '/yolo', desc: 'Skip danger-tool approvals for this session (TTL)' },
    { cmd: '/yolo off', desc: 'Restore HITL approvals + clear tool grants' },
    { cmd: '/yolo status', desc: 'Show YOLO / grant status for this session' },
    { cmd: '/long', desc: 'Show iteration budget + HITL status' },
    { cmd: '/long on', desc: 'Research budget (40 rounds) — HITL still on' },
    { cmd: '/long mission', desc: 'Run until done (hard wall ~500 rounds)' },
    { cmd: '/long yolo', desc: 'Research budget AND skip danger-tool approvals' },
    { cmd: '/unrestricted', desc: 'Mission + YOLO — finish this job, don’t ask' },
    { cmd: '/unrestricted off', desc: 'Restore Settings budget + HITL' },
    { cmd: '/long off', desc: 'Budget only off (HITL unchanged)' },
    { cmd: '/plan', desc: 'Show plan-mode status (inspect then propose)' },
    { cmd: '/plan on', desc: 'Plan mode — write/exec tools blocked until /plan go' },
    { cmd: '/plan go', desc: 'Approve the plan and execute (HITL still on)' },
    { cmd: '/plan off', desc: 'Leave plan mode' },
    { cmd: '/new', desc: 'Start a new chat session' },
    { cmd: '/reset', desc: 'Clear this conversation history' },
    { cmd: '/steer', insert: '/steer ', desc: 'Queue a note for the running task — edit, then Enter' },
    { cmd: '/steer!', insert: '/steer! ', desc: 'Pause the running task and inject a requirement' },
    { cmd: '/abort', desc: 'Stop and abandon the running task' },
    { cmd: '/help', desc: 'List available slash commands' },
  ];
})(typeof window !== "undefined" ? window : globalThis);
