# 15. A 10x read returns the whole resource by default

- **Status:** accepted
- **Date:** 2026-08-07
- **Issues:** [gain#716](https://github.com/iossifovlab/gain/issues/716);
  supersedes the "`gex_only` keeps scanpy's default" clause of
  [0014-gain-owns-the-10x-readers.md](0014-gain-owns-the-10x-readers.md)

## Context

Both 10x readers defaulted `gex_only` to `True`: an unparameterized read of
a 10x resource kept the `Gene Expression` features and dropped every other
feature type. On the two CellRanger-ARC multiome resources that ADR 0014
surveyed, that default discarded 76–78% of the feature table — the
chromatin peaks — and the resources this stack processes are multiome.
A default under which reading a resource silently discards most of it
misdescribes the resource.

ADR 0014 kept that default deliberately, for byte-compatibility while gain
was taking the readers over from scanpy: every existing resource had been
built with scanpy's default, and the reader replacement had to be provable
byte-identical. It named the silence, not the default, as the defect — a
read that drops features logs what went — and routed the multiome fix
through per-resource `gex_only: false` config edits, left as a follow-up.

That compatibility argument expired when it succeeded. The readers are
gain's ([#708](https://github.com/iossifovlab/gain/issues/708),
[#712](https://github.com/iossifovlab/gain/issues/712)), their equivalence
to scanpy is mechanized, and `parameters:` is a surface gain defines — so
which default that surface carries is gain's decision, and scanpy's choice
is no longer load-bearing.

## Decision

**`gex_only` defaults to `false` in both 10x formats.** An unparameterized
read returns the whole resource. Filtering a multiome down to its genes is
a curation judgement about what a resource *is*, and it is made in that
resource's `genomic_resource.yaml` with `gex_only: true` — never by
omission.

The filter itself is unchanged, and with it opt-in, its report changes
register: a config asking for the filter states its intent, so the drop is
reported at **info**, naming the feature types and counts, without config
advice. The reader of that line is whoever reads the log, not whoever
wrote the config.

**No GRR configs are edited.** ADR 0014's follow-up — adding
`gex_only: false` to the two `zemke2024Epigenetic` configs — is moot: the
default now does it, and a key restating the default is prose waiting to
go stale. No resource wants genes-only-from-a-multiome, so none needs an
explicit `true` either.

## Why it is scoped this way

**The statistics staleness is paid manually, again.** `calc_statistics_hash`
records the raw `parameters:` block, so a default that lives in code is
invisible to it: the flip changes the statistics of every mixed-feature
resource whose config omits `gex_only` without changing their hash, and
nothing triggers a rebuild. Hashing the *resolved* parameters instead —
the precedent `resolve_ann_data_format` set for the format — was
considered and set aside: it is the general
statistics-versioning gap ADR 0014 filed, not something to solve as a side
effect of a one-line default. The deployed resources get a one-time forced
reprocess (`grr_manage resource-repair -f`), exactly as ADR 0014's `h5ad`
path fix did.

**The drift tests hold the filter, never the default.** gain's and
scanpy's defaults now deliberately disagree, so every scanpy comparison
of a multiome passes `gex_only` explicitly on both sides. A red that
meant "upstream still defaults differently" would be a red about a
decision this ADR already made.

## Consequences

- The two multiome resources — the `10x_h5` pair under
  `zemke2024Epigenetic` — and any future CellRanger-ARC resource read
  whole by default; their statistics change accordingly on the forced
  rebuild — feature counts grow by the peaks that were being dropped.
- **Any other GRR carrying mixed-feature 10x resources drifts silently**:
  its stored statistics describe the filtered read until its owner forces
  a rebuild, and nothing warns them. Accepted, as in ADR 0014, and for
  the same reason — the hash cannot express a change in the computation.
- External callers of `load_ann_data_from_resource*` that relied on the
  scanpy-style default now get whole resources; in-repo nothing but the
  statistics build consumes these AnnDatas.
- The small-tier golden statistics record moved with the flip — from the
  three post-filter features to all five — as a deliberate rebuild. The
  integration copy of that golden was deleted rather than updated: it had
  existed to pin scanpy's output while scanpy was still the
  implementation, called no scanpy, and duplicated the small tier's bytes.
