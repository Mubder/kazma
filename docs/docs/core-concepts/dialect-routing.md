---
sidebar_position: 5
---

# Dialect Routing

Kazma includes first-class support for Arabic dialect detection and multi-dialect routing.

## Supported dialects

| Dialect | Code | Description |
|---|---|---|
| Modern Standard Arabic | ar-MSA | Formal written Arabic |
| Kuwaiti Arabic | ar-KWT | Kuwaiti Gulf dialect |
| Egyptian Arabic | ar-EGY | Egyptian colloquial |
| Gulf Arabic | ar-GUL | General Gulf dialect |
| Levantine Arabic | ar-LEV | Levant region dialect |
| English | en | English |

## How it works

```python
from kazma_core.dialect_detector import DialectDetector

detector = DialectDetector()
result = detector.detect("شلونك؟ وين رايح؟")
# result.dialect = "ar-KWT"
# result.confidence = 0.92
# result.script = "arabic"
```

## Routing

The router selects the appropriate response style and tokenizer:

```python
from kazma_core.router import DialectRouter

router = DialectRouter()
handler = router.route(dialect_result)
response = await handler.generate(messages)
```

## RTL support

Kazma UI components handle right-to-left rendering natively.

## Kuwaiti tokenizer

For Kuwaiti dialect, Kazma includes a specialized tokenizer:

```python
from kazma_core.kuwaiti_tokenizer import KuwaitiTokenizer

tokenizer = KuwaitiTokenizer()
tokens = tokenizer.tokenize("شلونك وين رايح")
# ["شلونك", "وين", "رايح"]
```
