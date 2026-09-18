# inference-platform

This repo contains the sanitised configs from my inference server. It runs
3× RTX PRO 6000 (96 GB each) on an EPYC host under Proxmox, with LiteLLM in
front of vLLM, SGLang and llama.cpp, ~15 containers, four open-weight model
families, and every call served locally, with no third-party LLM API in the
serving path.

The examples include the routing config, representative serving stacks, the
benchmark wrapper I use for capacity tests, and two incident notes that led
to changes in the current setup. Hosts are parameterised, secrets live in
env files, and model names are genericised in the example configs.

## Contents

```
gateway/config.example.yaml   LiteLLM routing: tiers, per-task pins, weighted
                              background group (DB-less mode)
serving/
  docker-compose.example.yml  vLLM stacks: TP2 primary + single-GPU aux
  docker-compose.sglang-glm53.example.yml
                              SGLang TP2 stack (GLM-5.3/MTP class)
  compose.env.example         env templates, one per service
  compose.env.aux.example     env for the aux-35b service
  sglang.env.example          env for the SGLang stack
  adaptive.example.json       adaptive-MTP draft profile
benching/
  bench_matrix.py             concurrency × context matrix runner over
                              llm-inference-bench
postmortems/
  2026-08-29-*.md             rollback removed boot logs before diagnosis
  2026-08-31-*.md             unpinned background route stalled a session
```

## Routing

Three tiers in the gateway config:

1. Interactive: the primary chat model on a dedicated TP2 GPU pair.
2. Auxiliary: Qwen3.6-35B-A3B on its own GPU. Every background task type
   (summarisation, titling, extraction, triage) has a named pin to this
   tier; no route falls through to the primary pair.
3. Background: a weighted group, weight 10 on the auxiliary GPU plus a
   capped TP2 member (weight 1, `rpm: 4`, `max_parallel_requests: 1`).
   Weight sets selection probability; the caps are what bound the impact on
   interactive traffic. Remove the TP2 member for strict isolation.

On 2026-08-31 an unpinned background compaction job followed the default
route onto the TP2 pair and stalled a live session for about 10 minutes
while the auxiliary GPU sat idle. After the task-type pins were added, a
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
  NVLink/P2P (TP2 traffic crosses the root complex)
- GPUs: TP2 pair = 325 W Max-Q cards; aux = one 600 W card

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

The GLM prefill row is from the 2026-09-03 rerun, server-validated on each
cell. The 09-02 scan showed a 32k/64k dip (3.7k/4.3k) that did not repeat in
either rerun; treated as sample noise from concurrent load.

Qwen3.6-35B-A3B had the highest c4 throughput in this test, so background
traffic runs there and the TP2 pair is reserved for interactive traffic.
GLM-5.3-Flash c1 decode changed from 140–153 tok/s at short context to
132 tok/s at 131k, which set the long-context expectations for the
interactive tier. Prefill was cross-checked against the engines' Prometheus
counters: 5.9k tok/s client-measured vs 6.2k server-side on an 83.5k-token
prompt, under 5% gap.

## Incidents

Two incidents led to changes in the current setup.

- [Rollback erased diagnostics during a 1M-context rollout](postmortems/2026-08-29-max-model-len-boot-failure.md):
  the 1M configuration failed to boot, the automatic rollback restarted the
  container and destroyed the boot logs, so the startup failure was never
  root-caused. Logs are now captured before any revert; deliberate restarts
  require approval; risky changes cut over on a spare port.
- [Unpinned background route stalled an interactive session](postmortems/2026-08-31-aux-compaction-stall.md):
  a compaction job followed the gateway's default route onto the TP2 pair
  and stalled a live session for ~10 min; the auxiliary GPU had spare
  capacity throughout. Six task types are now pinned by name, and the
  standing rule distinguishes unpinned routes from bounded, explicit
  overflow.

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

A warm thanks to the many members of the Local Inference Lab discord, you're all truly wonderful

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
