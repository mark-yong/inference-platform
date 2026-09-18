# inference-platform

**Operating model for a private multi-GPU LLM serving node: serving configs, gateway routing governance, benchmark harness, and the postmortems that shaped it.**

Not a tutorial repo: this is how I run a single-node self-hosted AI
platform 24/7: 3× RTX PRO 6000 (96 GB each) on an EPYC server under
Proxmox, ~15 containers, four open-weight model families in production
across vLLM / SGLang / llama.cpp, every call served locally (no
third-party LLM API in the serving path); the representative vLLM/SGLang
serving configs are included here. The configs here are sanitised,
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
  2026-08-31-*.md             aux job fell through a default route onto the
                              primary pair: gateway pins, not capacity
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

The 2026-08-31 postmortem is why the tiers exist: the gateway's default
route let an unpinned background job onto the TP2 pair, and a live session
stalled while the dedicated auxiliary GPU sat idle. After the pin, a full
background tick completes in ~35 s with no interactive contention on the
validation runs.

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
  complex ones. In effect the adaptive setting classifies reasoning budget
  per request.
- **`additional_drop_params: ["min_p", "logit_bias"]`** on vLLM MTP routes:
  speculative decoding rejects these, and common clients still send them.

## Serving config patterns (why the compose file looks like this)

- **GPU pinning via CDI** (`nvidia.com/gpu=N`), never `--gpus all` plus
  `CUDA_VISIBLE_DEVICES` together: CDI remaps injected GPUs to 0,1 inside
  the container; mixing mechanisms corrupts the mapping.
- **Bind the LAN IP, not 0.0.0.0**: `0.0.0.0` makes a port listen on every
  interface, so backend ports intended for the gateway only end up
  reachable from anywhere the host is. The host firewall narrows what is
  reachable; binding only the intended interface removes the exposure
  instead of filtering it.
- **Read-only model mounts; read-write caches**: weights are immutable,
  HF/vLLM/torch caches are per-stack host paths.
- **`ipc: host` + sized `shm_size`** for NCCL/tensor-parallel setups.
- **SGLang specifics** (from the production GLM-5.3/MTP TP2 stack, image
  v0.4.3): the explicit `--cuda-graph-bs-decode` list has to enumerate
  every batch size up to `--max-running-requests`; extend it when you
  raise the cap. Mainline SGLang also exposes `--cuda-graph-bs` plus
  graph padding, so check your version's capture/padding behaviour before
  assuming a sparse capture list is safe. Mamba-cache slots for hybrid
  models run ~5 per concurrent request (state + MTP intermediates);
  host-RAM HiCache tier sized explicitly.

## Benchmarks

`benching/bench_matrix.py` drives a concurrency × context-length matrix
through the open-source **llm-inference-bench** harness (credit: Martin Vit,
github.com/local-inference-lab/llm-inference-bench; clone it next to the
script or point `BENCH_REPO` at it). The measuring, engine auto-detection and
Prometheus cross-validation are upstream's; this layer adds matrix
generation, one-table collection, and env-based key handling (keys are read
from an env var *name*, never argv (argv leaks via `ps`)).

Benchmark conditions (from the saved result files on the production node):

- Harness: llm-inference-bench v0.4.29 @ `d115fee` (2026-09-01)
- Output: 2,048 max tokens per request, 5 requests per concurrency slot
- Sampling: engine defaults (temperature/top_p not pinned)
- Prompts: scout request populates the prefix cache, measured requests
  reuse the same prompt; figures measure sustained decode
- Results: decode table = aggregate decode tok/s across in-flight requests
  (the c1 column is single-stream; inter-token latencies quoted are
  single-stream p50/p99). Prefill table = prompt tok/s from client-measured
  time-to-first-token on the scout request, single sample per cell;
  131k cells cross-checked against the engines' Prometheus counters.
- Engines: SGLang (ormandj `sglang-glm53-flash-sm120` v0.4.3),
  vLLM (Blackwell build with b12x kernels; aux tier on
  `vllm/vllm-openai:nightly`)
- Speculative decoding: GLM rows ran with adaptive MTP (EAGLE, adaptive
  draft profile [3,5]) on SGLang; the aux 35B ran without MTP (draft MoE
  unsupported on its vLLM build); the DeepSeek DSpark r19 config's spec
  state at bench time is not recorded in the result file
