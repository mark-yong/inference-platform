# inference-platform

Sanitised configs from my inference server. It runs 3× RTX PRO 6000 (96 GB each)
on an EPYC host under Proxmox, with LiteLLM in front of vLLM, SGLang and
llama.cpp, about 15 containers and four open-weight model families. Every
steady-state call is served locally, with no third-party LLM API in the serving
path. The cloud burst tier (NVIDIA Nemotron on Nebius Token Factory) exists for
failover and overflow: it is reachable only through router fallbacks, and normal
operation sends it zero traffic.

LiteLLM exposes role names (`main`, `fast`) instead of model slugs, so the model
behind a role can change without a client edit; a resolver rewrites those names
from live backend probes.

## What runs where

| Role | Model | Where |
|---|---|---|
| main, fast | DeepSeek-V4.1-Flash (TP3), GLM-5.3-Flash (TP2), Qwen3.8-Flash-Next (TP1) | local GPUs |
| burst | NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4, the checkpoint the local Flare lane also serves | Nebius Token Factory |
| memory | CPU embedding sidecar, Qdrant corpus, SPARQL graph | local |

The burst target is the checkpoint this node serves itself, so a local answer and
a burst answer differ by one model name in the request. The reasoning is in
`design-decisions/002-cloud-burst-tier.md`.

## NVIDIA open models

- **NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4** @ `1de6d84f`, served locally
  from a Flare package built with a recipe for this checkpoint, and served
  unchanged on Token Factory as the burst lane. Against a reference serving of
  the same checkpoint it agreed on all 40 comparison prompts; 30/30 requests
  completed clean, and repeats were deterministic.
- Nemotron-3-Nano-30B-A3B, the earlier burst default, still routable.
- Nemotron-3-Ultra-550B-A55B, reachable on the same key, used for long-context
  consolidation.
- **nvidia/Qwen3.8-Flash-Next-FP4-Local** @ `b9ce18a6`, served on Flare as
  `m-yong/Qwen3.8-Flash-Next-FLARE` (c32, MTP5). Three of the fixes listed under
  Upstream work exist because of lanes like this one: `#31` keeps ChatML
  tool-argument numbers as literals, `#67` leaves a quoted `<tool_call>` in prose
  instead of starting a call (validated against Qwen3.8 Flash and 27B), and `#94`
  matches reasoning delimiters as tokens so a mention is not a channel switch.
- NVIDIA Model Optimizer made the NVFP4 quant of Qwen3.6-35B-A3B that serves
  background traffic (credited below).

## Token Factory

One `model_list` block in the gateway config, one env var for the key. Every
client in the stack reaches the burst lane through the same OpenAI-compatible
endpoint, with no second SDK and no separate prompt set.

Local serving has to cover the peak and the provider does not, so failover during
a GPU pair reload and overflow on a busy night cost nothing while idle. Because
the burst target is the same checkpoint as the local lane, a local answer and a
cloud answer can be compared directly instead of across two different models.
`gateway/token_factory_check.py` reads the model list from the endpoint at run
time, with no slug of its own, and reports latency and token usage for its smoke
call; those numbers are in `benching/results/`.

## Upstream work

Seven pull requests merged into NVIDIA's serving repository, two design documents
landed in-tree, and eleven defect reports filed with nine resolved:

- `#31` ChatML tool arguments keep JSON number literals instead of emitting every
  number as a float.
- `#49` Undeclared MiniMax tool calls are returned under automatic tool choice.
- `#56` Gemma 4 chat templates load in Helios.
- `#62` Gemma 4 reasoning channels and tool calls parse correctly.
- `#67` A tool tag quoted in prose stays text instead of aborting the response.
- `#83` The checkpoint licence travels with a package through promotion.
- `#94` Reasoning delimiters count only when generated as that token.

## Next

New models for this stack are being packaged for Flare rather than brought up on
another engine, and the next Flare release is expected soon. GLM-5.3-Flash is the
one I am waiting on. It does not serve on Flare yet, because the export runs out of
memory during kernel tuning on 27 Oct, the same failure reported upstream in `#73`,
so that lane stays on SGLang at TP2 until a release covers it.

## Quickstart

```bash
git clone https://github.com/mark-yong/inference-platform && cd inference-platform
cp gateway/config.example.yaml gateway/config.yaml   # roles, tiers, burst lane
export NEBIUS_API_KEY=...                            # burst lane only
python gateway/token_factory_check.py                # lists models, one smoke call
# serving stacks: serving/docker-compose.example.yml and the env templates beside it
```

