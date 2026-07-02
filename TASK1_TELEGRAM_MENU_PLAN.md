# Task 1 — Telegram Bot Menu Button: Implementation Plan

## Status: ✅ ALREADY IMPLEMENTED (commit `db85c36`)

The `setMyCommands` feature was implemented and merged to `origin/main` as commit `db85c36a61575d60345ea89be78b9de6cbb42fa7`:

```
feat(telegram): register bot commands via setMyCommands API for menu button

- telegram.py: add _register_bot_commands() method + call in listen() after getMe
- Registers 13 slash commands across 3 scopes (default, private, group)
- test_telegram_reactions.py: update startup test for additional setMyCommands calls
```

## What Was Implemented

### File: `kazma-gateway/kazma_gateway/adapters/telegram.py`

A `_register_bot_commands()` method was added to `TelegramAdapter` that:

1. **Builds** a list of `{"command": "help", "description": "Show available commands"}` dicts for all 13 commands from `slash_commands.py`:
   - `help`, `reset`, `status`, `model`, `memory`, `cost`, `replay`, `config`, `personality`, `context`, `undo`, `edit`, `swarm`

2. **POSTs** to `POST https://api.telegram.org/bot{token}/setMyCommands` for all 3 scopes:
   - `BotCommandScopeDefault` (`{"type": "default"}`)
   - `BotCommandScopeAllPrivateChats` (`{"type": "all_private_chats"}`)
   - `BotCommandScopeAllGroupChats` (`{"type": "all_group_chats"}`)

3. **Logs** success at INFO level: `[Telegram] set_my_commands OK for scope ...`
   Failures at WARNING level (non-fatal — the bot still starts).

4. **Called** in `listen()` right after `getMe` validation, before the polling loop begins.

### Pattern Used

Follows the existing `httpx` pattern from `_poll()` and `send()`:
- Uses `self._http` client (lazy-initialized httpx.AsyncClient)
- Same timeout/retry conventions
- Non-blocking on failure (the bot continues to work without the menu)

## Test Strategy

- `test_telegram_reactions.py` was updated to mock the additional `setMyCommands` HTTP calls during startup
- Manual verification: restart server with a Telegram bot, check that the menu button appears in the Telegram app

## Verification Steps

```bash
# 1. Restart the server
Get-Process -Name python | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }
& '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090

# 2. Check server logs for setMyCommands confirmation
# Should see: [Telegram] set_my_commands OK for scope default/private/group

# 3. Open Telegram → find your bot → the menu button (slash icon) should appear
#    next to the text input field with all 13 commands
```

## Note

This feature is **complete and shipped**. No further work needed. The implementation matches the Hermes pattern referenced in the task description (3 scopes, setMyCommands on startup, INFO/WARNING logging).
