# Incident: unpinned background route stalled an interactive session

Date: 2026-08-31
Affected service: primary chat model (TP2 pair)
Impact: one interactive session stalled for ~10 min
Status: Resolved; routing fixed at the gateway

## Summary

A large auxiliary job (session compaction) routed onto the primary chat
model's tensor-parallel GPU pair instead of the auxiliary model's dedicated
GPU, contending with interactive traffic and stalling a live session for
about 10 minutes. The auxiliary tier had no hard pin: tasks without an
explicit route could fall through to the default heavy model on the shared
pair.

## Trigger

A large background compaction job entered the gateway without a task-type
pin and followed the default route to the primary TP2 pair.

## Cause

The auxiliary tier had no hard pin, so unpinned tasks fell through the
gateway's default route onto the primary pair. The failure was not a
capacity problem: the auxiliary 35B model on its own GPU had ample headroom
throughout. The defect was the fall-through path in routing configuration.

## Resolution

1. Pinned six auxiliary task types (summarisation, title generation, memory
   flush, background review, extraction, triage) to the auxiliary model by
   name at the gateway, removing the fall-through path entirely.
2. Standing rule adopted: background workloads must never reach the
   interactive pair through an unpinned or default route. Any deliberate
   cross-tier overflow must be explicit, bounded, and independently
   rate-limited.

## Corrective actions

- [Implemented] Named per-task pins for every auxiliary task type; no
  default route reaches the primary pair.
- [Implemented] Standing rule above written into the routing governance
  (see gateway/config.example.yaml).
- [Implemented] Background group overflow onto the interactive pair, where
  used, is explicit and capped (rpm + max_parallel_requests: 1).

## Validation

A full auxiliary workflow (17 fetches plus digest generation) completed in
~35 s on the dedicated GPU, with no observed interactive contention.

## Operational lesson

Routing policy enforces workload tiering, not provisioning: the incident
cost nothing to fix in hardware because the spare capacity was already
there; the defect was in the gateway's default route.
