# 1314mc Provider — Actual Model Catalog (2026-05-10)

Base URL: `http://www.1314mc.net:3333/v1`
API Key Env: `API_1314MC_KEY`

## Available Models (confirmed via /v1/models)

### Claude Family
- `claude-opus-4-7` — Best for heavy analysis, code generation (slowest but most capable)
- `claude-opus-4-7-A` through `claude-opus-4-7-T`, `claude-opus-4-7-thinking` — variants
- `claude-opus-4-6` through `claude-opus-4-6-T2`, `claude-opus-4-6-thinking` — older Opus
- `claude-sonnet-4-6` through `claude-sonnet-4-6-A` — Good balance speed/capability
- `claude-haiku-4-5-20251001`, `claude-haiku-4-5-B` — Fastest, cheapest

### Other
- `MiniMax-M2.7` — Fast, cheap, good for format fixes and quick validation

## Models NOT available (despite what config.yaml said)
- `gpt-5.4-nano` — Returns HTTP 503 `"No available channel for model gpt-5.4-nano"`
- Any OpenAI/GPT models — This is NOT an OpenAI proxy

## Key Pitfalls
1. **HTTP (not HTTPS)** — Base URL is `http://www.1314mc.net:3333/v1`, not HTTPS. All calls must use plain HTTP.
2. **Model names changed** — Config.yaml referenced `gpt-5.4-nano` which doesn't exist on this provider. Must use actual model names listed above.
3. **Server occasionally returns 503** when overloaded. Retry with 10s delay usually works.
4. **MiniMax-M2.7** is the best "fast fix" model — cheap, quick, reliable for JSON format repair and simple tasks.

## Usage Pattern (Python)

```python
import requests

response = requests.post(
    "http://www.1314mc.net:3333/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['API_1314MC_KEY']}"},
    json={
        "model": "claude-sonnet-4-6",  # or MiniMax-M2.7 for fast tasks
        "messages": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
        ],
        "max_tokens": 400,
        "temperature": 0.3,
    },
    timeout=30,
)
```