Addresses in the examples are variables; secrets come from env files.

---

## Operating notes

The examples include the routing config, representative serving stacks, the
CPU embedding sidecar that keeps vector queries off the GPUs, the benchmark
wrapper I use for capacity tests, the production benchmark receipts, and two
incident notes that led to changes in the current setup. Hosts are
parameterised, secrets live in env files, and model names are genericised in
the example configs.

## Contents

```
gateway/
  config.example.yaml         LiteLLM routing: tiers, per-task pins, weighted
                              background group, embeddings lanes, burst tier
                              (DB-less mode)
  token_factory_check.py      burst-tier smoke check (stdlib only; reads model
                              slugs from the endpoint at run time)
serving/
  docker-compose.example.yml  vLLM stacks: TP3 primary + single-GPU aux
  docker-compose.sglang-glm53.example.yml
                              SGLang TP2 stack (GLM-5.3/MTP class)
  compose.env.example         env templates, one per service
  compose.env.aux.example     env for the aux-35b service
  sglang.env.example          env for the SGLang stack
  adaptive.example.json       adaptive-MTP draft profile
docker/
  rag-emb-cpu/                CPU embedding sidecar: compose + server source
                              (Qwen3-Embedding-0.6B, last-token pooling,
                              left padding; cosine 0.99994 vs the GPU lane)
benching/
  bench_matrix.py             concurrency × context matrix runner over
                              llm-inference-bench
  results/                    production benchmark receipts (sanitised server
                              fields) + generated summary tables
design-decisions/
  001-two-lane-embeddings.md  GPU ingest / CPU query split and the vector
                              contract between the lanes
  002-cloud-burst-tier.md     why the cloud lane is fallbacks-and-pins only
  003-agent-memory-layers.md  episodic / semantic / procedural memory design
postmortems/
  2026-08-29-*.md             rollback removed boot logs before diagnosis
  2026-08-31-*.md             unpinned background route stalled a session
```

## Routing

Three tiers in the gateway config:

1. Interactive: the primary chat model, DeepSeek-V4.1-Flash, across all three
   GPUs (TP3).
2. Auxiliary: Qwen3.6-35B-A3B on its own GPU. Every background task type
   (summarisation, titling, extraction, triage) has a named pin to this
   tier; no route falls through to the primary pair.
3. Background: a weighted group, weight 10 on the auxiliary GPU plus a
   capped member of the primary set (weight 1, `rpm: 4`,
   `max_parallel_requests: 1`). Weight sets selection probability; the caps
   are what bound the impact on interactive traffic. Remove the primary
   member for strict isolation.
4. Burst: NVIDIA Nemotron on Nebius Token Factory, as `burst-nemotron-lightning`
   (the checkpoint the local Flare lane also serves) and `burst-nemotron` (Nano)
   for the fast lane. Reachable only through router fallbacks (`primary-chat`,
   `primary-chat-nothink`, `background-llm`). Fail fast (`allowed_fails: 1`)
   plus a 45 s cooldown keeps consumers working through a GPU stack swap; the
   fallback chain is what makes this a burst tier and keeps it from becoming a
   shadow primary. See design-decisions/002-cloud-burst-tier.md.

On 2026-08-31 an unpinned background compaction job followed the default
route onto the interactive set (a TP2 pair at the time) and stalled a live
session for about 10 minutes while the auxiliary GPU sat idle. After the task-type pins were added, a
full background tick completed in ~35 s with no observed contention. See
Incidents.

Other configuration details:

- DB-less mode: no `database_url`; routing, aliases and keys live in this
  YAML, so gateway state is in git. The admin UI is unavailable in this
  mode; everything is file-managed.
- `model_group_alias` belongs under `router_settings`. Top-level, it is
  silently unread on current versions: the proxy comes up healthy, then the
  alias 404s at call time.
- `model_info.max_input_tokens` is published per model; clients read it
  from `/v1/models`. Version-gated; check the gateway version before
  debugging missing context length.
- `reasoning_effort` is not pinned. Pinning `high` measured 966 response
  tokens on trivial prompts; adaptive spent 51 on the same prompts and
  ~1,900 with a natural stop on complex ones.
