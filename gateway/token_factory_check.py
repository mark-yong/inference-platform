#!/usr/bin/env python3
"""Smoke check for the cloud burst tier: Nebius Token Factory.

Lists models from the live endpoint, picks the first NVIDIA Nemotron model,
sends one short chat completion, prints latency and token usage.

Stdlib only. Model slugs are read from the endpoint at run time (never
hard-coded): the burst tier is a role-named routing target whose backing
model is expected to change as NVIDIA ships new Nemotron sizes.

Env:
  NEBIUS_API_KEY   (required) Token Factory API key
  TF_BASE_URL      (optional) defaults to https://api.tokenfactory.nebius.com/v1
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("TF_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
KEY = os.environ.get("NEBIUS_API_KEY", "")


def call(method: str, path: str, payload: dict | None = None) -> dict:
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    if not KEY:
        print("NEBIUS_API_KEY not set", file=sys.stderr)
        return 2

    models = call("GET", "/models")["data"]
    nemotron = sorted(m["id"] for m in models if "nemotron" in m["id"].lower())
    print(f"endpoint: {BASE}")
    print(f"models: {len(models)} total, {len(nemotron)} nemotron")
    if not nemotron:
        print("no Nemotron model exposed by this endpoint; nothing to probe")
        return 1
    slug = nemotron[0]
    print(f"probing: {slug}")

    t0 = time.time()
    out = call(
        "POST",
        "/chat/completions",
        {
            "model": slug,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "max_tokens": 512,
            "temperature": 0.0,
        },
    )
    dt = time.time() - t0
    msg = out["choices"][0]["message"]
    text = msg.get("content") or ""
    # Reasoning models may return their chain in a reasoning_content field;
    # the visible answer can be empty while reasoning burned the budget.
    if not text.strip():
        text = f"<empty content; reasoning_content present: {bool(msg.get('reasoning_content'))}>"
    usage = out.get("usage", {})
    print(f"latency: {dt:.2f}s")
    print(f"usage: {json.dumps(usage)}")
    print(f"reply: {text.strip()[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
