"""Compression checkpoint: context crossing an agent boundary above the trigger
gets digested by the local model. Input is chunked to fit the model's context
window — sent whole, the tail would be silently truncated before summarizing.
The log keeps the digest and before/after counts, not the flood it replaced."""

import litellm

from squad.config import CompressorConfig
from squad.interceptor import current_log

_PROMPT = (
    "Compress this working context for another AI agent. Caveman style: drop "
    "articles, filler, hedging and pleasantries — substance only, fragments "
    "fine, dense bullets. Keep EXACT: every fact, decision, number, date, file "
    "path, identifier, API signature, and open question. Code blocks and quoted "
    "errors stay verbatim. Never invent or drop information.\n\n"
)


def count_tokens(text: str, model: str) -> int:
    try:
        return litellm.token_counter(model=model, text=text)
    except Exception:
        return len(text) // 4  # crude fallback; only gates a threshold


def _digest(text: str, model: str) -> str:
    resp = litellm.completion(
        model=model, messages=[{"role": "user", "content": _PROMPT + text}]
    )
    return resp.choices[0].message.content


def compress(text: str, cfg: CompressorConfig) -> str:
    """Digest text if it exceeds the trigger; otherwise return it unchanged."""
    before = count_tokens(text, cfg.model)
    if before <= cfg.trigger_tokens:
        return text
    # half the window for input, half for the prompt + the digest; ~4 chars/token
    chunk_chars = max(cfg.window_tokens // 2, 1) * 4
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    digest = "\n".join(_digest(c, cfg.model) for c in chunks)
    after = count_tokens(digest, cfg.model)
    if log := current_log.get():
        log.write("compress", payload={"digest": digest, "chunks": len(chunks)},
                  tokens={"in": before, "out": after})
    return digest
