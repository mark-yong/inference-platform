#!/usr/bin/env python3
"""Run a concurrency x context-length matrix over llm-inference-bench.

Thin preset layer over the upstream harness (Martin Vit,
https://github.com/local-inference-lab/llm-inference-bench): upstream does
the measuring, engine auto-detection, and Prometheus cross-validation; this
script picks the production preset (contexts, concurrencies, output-token
cap) and invokes upstream's llm_decode_bench.py once per
(concurrency, context) cell, then collects the sustained-decode figures
into one table.

Interface matched to upstream at the version the README pins
(v0.4.29, commit d115fee): entry point llm_decode_bench.py, matrix flags
--concurrency/--contexts, output flag --output, summary in
summary_table[context][concurrency] (tok/s).

Reference numbers this matrix produced on 3x RTX PRO 6000 (96 GB) are in
README.md "Benchmarks".

Usage:
  python3 bench_matrix.py --base-url http://serving-host:8000 \
      --model primary-chat --api-key-env VLLM_API_KEY_PRIMARY \
      --concurrency 1 2 4 --contexts 1024 32768 131072

--base-url is the server ROOT (no /v1): upstream builds
{base_url}/v1/chat/completions itself, so a /v1 suffix would double up.

Environment:
  The API key is read from the env var named by --api-key-env, so the key
  never appears in THIS script's argv. The upstream harness itself takes
  --api-key as an argument, which is visible to same-host users via ps:
  run benchmarks from a single-operator host, or put auth at the gateway
  and bench through it.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

DEFAULT_CONC = [1, 2, 4]
DEFAULT_CONTEXTS = [1024, 32768, 131072]
DEFAULT_MAX_TOKENS = 2048  # per-request output cap used for the README tables
DEFAULT_DURATION = 30  # seconds of sustained decode per matrix cell (explicit)
# upstream harness repo cloned next to this script (or BENCH_REPO points at it)
BENCH = os.environ.get("BENCH_REPO", "./llm-inference-bench")
ENTRY = os.path.join(BENCH, "llm_decode_bench.py")


def run_cell(base_url, model, api_key, conc, ctx, max_tokens, outdir,
             timeout=3600):
    """Run one (concurrency, context) cell via upstream; return row or None."""
    label = f"c{conc}-ctx{ctx}"
    out = os.path.join(outdir, f"{label}.json")
    cmd = [
        sys.executable, ENTRY,
        "--host", base_url,
        "--api-key", api_key,
        "--model", model,
        "--concurrency", str(conc),
        "--contexts", str(ctx),
        "--max-tokens", str(max_tokens),
        "--duration", str(DEFAULT_DURATION),
        "--output", out,
        "--display-mode", "plain",
        "--no-hw-monitor",
    ]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  {label}: TIMEOUT", flush=True)
        return None
    dt = time.time() - t0
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip()[-140:]
        print(f"  {label}: FAILED ({tail})", flush=True)
        return None
    try:
        with open(out) as f:
            result = json.load(f)
    except Exception:
        print(f"  {label}: no output json at {out}", flush=True)
        return None
    agg = sustained_decode(result, conc, ctx)
    if agg is None:
        print(f"  {label}: ran, but no sustained-decode figure in summary_table",
              flush=True)
        return None
    print(f"  {label}: agg={agg:.0f} tok/s ({dt:.0f}s)", flush=True)
    return {"cell": label, "concurrency": conc, "context": ctx,
            "agg_tok_s": round(agg, 1), "wall_s": round(dt, 1)}


def sustained_decode(result, conc, ctx):
    """Sustained-decode tok/s for one cell from upstream's summary_table.

    summary_table maps context -> {concurrency: tok_s}. Keys are strings.
    No heuristic fallback: each invocation is deliberately one
    (concurrency, context) cell, so a missing key means the result file
    does not match what was requested and must not be silently reinterpreted.
    """
    table = result.get("summary_table")
    if not isinstance(table, dict):
        return None
    row = table.get(str(ctx))
    if not isinstance(row, dict):
        return None
    value = row.get(str(conc))
    return float(value) if isinstance(value, (int, float)) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True,
                    help="server ROOT url, e.g. http://serving-host:8000 "
                         "(no /v1; upstream appends /v1/... itself)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-env", default="BENCH_API_KEY",
                    help="name of the env var holding the API key")
    ap.add_argument("--concurrency", type=int, nargs="+", default=DEFAULT_CONC)
    ap.add_argument("--contexts", type=int, nargs="+", default=DEFAULT_CONTEXTS)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="per-request output cap (explicit, not upstream default)")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--csv", default="results/matrix.csv")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        sys.exit(f"set {args.api_key_env} (never pass keys as CLI args)")
    if not os.path.isfile(ENTRY):
        sys.exit(f"upstream harness not found at {ENTRY}; clone "
                 "https://github.com/local-inference-lab/llm-inference-bench "
                 "next to this script or point BENCH_REPO at it")
    os.makedirs(args.outdir, exist_ok=True)

    print(f"matrix: conc={args.concurrency} x ctx={args.contexts} "
          f"max_tokens={args.max_tokens} on {args.model} @ {args.base_url}")
    rows = []
    for conc in args.concurrency:
        for ctx in args.contexts:
            print(f"-- c{conc} ctx{ctx}", flush=True)
            row = run_cell(args.base_url, args.model, api_key, conc, ctx,
                           args.max_tokens, args.outdir)
            if row:
                rows.append(row)

    if rows:
        path = args.csv
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)}/{len(args.concurrency) * len(args.contexts)} "
              f"cells ok -> {path}")
        # pivot: rows = context, columns = concurrency (aggregate tok/s)
        by_ctx = {}
        for r in rows:
            by_ctx.setdefault(r["context"], {})[r["concurrency"]] = r["agg_tok_s"]
        concs = sorted({r["concurrency"] for r in rows})
        header = "ctx".ljust(8) + "".join(f"c{c:>10}" for c in concs)
        print(header)
        for ctx in sorted(by_ctx):
            line = str(ctx).ljust(8)
            for c in concs:
                v = by_ctx[ctx].get(c)
                line += f"{v:>11.0f}" if v is not None else f"{'-':>11}"
            print(line)
        print("(aggregate decode tok/s; c1 column is single-stream)")
    expected = len(args.concurrency) * len(args.contexts)
    sys.exit(0 if len(rows) == expected else 1)


if __name__ == "__main__":
    main()