- `additional_drop_params: ["min_p", "logit_bias"]` on vLLM MTP routes;
  speculative decoding rejects these and common clients still send them.

## Serving configuration

- GPUs are pinned with CDI device names (`nvidia.com/gpu=N`). Never combine
  `--gpus all` with `CUDA_VISIBLE_DEVICES`: CDI remaps injected GPUs to
  0,1 inside the container, and mixing the two corrupts the mapping.
- Backend ports bind the serving host's LAN IP, not 0.0.0.0. `0.0.0.0`
  listens on every interface; the host firewall narrows what is reachable,
  binding the intended interface removes the exposure.
- Model weights are read-only mounts; HF/vLLM/torch caches are per-stack
  host paths.
- `ipc: host` and a sized `shm_size` for NCCL/tensor-parallel setups.
- SGLang (GLM-5.3/MTP TP2 stack, image v0.4.3): `--cuda-graph-bs-decode`
  enumerates every batch size up to `--max-running-requests`; extend the
  list when raising the cap. Mainline SGLang also exposes `--cuda-graph-bs`
  plus graph padding, so check your version's capture behaviour before
  assuming a sparse list is safe. Mamba-cache slots run about 5 per
  concurrent request (state + MTP intermediates). The host-RAM HiCache tier
  is sized explicitly (32 GB).

## Embeddings

Vector embedding runs as two lanes with one contract
(design-decisions/001-two-lane-embeddings.md):

- Ingest lane: vLLM serving Qwen3-Embedding-0.6B (1024-d) on the GPU,
  started on demand for batch ingestion and stopped afterwards (~1.1 GiB
  VRAM freed between runs). Never takes query traffic.
- Query lane: an always-on torch CPU sidecar (`docker/rag-emb-cpu/`), capped
  at 4 threads and 8 GB, OpenAI-compatible on :8017. Interactive retrieval
  never queues behind an ingest batch, and the GPU is free between runs.

The contract: raw text input, LEFT padding, last-token pooling at position
-1, L2-normalised FP32 output. Cosine 0.99994 against the GPU lane for
identical input, with a standing >= 0.99 swap gate before any embedder
cutover. The first CPU implementation used mean pooling with right padding:
the vectors were the right shape and quietly wrong. The contract exists
because of that failure.

## Benchmark setup

`benching/bench_matrix.py` drives a concurrency × context-length matrix
through the llm-inference-bench harness (Martin Vit,
github.com/local-inference-lab/llm-inference-bench; clone it next to the
script or point `BENCH_REPO` at it). The measuring, engine auto-detection
and Prometheus cross-validation are upstream's; this layer adds the
production preset (explicit contexts, concurrencies, output-token cap) and
one-table collection. The API key is read from an env var *name* in this
script; upstream's own `--api-key` flag is visible to same-host users via
`ps`, so bench from a single-operator host or bench through the gateway.

Benchmark conditions (from the saved result files on the production node):

- Harness: llm-inference-bench v0.4.32 @ `d115fee` (2026-09-01)
- Output: 2,048 max tokens per request; sustained decode, 30 s per matrix
  cell
- Sampling: engine defaults (temperature/top_p not pinned)
- Prompts: scout request populates the prefix cache, measured requests
  reuse the same prompt; figures measure sustained decode
- Results: decode table = aggregate decode tok/s across in-flight requests
  (the c1 column is single-stream; inter-token latencies quoted are
  single-stream p50/p99). Prefill table = prompt tok/s from client-measured
  time-to-first-token on the scout request, single sample per cell; 131k
  cells cross-checked against the engines' Prometheus counters
- Single-stream inter-token latency: p50 6.8 ms, p99 7.2 ms, measured
  across context lengths
- Engines: SGLang (ormandj `sglang-glm53-flash-sm120` v0.4.3), vLLM
  (Blackwell build with b12x kernels; aux tier on
  `vllm/vllm-openai:nightly`). Run dates: DeepSeek 2026-08-28, aux 35B
  2026-09-01, GLM 2026-09-02/03 (the GLM result file's own metadata records
  an older harness build than the pinned commit; prefill figures are from
  the 09-03 rerun)
- Speculative decoding: GLM rows ran with adaptive MTP (EAGLE, adaptive
  draft profile [3,5]) on SGLang; the aux 35B ran without MTP (draft MoE
  unsupported on its vLLM build); the DeepSeek DSpark r19 config's spec
  state at bench time is not recorded in the result file
