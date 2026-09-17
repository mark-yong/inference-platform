#!/usr/bin/env python3
"""Generate a concurrency x context-length benchmark matrix for vLLM/SGLang.

A thin config layer over the excellent open-source llm-inference-bench
harness (credit: Martin Vit, https://github.com/local-inference-lab/
llm-inference-bench). This script only generates and runs the matrix and
collects per-cell aggregate/single-stream tok/s into one table; the
measuring itself, Prometheus cross-validation, and engine auto-detection
are the upstream harness's.

Reference numbers this matrix produced on 3x RTX PRO 6000 (96 GB) are in
README.md "Benchmarks".

Usage:
  python3 bench_matrix.py --base-url http://serving-host:8000/v1 \
      --model primary-chat --api-key-env VLLM_API_KEY_PRIMARY \
      --concurrency 1 2 4 --ctx 1024 32768 131072

Environment:
  The API key is read from the env var named by --api-key-env (never a CLI
  arg; argv leaks via ps).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

DEFAULT_CONC = [1, 2, 4]
DEFAULT_CTX = [1024, 32768, 131072]
# upstream harness repo cloned next to this script (or system-installed)
BENCH_REPO = os.environ.get("BENCH_REPO", "./llm-inference-bench")


def run_cell(base_url, model, api_key, conc, ctx, outdir, timeout=1800):
    """Run one (concurrency, context) cell; return dict or None on failure."""
    label = f"c{conc}-ctx{ctx}"
    out = os.path.join(outdir, f"{label}.json")
    cmd = [
        sys.executable, os.path.join(BENCH_REPO, "benchmark.py"),
        "--host", base_url, "--api-key", api_key,
        "--model", model,
        "--concurrency", str(conc),
        "--context-length", str(ctx),
        "--json-out", out,
    ]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    dt = time.time() - t0
    if p.returncode != 0:
        print(f"  {label}: FAILED ({p.stderr.strip()[-120:]})", flush=True)
        return None
    try:
        with open(out) as f:
            r = json.load(f)
    except Exception:
        print(f"  {label}: no output json", flush=True)
        return None
    row = {
        "cell": label, "concurrency": conc, "context": ctx,
        "agg_tok_s": r.get("total_token_throughput")
                     or r.get("aggregate_output_throughput"),
        "single_tok_s": r.get("output_throughput")
                        or r.get("per_request_output_throughput_mean"),
        "wall_s": round(dt, 1),
    }
    print(f"  {label}: agg={row['agg_tok_s']} tok/s single={row['single_tok_s']} "
          f"tok/s ({dt:.0f}s)", flush=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-env", default="BENCH_API_KEY",
                    help="name of the env var holding the API key")
    ap.add_argument("--concurrency", type=int, nargs="+", default=DEFAULT_CONC)
    ap.add_argument("--ctx", type=int, nargs="+", default=DEFAULT_CTX)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--csv", default="results/matrix.csv")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        sys.exit(f"set {args.api_key_env} (never pass keys as CLI args)")
    os.makedirs(args.outdir, exist_ok=True)

    print(f"matrix: conc={args.concurrency} x ctx={args.ctx} "
          f"on {args.model} @ {args.base_url}")
    rows = []
    for conc in args.concurrency:
        for ctx in args.ctx:
            print(f"-- c{conc} ctx{ctx}", flush=True)
            row = run_cell(args.base_url, args.model, api_key, conc, ctx,
                           args.outdir)
            if row:
                rows.append(row)

    if rows:
        path = args.csv
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)}/{len(args.concurrency) * len(args.ctx)} cells ok "
              f"-> {path}")
        print("table:")
        print(f"{'model':<24}{'c1':>10}{'c2':>10}{'c4':>10}")
        # cheap pivot: aggregate tok/s by concurrency
        by_conc = {}
        for r in rows:
            by_conc.setdefault(r["concurrency"], []).append(r["agg_tok_s"])
        for conc in sorted(by_conc):
            vals = [v for v in by_conc[conc] if v]
            print(f"{args.model:<24}{min(vals) if vals else '-':>10}"
                  f"{sum(vals) / len(vals) if vals else '-':>10.0f}"
                  f"{max(vals) if vals else '-':>10}")
    sys.exit(0 if rows else 1)


if __name__ == "__main__":
    main()
