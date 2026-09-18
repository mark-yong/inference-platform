# inference-platform

**Operating model for a private multi-GPU LLM serving node: serving configs, gateway routing governance, benchmark harness, and the postmortems that shaped it.**

Not a tutorial repo: this is how I run a single-node self-hosted AI
platform 24/7: 3× RTX PRO 6000 (96 GB each) on an EPYC server under
Proxmox, ~15 containers, four open-weight model families in production
across vLLM / SGLang / llama.cpp, every call served locally (no
third-party LLM API in the serving path). The configs here are sanitised,
annotated forms of the live production files: hosts parameterised, secrets
moved to env, model names genericised; the structure and the incident
notes in the comments are intact.

## What's in the box

```
gateway/config.example.yaml   LiteLLM routing governance: tiers, pins, weighted
                              background group, per-task aliases (DB-less mode)
serving/
  docker-compose.example.yml  vLLM stacks: TP2 primary + single-GPU aux,
                              CDI GPU pinning, LAN-IP binds, cache layout
  compose.env.example         env template (keys never in-file)
benching/
  bench_matrix.py             concurrency × context-length matrix runner
                              (thin layer over llm-inference-bench)
postmortems/
  2026-08-29-*.md             rollback destroyed the evidence: observability
                              must be part of the rollback path
  2026-08-31-*.md             aux job stalled interactive traffic: routing
                              policy, not hardware, is the fix
```

## The routing governance story (why the gateway config looks like this)

Three tiers, enforced in the gateway config itself:

1. **Interactive**: the primary chat model on a dedicated TP2 GPU pair.
2. **Auxiliary**: a small MoE on its own GPU; every background task type
   (summarisation, titling, extraction, triage...) gets a **named pin** to
   this tier. There is no fall-through route to the primary pair.
3. **Background**: a weighted multi-deployment group (`weight: 10` on the
   independent GPU, capped fallbacks on the big pairs) so background traffic
   survives TP2-pair maintenance while staying rate-limited (rpm caps +
   `max_parallel_requests: 1`).

The 2026-08-31 postmortem shows the failure mode this tier structure
exists for: everything works until two workloads land on one GPU. After the
pin, a full background tick completes in ~35 s with zero interactive
contention.

Other patterns worth lifting from the annotated config:

- **DB-less mode**: no `database_url`; routing, aliases and keys live in the
  YAML, making gateway state git-manageable. (Trade-off: the admin UI is
  unavailable; everything is file-managed.)
- **`model_group_alias` belongs under `router_settings`**: as a top-level
  key it is silently unread on current versions: the proxy comes up healthy,
  then the alias 404s at call time.
- **Context-window discovery**: publish `model_info.max_input_tokens` per
  model; clients read it from `/v1/models` (version-gated behaviour; check
  the gateway version before debugging "missing context length").
- **Adaptive reasoning by default, pins only for special routes**: pinning
  `reasoning_effort: high` measured 966 response tokens even on trivial
  prompts; adaptive spends 51 on trivial and ~1,900 with a natural stop on
  complex ones. Adaptive thinking is a free cost-classifier.
- **`additional_drop_params: ["min_p", "logit_bias"]`** on vLLM MTP routes:
  speculative decoding rejects these, and common clients still send them.

## Serving config patterns (why the compose file looks like this)

- **GPU pinning via CDI** (`nvidia.com/gpu=N`), never `--gpus all` plus
  `CUDA_VISIBLE_DEVICES` together: CDI remaps injected GPUs to 0,1 inside
  the container; mixing mechanisms corrupts the mapping.
- **Bind the LAN IP, not 0.0.0.0**: the host firewall exposes specific
  ports only; a wildcard bind advertises stacks that should be gateway-only.
- **Read-only model mounts; read-write caches**: weights are immutable,
  HF/vLLM/torch caches are per-stack host paths.
- **`ipc: host` + sized `shm_size`** for NCCL/tensor-parallel setups.
- **SGLang specifics** (from the production TP2 stack): cuda-graph decode
  batch list must enumerate *every* batch size up to
  `--max-running-requests` (extend the list when you raise it); mamba-cache
  slots for hybrid models are ~5 per concurrent request (state + MTP
  intermediates); host-RAM HiCache tier sized explicitly.

