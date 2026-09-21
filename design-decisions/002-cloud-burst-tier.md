# Design decision 003: cloud burst tier, not cloud fallback habit

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

## Notes for reusers

The distinction that matters is structural: fallbacks/pins only, never a
shared-weight deployment. A cloud deployment with weight > 0 in a latency
routing group IS a primary, whatever the README says.
