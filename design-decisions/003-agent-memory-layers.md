# Procedural memory for production agents

How the always-on agent layer on this platform remembers, learns, and stops
repeating mistakes. The serving and routing layers are covered in the README
and the other design decisions; this document is the layer above them.

## The layers

1. **Episodic store.** Every conversation is a searchable transcript
   (full-text index). Retrieval is by query with temporal bias: "where did we
   leave X" ranks recent-first; "how did X start" ranks oldest-first.
2. **Semantic memory.** A curated fact bank with strict retention discipline:
   recall before retain (never store what a query would recover); invalidate
   superseded facts instead of re-retaining corrections; storage has a hard
   character budget, so consolidation is atomic (stale entries are merged or
   removed in the same batch that adds new ones).
3. **Procedural memory (skills).** Recurring procedures are captured as
   loadable, versioned markdown with embedded scripts, references and
   verification rules. Skills are authored from live operational failures,
   not written in the abstract.
4. **Vault.** A git-versioned knowledge base (inbox to wiki flow) with a
   nightly consolidation job and a bank-sync step; a dedup script removes
   observations that merely duplicate facts already anchored to documents.

## Skills learned the hard way (two real examples)

**Workday application filling.** The platform rejects characters it does not
document (`~`, `$`, `%` fail validation beyond the published illegal set);
empty auto-added form blocks throw misleading "From required" errors; file
uploads fail through normal automation because the process cannot stat
network paths (solved via CDP-level file injection). Each failure became a
rule in a skill that the next session loads by default; the second
application ran clean on the first pass.

**Career-site taxonomy mapping.** A government portal's field-of-study list
(248 options) contains no "Data Science" entry; the degree name lives in an
optional field that card views do not display; the skill-tag taxonomy lacks
the JD's key term entirely (so the keyword goes into the description prose
instead). Negative results (what does NOT exist in a taxonomy) are recorded
alongside positive mappings, because "we checked, it is not there" prevents
the next session from re-searching.

## The operating principle

The unit of memory is not the fact; it is the *verifiable correction*. Every
incident ends with a rule that changes future behavior: capture logs before
rollback, never mutate the healthy serving path in place, pin auxiliary
workloads at the gateway, fingerprint state diffs. Facts decay; enforced
rules compound. The measure of the system is that the same mistake is never
paid for twice.