## Benchmarks

`benching/bench_matrix.py` drives a concurrency × context-length matrix
through the open-source **llm-inference-bench** harness (credit: Martin Vit,
github.com/local-inference-lab/llm-inference-bench; clone it next to the
script or point `BENCH_REPO` at it). The measuring, engine auto-detection and
Prometheus cross-validation are upstream's; this layer adds matrix
generation, one-table collection, and env-based key handling (keys are read
from an env var *name*, never argv (argv leaks via `ps`)).

Reference results from the production node (3× RTX PRO 6000, one Max-Q
325 W pair for TP2 + one full 600 W card):

| Model · engine | c1 | c2 | c4 | 131k ctx (c1) |
|---|---|---|---|---|
| 35B-A3B NVFP4 · 1 GPU (vLLM) | 264 | 409 | 770 | 198 |
| Primary chat · TP2 (SGLang) | 140–153 | 212–230 | 333–391 | 132 |
| Primary chat · TP2 (vLLM) | 174–187 | 269–281 | 390 | 186 |
| Primary chat EXL3-4bpw · TP2 (vLLM) | 114–118 | n/m | n/m | 114 (flat 0→131k) |

Readings that drove decisions:

- The small A3B MoE on **one** GPU is the throughput king (770 tok/s
  aggregate at c4). That is why 100% of background agent traffic rides it
  while interactive traffic keeps the TP2 pair.
- Long-context decode stays essentially flat on TP2 (140→132 tok/s across
  0→131k); capacity planning does not blow up at long context.
- 4-bit EXL3 keeps decode flat across the entire 131k range (114→114); the
  quantisation trade was measured, not assumed.
- Single-stream inter-token latency is tight at every context (p50 6.8 ms,
  p99 7.2 ms); streaming quality holds even while background jobs run on
  the third GPU.
- Prefill cross-checked server-side: 6.2k tok/s client-measured vs 6.5k on
  the engine's own Prometheus counters (83.5k-token prompt, <5% gap).

## The postmortems

Both happened on this node; I kept them here because the failure modes
generalise to any production ML deployment:

- **[Rollback destroyed the evidence](postmortems/2026-08-29-max-model-len-boot-failure.md)**:
  a 1M-context change failed to boot, the automatic rollback reverted and
  restarted, and the logs explaining *why* were destroyed with the container.
  Root cause was never identified. Fix: capture complete logs BEFORE any
  revert; containers serving live traffic are never recreated by automation;
  risky changes cut over on a spare port, never in place.
- **[Aux task stalled interactive traffic](postmortems/2026-08-31-aux-compaction-stall.md)**:
  a large background job with no routing pin landed on the primary TP2
  pair and stalled a live session ~10 min. The fix was routing policy at the
  gateway, not hardware: interactive and background workloads must never
  share a serving pair.

## Operating rules (all from incidents on this node)

1. **Probe on a spare port first; cut over in one step.** Never mutate the
   healthy production path in place.
2. **The gateway you're running through is a dependency of your own tooling**:
   restart it via a detached script that health-polls and auto-rolls-back,
   never from inside a session that dies with it.
3. **Containers serving live traffic are never recreated by automation.**
   Restarts are explicit, human-approved actions.
4. **Capture logs before any revert.** The rollback path is part of the
   system and must preserve evidence.
5. **Version-pin everything.** Engine images and gateway releases alike;
   floating tags and `*-stable` channels have both silently shipped old
   versions here.

## Honest limitations

- Single node, 3 GPUs; not a multi-node fabric study. P2P/NVLink bandwidth
  measurement was attempted and failed on a missing CUDA runtime lib; the
  NVIDIA P2P registry overrides were verified instead.
- The 1M-context boot failure was never root-caused (evidence destroyed by
  the rollback, which is the point of the postmortem).
- Numbers are from one operator's node; treat them as a worked example of
  the *method* (matrix, cross-validation, tier economics), not as
  generalisable device figures.

## License

MIT. See [LICENSE](LICENSE).
