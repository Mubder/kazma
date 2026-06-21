/* Kazma Chat — WebSocket streaming chat interface */

var chatSessionId = null;
var ws = null;
var currentAssistantEl = null;
var tokenBuffer = '';

function connectWs() {
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(protocol + '//' + location.host + '/ws/chat');

    ws.onopen = function() {
        console.log('WebSocket connected');
    };

    ws.onmessage = function(event) {
        var data = JSON.parse(event.data);
        handleWsEvent(data);
    };

    ws.onclose = function() {
        console.log('WebSocket disconnected, reconnecting in 2s...');
        setTimeout(connectWs, 2000);
    };

    ws.onerror = function(err) {
        console.error('WebSocket error:', err);
    };
}

function handleWsEvent(data) {
    var messagesEl = document.getElementById('messages');
    var thinkingEl = document.getElementById('thinking-indicator');

    switch (data.type) {
        case 'session':
            chatSessionId = data.session_id;
            break;

        case 'thinking':
            thinkingEl.style.display = 'flex';
            break;

        case 'token':
            thinkingEl.style.display = 'none';
            if (!currentAssistantEl) {
                currentAssistantEl = createMessageEl('assistant');
                tokenBuffer = '';
            }
            tokenBuffer += data.content;
            var textEl = currentAssistantEl.querySelector('.message-text');
            textEl.innerHTML = renderMarkdown(tokenBuffer);
            scrollToBottom();
            break;

        case 'tool_call':
            if (!currentAssistantEl) {
                currentAssistantEl = createMessageEl('assistant');
            }
            var toolBox = document.createElement('div');
            toolBox.className = 'tool-call-box';
            toolBox.innerHTML = '<span class="tool-name">' + escapeHtml(data.name) + '</span> ' +
                '<code>' + escapeHtml(truncate(data.args, 100)) + '</code>';
            currentAssistantEl.querySelector('.message-content').appendChild(toolBox);
            scrollToBottom();
            break;

        case 'tool_result':
            if (currentAssistantEl) {
                var resultBox = document.createElement('div');
                resultBox.className = 'tool-result-box';
                resultBox.innerHTML = '<strong>Result:</strong> ' + escapeHtml(truncate(data.result, 300));
                currentAssistantEl.querySelector('.message-content').appendChild(resultBox);
            }
            scrollToBottom();
            break;

        case 'done':
            thinkingEl.style.display = 'none';
            currentAssistantEl = null;
            tokenBuffer = '';
            if (data.cost !== undefined) {
                document.getElementById('session-cost').textContent = '$' + data.cost.toFixed(4);
            }
            if (data.tokens !== undefined) {
                document.getElementById('session-tokens').textContent = data.tokens + ' tokens';
            }
            enableInput();
            break;

        case 'error':
            thinkingEl.style.display = 'none';
            if (!currentAssistantEl) {
                currentAssistantEl = createMessageEl('assistant');
            }
            var textEl = currentAssistantEl.querySelector('.message-text');
            textEl.innerHTML = '<span class="text-error">' + escapeHtml(data.content) + '</span>';
            currentAssistantEl = null;
            enableInput();
            break;
    }
}

function createMessageEl(role) {
    var messagesEl = document.getElementById('messages');
    // Remove welcome if present
    var welcome = messagesEl.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    var div = document.createElement('div');
    div.className = 'message message-' + role;

    var avatar = role === 'user' ? 'You' : 'K';
    div.innerHTML =
        '<div class="message-avatar">' + avatar + '</div>' +
        '<div class="message-content">' +
            '<div class="message-text"></div>' +
            '<div class="message-meta"></div>' +
        '</div>';

    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

function sendMessage() {
    var input = document.getElementById('chat-input');
    var text = input.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    // Create user message
    var userEl = createMessageEl('user');
    userEl.querySelector('.message-text').textContent = text;

    // Send over WebSocket
    ws.send(JSON.stringify({
        type: 'message',
        content: text,
        session_id: chatSessionId
    }));

    input.value = '';
    input.style.height = 'auto';
    disableInput();
}

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}

function disableInput() {
    var input = document.getElementById('chat-input');
    var btn = document.getElementById('send-btn');
    input.disabled = true;
    btn.disabled = true;
}

function enableInput() {
    var input = document.getElementById('chat-input');
    var btn = document.getElementById('send-btn');
    input.disabled = false;
    btn.disabled = false;
    input.focus();
}

function scrollToBottom() {
    var el = document.getElementById('messages');
    el.scrollTop = el.scrollHeight;
}

function newSession() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'clear' }));
    }
    var messagesEl = document.getElementById('messages');
    messagesEl.innerHTML =
        '<div class="chat-welcome">' +
            '<div class="welcome-icon">&#x1F30A;</div>' +
            '<h2>Kazma</h2>' +
            '<p>How can I help you today?</p>' +
        '</div>';
    document.getElementById('session-cost').textContent = '$0.00';
    document.getElementById('session-tokens').textContent = '0 tokens';
    currentAssistantEl = null;
    tokenBuffer = '';
}

function loadSession(sessionId) {
    // Load session messages via API
    fetch('/api/chat/sessions/' + sessionId + '/messages')
        .then(function(r) { return r.json(); })
        .then(function(messages) {
            var messagesEl = document.getElementById('messages');
            messagesEl.innerHTML = '';
            messages.forEach(function(msg) {
                var el = createMessageEl(msg.role);
                el.querySelector('.message-text').textContent = msg.content;
            });
            chatSessionId = sessionId;
        });
}

// Simple markdown renderer (bold, code, links)
function renderMarkdown(text) {
    var html = escapeHtml(text);
    // Bold
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '...' : str;
}

// Connect on page load
document.addEventListener('DOMContentLoaded', connectWs);
