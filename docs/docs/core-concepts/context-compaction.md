---
sidebar_position: 4
---

# Context Compaction

Context compaction summarizes long conversations to fit within the model context window.

## The problem

LLMs have fixed context windows. Long conversations accumulate tokens that eventually exceed the limit, causing errors or truncation.

## The solution

Kazma automatically detects when the context is approaching the limit and compacts the conversation:

```python
from kazma_core.compaction import ContextCompactor

compactor = ContextCompactor(max_tokens=4096, threshold=0.8)
compacted = await compactor.compact(messages)
# Returns: [summary_message, recent_messages]
```

## Compaction strategy

1. **Detect** — Token count exceeds threshold (80% of max)
2. **Summarize** — Older messages are summarized into a single system message
3. **Preserve** — Recent messages (last 5-10) are kept verbatim
4. **Merge** — Summary + recent messages replace the full history

## Configuration

```yaml
compaction:
  enabled: true
  max_tokens: 4096
  threshold: 0.8
  preserve_recent: 5
  summary_model: openai/gpt-4o-mini
```
