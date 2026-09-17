# Postmortem: MAX_MODEL_LEN 1M Boot Failure

Date: ~2026-08-29 · Service: primary chat model (vLLM, TP2) · Severity: 8 min
service downtime · Author: Mark Yong Enhan

## What was attempted

Raise the primary model's context window to 1M tokens on the production TP2
pair, watching health and rolling back automatically if startup failed.

## What happened

- The 1M configuration never reached a healthy state within the 300-second
  readiness window.
- The automatic rollback reverted the configuration and restarted the service —
  and in doing so, destroyed the boot logs that explained WHY it failed.
- The service recovered in ~8 minutes, but the root cause was never positively
  identified.

## What made it worse

The rollback logic optimised for fast recovery and treated logs as ephemeral
container state. On a system where the container restart is the failure signal,
throwing away the logs throws away the diagnosis. The root cause remains
unknown to this day — an honest limitation.

## Root cause (process, not technical)

Observability was not part of the rollback path. Recovery automation and
diagnosis automation were treated as separate concerns; they are not.

## Fix and follow-up

1. Standing rule for any context-window or capacity change: capture complete
   docker logs BEFORE any revert or restart can touch the container.
2. Container lifecycle policy: containers serving live traffic are never
   recreated by automation — restarts are explicit, human-approved actions.
3. Cutover pattern for risky changes: stand up the new configuration on a spare
   port, probe it, then cut over — never mutate the healthy production path in
   place.

## Why this story belongs in interviews

It is a real production incident where the correct answer was a governance
change, not a config tweak — the same lesson that governs production ML
deployments anywhere: the rollback path is part of the system, and it must
preserve evidence.
