# Architecture Decision Records

An ADR records a decision that shaped this codebase: what was decided, what the
alternatives were, why this one won, and what it cost. It is written for the
maintainer who arrives later and asks "why on earth is it done this way" — the
answer should be findable in one place, not reconstructed from issue threads and
commit messages.

These are **internal engineering records**. They deliberately live outside
`docs/source/`, which is the published GAIn documentation site: an ADR is not user
documentation and should not be rendered into it. Nothing in the Sphinx toctree
references this directory.

## Convention

- One decision per file, markdown, named `NNNN-short-slug.md` with a zero-padded
  four-digit number allocated in order (`0001-`, `0002-`, …). Numbers are never
  reused, and a superseded ADR is not deleted — it gets its status changed and a
  pointer to whatever replaced it.
- Start with a `# N. Title` heading and a short metadata block: **Status** (one of
  `proposed`, `accepted`, `superseded by NNNN`, `deprecated`), **Date**, and the
  **Issues** the decision came out of.
- Then the substance. `0001` is the model to copy: *Context* (what the situation
  was, including the measurement if the decision was driven by one), *Decision*
  (what was chosen, stated flatly), the reasoning — including **why the decision
  was scoped the way it was**, which is usually the part a later reader most needs
  — and *Consequences* (what the project now has to live with).
- Write the cost honestly. If a change took several review rounds, or the fixes
  introduced regressions, that belongs in the record. An ADR that reads like the
  decision was obvious and the execution was clean is worse than no ADR: it tells
  the next author that a similar change is safe when the evidence says otherwise.

Write one when a decision is **non-obvious, expensive to reverse, or likely to be
re-litigated** — a new architectural seam, a deliberate restriction, a rejected
alternative that will look attractive again. Not for routine implementation
choices.

## ADRs and the module-header ledgers

Several packages in `gain` carry a long module docstring in their `__init__.py`
recording changes to that package's **public export surface** — a removed export,
a new optional capability, a name that no longer means what it used to. The one in
`gain/genomic_resources/genomic_position_table/` is the fullest example.

The two are not the same thing and must not duplicate each other:

| | Ledger (`__init__.py` docstring) | ADR (this directory) |
| --- | --- | --- |
| **Answers** | "what happened to this name, and what do I migrate to?" | "why is the system shaped this way?" |
| **Scoped to** | one package's public surface | one decision, wherever it reaches |
| **Read by** | someone whose import just broke | someone deciding whether to change or extend the design |

When a decision has both an export-surface consequence and a rationale, the ledger
records **the fact** — this name is new, this one is gone, here is what to call
instead — and **points to the ADR** for the reasoning. The reasoning is written
once, in the ADR.

The reason for that split is the same reason ADR `0001` exists at all: two records
of one decision will drift.

## Index

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-bulk-read-path-for-statistics.md) | A specialized bulk read path for the statistics scan | accepted |