- Interconnect: PCIe 4.0 x16 on every GPU link, NODE topology, no
  NVLink/P2P (the P2P registry overrides were verified but the fabric is
  plain PCIe; TP2 traffic crosses the root complex)
- GPUs: TP2 pair = 325 W Max-Q cards; aux = one 600 W card

Reference results from the production node (3× RTX PRO 6000, one Max-Q
325 W pair for TP2 + one full 600 W card):

| Model · engine | c1 | c2 | c4 | 131k ctx (c1) |
|---|---|---|---|---|
| GLM-5.3-Flash · TP2 SGLang | 140–153 | 212–230 | 333–391 | 132 |
| DeepSeek-V4-Flash · TP2 vLLM | 174–187 | 269–281 | 390 | 186 |
| Qwen3.6-35B-A3B NVFP4 · 1 GPU vLLM | 264 | 409 | 770 | 198 |

Prefill throughput (prompt tok/s, same runs, client-measured TTFT):

| Model · engine | 8k | 16k | 32k | 64k | 131k |
|---|---:|---:|---:|---:|---:|
| GLM-5.3-Flash · TP2 SGLang | 4,941 | 6,013 | 3,678 | 4,255 | 6,229 |
| DeepSeek-V4-Flash · TP2 vLLM | 5,734 | 5,582 | 6,363 | 6,793 | 6,583 |
| Qwen3.6-35B-A3B NVFP4 · 1 GPU vLLM | 22,553 | 20,767 | 17,514 | 13,859 | 9,432 |

Readings that drove decisions:

- The A3B auxiliary model has the highest c4 aggregate throughput in this
  matrix: 770 tok/s on one GPU. That is why background agents are routed
  there instead of consuming TP2 capacity.
- GLM c1 decode fell only ~6% from short context to 131k (140→132 tok/s)
  on this stack; capacity planning does not blow up at long context.
- Single-stream inter-token latency is tight at every context (p50 6.8 ms,
  p99 7.2 ms); streaming quality held while background jobs ran on the
  third GPU during the validation runs.
- Prefill differs sharply by tier: the 1-GPU 35B NVFP4 prefills at
  22.5k tok/s short-context (3-4x either TP2 pair) and still clears
  9.4k at 131k; both TP2 pairs hold a roughly flat ~3.7-6.8k across the
  whole range.
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
  the auxiliary tier had no hard pin, so a background compaction job fell
  through the gateway's default route onto the primary TP2 pair (thinking
  mode on) and stalled a live session ~10 min, while the auxiliary model's
  dedicated GPU sat with ample headroom. The bug was the fall-through path,
  not capacity; the fix removes it: six task types pinned by name, no
  default route to the primary pair. A full aux tick went from stalling
  sessions to ~35 s.

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

## Credits

The serving stack stands on open-source work from the local inference
community; the pieces this node actually runs:

- **fester** (Martin Vit,
  [voipmonitor](https://github.com/voipmonitor)): the docker containers
  ([blackwell-llm-docker](https://github.com/local-inference-lab/blackwell-llm-docker))
  and recipes ([rtx6kpro](https://github.com/local-inference-lab/rtx6kpro))
  that most local-inference-lab projects run on, plus the
  [llm-inference-bench](https://github.com/local-inference-lab/llm-inference-bench)
  harness behind `benching/bench_matrix.py`.
- **[lukealonso](https://github.com/lukealonso)** (Luke Alonso): the
  [b12x](https://github.com/local-inference-lab/b12x) kernel backend that
  makes RTX PRO 6000 / Blackwell cards runnable with vLLM (this node's
  stacks launch with `BACKEND=b12x`), and the quants in
  [quant-toolkit](https://github.com/local-inference-lab/quant-toolkit).
- **[ormandj](https://github.com/ormandj)** (David Orman): the
  [sglang-glm53-flash-sm120](https://github.com/ormandj/sglang-glm53-flash-sm120)
  docker image and W4A16+FP8-mix quant behind the primary TP2 stack.
- **The [local-inference-lab](https://github.com/local-inference-lab)
  community**: testing across all of the above.
- **[NVIDIA Model Optimizer](https://github.com/NVIDIA/Model-Optimizer)**:
  the NVFP4 quant of Qwen3.6-35B-A3B
  ([nvidia/Qwen3.6-35B-A3B-NVFP4](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4))
  that serves background traffic.

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
