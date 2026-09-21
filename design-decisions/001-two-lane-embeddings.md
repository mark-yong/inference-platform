# Design decision 001: two-lane embeddings (GPU ingest, CPU query)

Date: 2026-09-05 to 2026-09-08 · Status: deployed

## Problem

One embedding endpoint served both batch ingestion and query-time embedding.
Two failure modes appeared in the same week:

1. An ingest batch monopolised the endpoint while interactive retrieval was
   trying to embed a query: the query waited behind thousands of document
   chunks.
2. When the embedding model sat on a GPU shared with a serving stack, batch
   ingest and interactive serving competed for the same VRAM.

## Decision

Split the workloads onto two lanes with one contract:

- **Ingest lane (GPU, :8016):** vLLM serving Qwen3-Embedding-0.6B (1024-d).
  Started on demand for batch ingestion, stopped afterwards (~1.1 GiB VRAM
  freed between runs). Never kept warm; never takes query traffic.
- **Query lane (CPU, :8017):** a small torch sidecar, always on, capped at
  4 threads and 8 GB. Query latency is single-digit milliseconds for short
  strings; batch throughput is irrelevant here.

## The contract (the part that almost sank it)

Both lanes must produce **identical vectors** for identical input. The first
CPU implementation used mean pooling with right padding, the default-looking
choice. The GPU lane (vLLM) uses last-token pooling. The vectors were the
right shape, plausibly distributed, and quietly different: retrieval quality
drifted and nothing errored.

The fix and the contract now in force:

- Raw text input (no instruct prefix on either lane; vLLM's /v1/embeddings
  adds none).
- LEFT padding on the CPU lane, so the last real token sits at position -1
  in every batch shape.
- Last-token pooling at position -1, L2-normalised FP32 output.
- Verification: cosine similarity 0.99994 (CPU vs GPU) across a multi-query
  spot check after the fix.
- Standing swap gate: any embedder change (model, version, pooling) must
  demonstrate >= 0.99 cosine agreement against the incumbent before cutover.

## Consequences

- Interactive retrieval never queues behind ingest.
- The GPU is freed between ingest runs.
- The contract is checked at every deploy, not trusted: the gate is part of
  the cutover procedure, not a one-off test.
- The wrong-pooling version is kept in history as the documented failure mode;
  do not resurrect it as a "simpler" alternative.

## Notes for reusers

Any embedding model with a last-token-pooling serving path needs the same
padding discipline. Mean pooling with right padding is correct for models
trained that way (and wrong for this one): check the model card's pooling
specification before assuming either.
