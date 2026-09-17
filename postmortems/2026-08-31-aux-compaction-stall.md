# Postmortem: Auxiliary-Task Compaction Stall

Date: 2026-08-31 · Service: primary chat model (TP2 pair) · Severity: ~10 min
interactive session stall · Author: Mark Yong Enhan

## What happened

A large auxiliary job (session compaction) routed onto the primary chat model's
tensor-parallel GPU pair instead of the auxiliary model's dedicated GPU. With
thinking mode enabled on top, the job contended with interactive traffic and
stalled a live session for about 10 minutes.

## Diagnosis

Traced the routing chain and found the auxiliary tier had no hard pin: tasks
without an explicit route could fall through to the default heavy model on the
shared pair. The failure was not a capacity problem: the auxiliary 35B model
on its own GPU had ample headroom; it was a routing governance gap.

## Fix

1. Pinned six auxiliary task types (summarisation, title generation, memory
   flush, background review, extraction, triage) to the auxiliary model by
   name at the gateway, removing the fall-through path entirely.
2. Established the standing rule: interactive and background workloads must
   never share the same serving pair; routing policy enforces the tiering, not
   good intentions.
3. Verified: after the pin, a full auxiliary tick (17 fetches plus digests)
   completes in ~35s on the dedicated GPU with no interactive contention.

## Why this story belongs in interviews

Multi-model serving fails quietly: everything works until two workloads meet
on one GPU. The durable fix is routing policy at the gateway layer; the same
lesson as workload isolation in any multi-tenant system, applied to inference.