- Interconnect: PCIe 4.0 x16 on every GPU link, NODE topology, no
  NVLink/P2P, so tensor-parallel traffic crosses the root complex
- GPUs: 3× RTX PRO 6000, 97,887 MiB each; power limits read from `nvidia-smi`
  are 300 W on the two Max-Q cards (index 0 and 1) and 350 W on index 2

Result receipt files are tracked in `benching/results/` (server fields
rewritten to the parameterised host name; no other field modified), with
generated summary tables in `benching/results/summary-tables.md`. The
receipts carry both GLM runs: the decode table below reads the 2026-09-02
file (v0.4.29 harness), and a 2026-09-03 rerun on the v0.4.34 harness
measured higher single-stream decode (167.9 to 180.3 across contexts, 131k
at 167.9); both receipts are kept, and the rerun is the current expectation
for this stack.

Decode results (aggregate tok/s):

| Model · engine | c1 | c2 | c4 | 131k ctx (c1) |
|---|---|---|---|---|
| GLM-5.3-Flash · TP2 SGLang | 140–153 | 212–230 | 333–391 | 132 |
| DeepSeek-V4-Flash · TP2 vLLM | 174–187 | 269–281 | 390 | 186 |
| Qwen3.6-35B-A3B NVFP4 · 1 GPU vLLM | 264 | 409 | 770 | 198 |

Prefill throughput (prompt tok/s, same runs, client-measured TTFT):

| Model · engine | 8k | 16k | 32k | 64k | 131k |
|---|---:|---:|---:|---:|---:|
| GLM-5.3-Flash · TP2 SGLang | 5,232 | 5,782 | 5,705 | 5,542 | 5,912 |
| DeepSeek-V4-Flash · TP2 vLLM | 5,734 | 5,582 | 6,363 | 6,793 | 6,583 |
| Qwen3.6-35B-A3B NVFP4 · 1 GPU vLLM | 22,553 | 20,767 | 17,514 | 13,859 | 9,432 |

These tables are the 2026-09 receipts and are kept as measured. The DeepSeek
row is DeepSeek-V4-Flash on a TP2 pair, which was the primary configuration at
the time; the primary model is now DeepSeek-V4.1-Flash on three GPUs, and no
benchmark receipt for that stack is published yet.

The GLM prefill row is from the 2026-09-03 rerun, server-validated on each
cell. The 09-02 scan showed a 32k/64k dip (3.7k/4.3k) that did not repeat in
either rerun; treated as sample noise from concurrent load.

Qwen3.6-35B-A3B had the highest c4 throughput in this test, so background
traffic runs there and the primary set is reserved for interactive traffic.
GLM-5.3-Flash c1 decode changed from 140–153 tok/s at short context to
132 tok/s at 131k, which set the long-context expectations for the
interactive tier. Prefill was cross-checked against the engines' Prometheus
counters: 5.9k tok/s client-measured vs 6.2k server-side on an 83.5k-token
prompt, under 5% gap.

## TP3

Three-GPU serving is what the primary model runs on. DeepSeek-V4.1-Flash
(`deepseek-ai/DeepSeek-V4.1-Flash-1017`) takes GPUs 0, 1 and 2 from the image
`azallaza/ds41-kraken-tp3:20261019.3` on port 8019, and the gateway's `main`
route points at it. Harder requests go there; the cheaper lanes stay on one
GPU. The stack was restarted on 28 Oct and has run continuously since.

GLM-5.3-Flash is the case where TP3 was tried and did not win, so that model
still serves on TP2. The local-inference-lab Discord ran the three-GPU work for
it; as of 2026-09-18 it functions but stays behind TP2:

- Community-maintained ported images
  ([GLM5.3-Flash-TP3-Testing](https://github.com/BoxTops4Coochie/GLM5.3-Flash-TP3-Testing),
  r30/r34, latest 2026-09-16) boot with MTP3 or DFlash; decode ran
  165–180 tok/s c1 flat to 128k on LLM-bench. DCP1 reaches ~2M context,
  DCP3 ~5–7.5M at slower decode (~150 tok/s c1, ~440 tok/s c8).
- Checkpoints in use: local-inference-lab/GLM-5.3-Flash-NVFP4 (uniform
  NVFP4, simplest) and brandonmusic/GLM-5.3-Flash-TrellisMX-MXFP8. Mixed-quant
  checkpoints (MXFP8 + W4A16 in one file) add enough TP3-specific complexity
  that testers rejected them; EXL3 fails on TP3 outright (padded
  `moe_intermediate_size` loader mismatch).
- Long-context consistency on the GLM TP3 attempts has not reached TP2 parity.

## Incidents

Two incidents led to changes in the current setup.

- [Rollback erased diagnostics during a 1M-context rollout](postmortems/2026-08-29-max-model-len-boot-failure.md):
  the 1M configuration failed to boot, the automatic rollback restarted the
  container and destroyed the boot logs, so the startup failure was never
  root-caused. Logs are now captured before any revert; deliberate restarts
  require approval; risky changes cut over on a spare port.
- [Unpinned background route stalled an interactive session](postmortems/2026-08-31-aux-compaction-stall.md):
  a compaction job followed the gateway's default route onto the interactive set and stalled a live session
  and stalled a live session for ~10 min; the auxiliary GPU had spare
  capacity throughout. Six task types are now pinned by name, and the
  standing rule distinguishes unpinned routes from bounded, explicit
  overflow.

The routing-tier and burst-tier designs behind these fixes are written up
in `design-decisions/`; `003-agent-memory-layers.md` documents the memory
architecture that sits above the serving layers.

## Operating rules

1. Probe risky changes on a spare port and cut over in one step; the
   healthy production path is never mutated in place.
2. Restart the gateway from a detached script that health-polls and rolls
   back, never from a session that dies with it.
3. Deliberate restarts of containers serving live traffic are explicit,
   human-approved actions. `restart: unless-stopped` covers crash recovery
   only.
4. Capture complete logs before any revert; the rollback restarts the
   container.
5. Version-pin engine images and gateway releases. The examples pin tested
   tags; production files add digests.

## Operation

The node is operated by agents. Hermes runs on the node itself and its
sessions ride the gateway; some stacks are stood up and maintained from
Cursor sessions on the workstation, driven by external models. The gates
below apply to either surface.

- Cron watchdogs poll gateway health and check the running image against
  the compose pin every 10 minutes. They report only; they hold no restart
  or patch capability.
- Routing pins, aliases, rate caps and context limits are config edits in
  the DB-less gateway config, reverted with git. Several take effect
  without a restart.
- Restarts, recreates and cutovers of production serving containers are
  explicit, human-approved actions.
- Benchmark result files are kept as receipts; routing and model decisions
  cite them.
- Production profiles (the DeepSeek DSpark r19 defaults, the SGLang image
  versions) are tracked from upstream recipe repos and verified on this
  node before adoption.

### Deploying a model

Deployments run the same gate pattern from either surface. The loop as run
for the current GLM quant stack:

1. Upstream recipe repo, producer image tag and base checkpoint revision
   are pinned; the expected artifact is written down first (shard count,
   tensor count, byte total).
2. The HF manifest is checked against that contract before download; a
   321-vs-642 GB storage-figure discrepancy was resolved with a metadata
   query, zero bytes moved.
3. Disk and GPU headroom checks run before staging; after download the
   index is checked against the contract and produced output is verified
   shard-by-shard SHA256 against the published manifest. Byte-exact
   establishes identity with the published checkpoint; no separate
   equivalence validation is needed.
4. Quantization producers run with a preflight-only flag first; the
   KV-scale investigation read the pinned image's code before touching a
   live path.
5. New stacks come up on a spare port and take traffic only after an
   explicit go; superseded containers are held, not deleted, and image
   cleanup is a separate pass.

## Credits

A warm thanks to the many members of the Local Inference Lab Discord, you're all truly wonderful

- **[voipmonitor](https://github.com/voipmonitor)** (Martin Vit): the
  docker containers
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
  community**: contributions and testing across all of the above.
- **[NVIDIA Model Optimizer](https://github.com/NVIDIA/Model-Optimizer)**:
  the NVFP4 quant of Qwen3.6-35B-A3B
  ([nvidia/Qwen3.6-35B-A3B-NVFP4](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4))
  that serves background traffic.

## Limitations

- Single node, 3 GPUs. P2P/NVLink bandwidth measurement failed on a missing
  CUDA runtime library; the NVIDIA P2P registry overrides were verified
  instead.
- The 1M-context startup failure was not root-caused because rollback
  removed the relevant logs.
- Results are from this node only.

## License

MIT. See [LICENSE](LICENSE).
