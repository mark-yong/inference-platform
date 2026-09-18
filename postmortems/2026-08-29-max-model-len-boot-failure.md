# Incident: rollback erased diagnostics during a 1M-context rollout

Date: 2026-08-29
Affected service: primary chat model (vLLM, TP2)
Impact: ~8 min service unavailability
Status: Resolved; technical trigger undetermined

## Summary

A 1M-token context-window change on the production TP2 pair never reached a
healthy state within the 300-second readiness window. The automatic rollback
reverted the configuration and restarted the service, destroying the boot
logs that would have explained the startup failure. Service recovered in
~8 minutes; the technical root cause was never established.

## Trigger

Configuration change: `--max-model-len` raised to 1M on the primary TP2
stack, with automatic rollback armed.

## Cause

Technical root cause: unknown. The boot logs required to determine the
startup failure were destroyed during the automatic rollback.

Contributing process failure: the rollback path recreated/restarted the
serving container before preserving its diagnostic output. The rollback
logic optimised for fast recovery and treated logs as ephemeral container
state; on a system where a container restart is the failure signal,
discarding the logs discards the diagnosis. Observability was not part of
the rollback path.

## Resolution

Automatic rollback restored the previous configuration; the service
returned to healthy in ~8 minutes.

## Corrective actions

- [Implemented] Capture complete docker logs BEFORE any revert or restart
  touches a container (standing rule for context-window or capacity changes).
- [Implemented] Container lifecycle policy: containers serving live traffic
  are never recreated by automation; deliberate restarts are explicit,
  human-approved actions.
- [Implemented] Risky changes cut over on a spare port; the healthy
  production path is never mutated in place.

## Validation

Post-change 1M-context attempts follow the capture-logs-first and
spare-port patterns; no repeat of the evidence-loss failure mode since.

## Operational lesson

Rollback is part of the observability system: recovery automation must
preserve the evidence required to diagnose the failure it is recovering
from.
