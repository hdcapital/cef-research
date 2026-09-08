"""Which Anthropic credential in the repository actually serves a request?

Runs one FREE call (messages.count_tokens) per secret the workflow exposes,
with and without the workspace header, and prints the verdict for each:
the key's prefix (its KIND - sk-ant-api03 is a Console API key, sk-ant-admin
an Admin key, sk-ant-oat an OAuth token), never the key itself, and the
error class and message when the call is refused. Written for the day the
term extractor failed every document with "This API key is not scoped to a
workspace" and nobody could tell which of two secrets was in play.
"""
from __future__ import annotations

import json
import os

import anthropic

MODEL = os.environ.get("CHEAP_MODEL") or "claude-opus-5"
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()


def kind(key: str) -> str:
    parts = key.split("-")
    return "-".join(parts[:3]) if len(parts) >= 3 else "unknown"


def probe(name: str, key: str) -> dict:
    out = {"secret": name, "set": bool(key), "kind": kind(key) if key else None,
           "length": len(key)}
    if not key:
        return out
    for label, headers in (("plain", None), ("with_workspace_header",
                                              {"anthropic-workspace-id": WS} if WS else None)):
        if label != "plain" and headers is None:
            out[label] = "no ANTHROPIC_WORKSPACE_ID set"
            continue
        client = anthropic.Anthropic(api_key=key, default_headers=headers, max_retries=0)
        try:
            r = client.messages.count_tokens(
                model=MODEL, messages=[{"role": "user", "content": "ping"}])
            out[label] = f"OK ({r.input_tokens} tokens counted)"
        except anthropic.APIStatusError as exc:
            out[label] = f"{type(exc).__name__} {exc.status_code}: {str(exc)[:220]}"
        except Exception as exc:  # noqa: BLE001
            out[label] = f"{type(exc).__name__}: {str(exc)[:220]}"
    return out


def main() -> int:
    results = [probe(n, os.environ.get(n, "")) for n in
               ("CLAUDE_SECRET_KEY", "ANTHROPIC_API_KEY_SECRET")]
    results.append({"workspace_id_set": bool(WS), "model": MODEL})
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
