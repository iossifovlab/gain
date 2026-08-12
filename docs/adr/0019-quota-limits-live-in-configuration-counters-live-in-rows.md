# 19. Quota limits live in configuration, counters live in rows

- **Status:** accepted
- **Date:** 2026-08-12
- **Issues:** [gain#750](https://github.com/iossifovlab/gain/issues/750) (this
  record), [gain#670](https://github.com/iossifovlab/gain/issues/670) (the
  ambiguous all-zero row), [gain#748](https://github.com/iossifovlab/gain/issues/748)
  (the construction-time override this removes),
  [gain#747](https://github.com/iossifovlab/gain/issues/747) (the row lock, which
  stays), [gain#768](https://github.com/iossifovlab/gain/issues/768) (the narrow
  reset write, which stays),
  [gain#749](https://github.com/iossifovlab/gain/issues/749) /
  [gain#788](https://github.com/iossifovlab/gain/issues/788) (the field
  declarations this builds on)

## Context

A quota row meters three resources — jobs, variants and attributes — over two
periods, daily and monthly, against limits configured per quota type in
`QUERY_QUOTAS`. Two things could own the number in a column, and until this
change the codebase had not decided which.

The six period columns stored the units a row had **left**. The limits they
were measured against were never stored: they were read from configuration at
call time. So a limit and a counter met exactly once, at a period refresh,
which wrote the limit of that moment into the row. Between refreshes the row
was authoritative and the configuration was inert.

That produced three defects that read as unrelated but are one:

1. **A raised limit did not reach an existing row.** The predicates never
   consulted a limit at all — they compared a stored remaining counter against
   zero — so doubling a configured limit granted an existing user no
   additional capacity until the next refresh.
2. **The reported figure was incoherent.** The quota endpoint paired a stored
   counter (`current`) with a live limit (`max`), so an untouched user could
   be reported at "1,000,000 / 2,000,000" the moment a limit was doubled: the
   denominator moved and the numerator did not.
3. **An all-zero row was ambiguous.** A fresh row's honest `default=0` had to
   mean "exhausted" (gain#670). gain#748 fixed it by overriding construction so
   a new quota was inserted holding its limits — which is the same coupling
   again, at a third site.

The only lever available under that representation was an admin-triggered
refresh, which restores capacity but forgives every user's consumption as a
side effect. There was no cheap fix.

## Decision

**A configured limit is the current truth. A row stores only what has been
consumed against it.**

Concretely: the six period columns store units **consumed** since that period
was last refreshed. Nothing stores a limit. Every reader derives headroom as
`limit - consumed` against the limit read from configuration at that moment.
A period refresh sets its counters to zero. A new row needs no construction
override, because zero consumed is exactly what a new row has.

### What did *not* change: the wire

Every HTTP surface keeps speaking **remaining**, because that is what it
always meant and what the UI displays:

- the quota endpoint's `current` still counts down as a user consumes;
- the admin panel's `set-current-quota` still takes the number of units the
  operator wants the user to have left, so `amount=0` still exhausts a quota;
- the quota export CSV still reports what a user has left under its existing
  column names.

An operator can also still put a single row *above* its type's configured
limit, which the panel could always do while the column held units outright,
and which the e2e suite relies on to stop the shared IP quota from binding.
That is stored as a **negative** consumption — the row has used less than
nothing against its limit — and is spent before the limit begins to apply. A
row can therefore sit on either side of zero, and the sign says which:
positive is consumption, negative is a grant beyond the limit.

The conversion therefore sits at exactly two places: `QuotaSnapshot.from_quota`
on the way out, and `Quota.set_remaining` on the way in. Everything downstream
of the snapshot — the predicates, the two-quota merge, the endpoint, the admin
response — is untouched.

This was deliberate and is the part most likely to be re-litigated. The
alternative considered was to carry consumed units all the way out to the
snapshot, converting at each edge instead. It was rejected for two reasons.
The first is that the e2e suite pins the wire meaning in a way that is easy to
break silently: `setCurrentQuota(email, 'daily_variants', 0)` is the setup for
"blocked when the daily variant quota is exhausted", and under consumed-on-the-
wire that call would produce a *fully available* quota and the test would pass
while exercising the opposite of its name. The second is the merge, below.

### Why the extras keep the other convention

The three `extra_*` columns store units **remaining**, and continue to. An
extra-unit grant is a balance, not a period allowance: there is no limit to
measure it against, and a refresh must not clear it. A quota row therefore
holds both conventions at once. That is a real cost — it is stated here
because it reads as an oversight otherwise — but the alternative is inventing
a limit for a field that has none.

### Consequence: limits are live in *both* directions

Lowering a limit below what a row has already consumed refuses that user
immediately, where previously they kept their allowance until the next
refresh. This is the correct reading of "the configured limit is the current
truth" and it is intended, not an accident. It is called out here rather than
discovered in production.

### Consequence: over-consumption stays forgiven

A charge stops at the limit rather than recording an overdraft. This preserves
what the old floor-at-zero did, and it is a decision rather than an
inheritance: when extras cover an overshoot, the excess is already charged to
the extras pool, so recording it against the period counter too would charge
the same units twice and surface later as missing headroom after a limit
raise.

A row that a *lowered* limit has left above its limit is never charged back
down to it, which is why the stop is the larger of the limit and what is
already consumed.

## The trap this avoids, and the guard that replaced it

An anonymous user's effective quota is the field-wise merge of their session
and IP snapshots, and the merged object carries the predicates that gate
access. Had the snapshot carried consumed units, "more restrictive" would have
inverted to a **maximum** for the six period fields while staying a **minimum**
for the three extras. A mechanical port keeping `min` throughout would have
granted every anonymous user the *more permissive* of their two quotas — and
no existing test could have caught it, because both anonymous quota classes
resolve to the same configuration block and so agree in every scenario the
suite constructs.

Deriving headroom in `from_quota` removes that hazard rather than navigating
it: fewer units left is stricter for all nine fields, so the merge stays a
minimum throughout.

A second layer of that assumption survives and is now asserted. The merge
takes *both* rows' limits, which describes a real quota only while the two
classes are configured identically. That was harmless while the predicates
ignored limits; it is load-bearing now. `QuotaSnapshot.minimum` therefore
raises if the two snapshots' limits differ, rather than silently answering
with a row that never existed.

## Consequences

- Migration `0044` converts existing rows for all three quota types, using
  each type's limits as configured when it runs. That bakes in the limits of
  the moment — the very coupling being removed — but only once, and there is
  nothing else to convert against: a row records no history of the limit it
  was last refreshed to.
- A row whose stored remaining exceeded its current limit converts to a
  negative consumption, and is clamped to zero. Under live limits such a user
  has consumed nothing against the new limit.

  There are **two** sources of such a row, and the clamp treats them alike
  although they differ. One is the residue of a limit having been *lowered*,
  where clamping is plainly right. The other is an admin grant deliberately
  placed above the limit, which the clamp silently revokes at migration time
  — the row comes back at exactly its limit rather than at the grant. This
  was accepted rather than solved: distinguishing them needs information the
  row does not carry, and the grant is re-issuable from the admin panel,
  which after this change stores it as a negative consumption and so keeps
  it. Re-issue any deliberate over-limit allowances after deploying.
- The reverse migration is `limit - x` again, so a rollback is possible, but
  the clamp is not invertible: a row that clamped comes back as a full
  allowance rather than as what it held.
- gain#748's construction-time override and its initial-counters helper are
  gone, along with the model docstring paragraph explaining why zero meant
  "exhausted".
- The row lock from gain#747 and the narrow reset write from gain#768 both
  stay. The original issue argued that consumed units would let the six
  counters become an atomic `F()` update and remove the lock; that is not what
  shipped, and could not be: the extras draw depends on both periods' headroom
  *before* the write, all three extras are zeroed together on a condition
  computed from that same read, and a job completion applies the charge three
  times in sequence where each application can zero the extras the next one
  reads. The lock was needed for the extras either way.
- `_max_for` indexes the settings dict **by column name**, so a period
  counter's column name doubles as its settings key. Renaming a column to
  advertise the new meaning (`daily_variants_consumed`) would break that
  identity quietly. The columns were therefore *not* renamed: the meaning is
  recorded here instead. A future rename needs `RESOURCE_FIELDS` to carry a
  settings key of its own.
