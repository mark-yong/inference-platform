# Design decision 002: cloud burst tier, not cloud fallback habit

Date: 2026-09 (wired into production config) · Status: deployed

## Problem

A fully local serving path has two failure shapes:

1. Planned maintenance: a GPU stack swap takes a serving pair down for
   minutes at a time (model reload, config cutover).
2. Unplanned capacity spikes: a batch of background jobs plus interactive
   traffic can exceed what the idle tiers absorb.

Routing every overflow to a generic cloud provider quietly turns the cloud
into a shadow primary: spend creeps, data leaves the building by default, and
nobody notices until the invoice or the incident.

## Decision

Wire a **burst tier** with three properties:

1. **NVIDIA open models only.** The burst deployments target Nemotron
   (Nano for the fast lane, larger sizes when reasoning depth is needed) on
   Nebius Token Factory. Open weights and open recipes are the point: the
   same model family can be self-hosted later, so the burst tier is an
   elastic extension of the stack, not a dependency on a closed API.
2. **Zero traffic in normal operation.** Burst deployments appear ONLY in
   fallback chains (or explicit pins for deliberate offload jobs). The local
   alias stays primary; cloud fires on failure or cooldown only. A grep of
   gateway logs in a normal week shows zero burst-tier hits, and that is the
   invariant to keep.
3. **Role-named alias.** `burst-nemotron` is a generic tier name; the model
   slug behind it can change (Nano, Super, a newer release) without consumer
   renames. The model list is read from the provider at run time by the
   smoke-check script; slugs are never hard-coded in scripts.

## Consequences

- Planned maintenance routes around itself: `cooldown_time: 45` plus the
  fallback chain keeps consumers working while a pair reloads.
- Spend is observable by construction: any burst-tier hit is an anomaly worth
  a look, visible in tracing.
- The data boundary is explicit: only workloads that opted into the burst
  chain (or an explicit pin) ever leave the LAN, and the config makes that
  boundary auditable in one file.

## Burst target

The original wiring defaulted to Nemotron Nano because it was the cheapest way to
prove the chain. Once the same family was serving locally on flare, a burst to an
earlier size gave two models and two answers, so a difference between the lanes
could have come from the engine or from the weights.

The default burst target is now the checkpoint this node serves itself:
`burst-nemotron-lightning` on `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`.
Property 3 is what made it a one-line change with no consumer touched. Nano stays
routable for the fast lane, and the larger Nemotron sizes stay available on the
same key for work that wants them.

## Notes for reusers

The distinction that matters is structural: fallbacks/pins only, never a
shared-weight deployment. A cloud deployment with weight > 0 in a latency
routing group IS a primary, whatever the README says.

## Live receipts (2026-10-20)

- Key staged: `NEBIUS_API_KEY` in `/opt/stacks/litellm-proxy/.env` on 114
  (litellm service `env_file`), verified in the container env.
- Smoke check (`gateway/token_factory_check.py`, direct to Token Factory):
  28 models, 5 Nemotron; probed `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`,
  1.02 s, reply "ok".
- Gateway wiring: `model_name: burst-nemotron` added to the production
  `config.yaml` (litellm v1.104.0), `api_key: os.environ/NEBIUS_API_KEY`.
  Fallback/pin only — no consumer routes to it; the zero-hit invariant is
  intact. Recreated with `--no-deps` (full-stack recreate hits a postgres
  endpoint race).
- First runtime call through the gateway: POST `:4000/v1/chat/completions`,
  model `burst-nemotron` → content "ok", reasoning present, 81 total tokens.
- Backing slug behind the alias: `nebius/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`.
- Ops gotcha: the stack `.env` is hand-edited with `micro`; saving a stale
  editor buffer silently reverts appended lines — grep-verify after edits.
