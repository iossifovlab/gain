"""Genomic position table backends.

**Removed export: ``VCFLine``.**  It was in this package's ``__all__`` and so a
public name of ``gain``; #237 deleted it, because the VCF backend no longer
builds a per-line object at all -- it yields records (``record.py``), like every
other record backend.  This is a breaking export change, recorded here because
nothing else records it: an importer of ``VCFLine`` now gets an ImportError.
There is no drop-in replacement object, and none is wanted -- a VCF line is a
record tuple, and what used to be read off a ``VCFLine`` is read from the
record's slots (``CHROM`` ... ``ALT``) or, for scores, through the score
layer's ``GenomicScore.get_score_value_from_record``.

**Removed exports: ``Line`` and ``BigWigLine``** (and, with them, the
``LineBase`` protocol they satisfied and the ``row()`` method all three
declared).  Both were in this package's ``__all__``, so both are public names of
``gain``, and an importer of either now gets an ImportError -- the same breaking
change as ``VCFLine``, recorded here for the same reason.  With ``VCFLine``
already gone in #237, these were the last two of the three line adapters this
package ever exported.  #239 deleted them once #238 had migrated bigWig, the
last backend that built one: every backend now yields records, so nothing
constructed a line adapter and nothing consumed one.  ``LineBase`` went with
them because a protocol with no implementors describes nothing, and ``row()``
-- which serialised an adapter back to its raw row -- went because its only
caller, ``save_as_tabix_table``, was itself dead and was deleted in #235.

The score layer's adapter-era ``ScoreLine`` was deleted by #239 too, but it was
never exported from this package and was never an adapter itself -- it *wrapped*
one, asserting its line was a ``Line`` or a ``BigWigLine``.  That assert is why
it could not outlive them.  It has no bearing on this package's exports; a score
caller goes through ``GenomicScore.get_score_value_from_record`` (see below).

There is no replacement and no deprecation shim.  A shim was considered and
rejected: it costs nothing to anyone who does not call it, but hands anyone who
*does* call it back the exact per-line allocation this whole migration exists to
remove.  A caller reading coordinates off a ``Line`` reads them from the
record's slots instead (``record[CHROM]``, ``record[POS_BEGIN]``,
``record[POS_END]``, ``record[REF]``, ``record[ALT]``); a caller using
``line.get(key)`` for a column indexes the record's payload
(``record[PAYLOAD][key]``); a caller wanting the whole raw row back takes
``tuple(record[PAYLOAD])``.  For scores, none of this is the intended route at
all -- go through the score layer's ``GenomicScore.get_score_value_from_record``
or ``get_score_values_from_record``, which read the same slots and additionally
handle NA values, parsing and aggregation.

**The ``tuple()`` around that last one is the migration, not noise.**
``row()`` returned ``tuple(self._data)`` in both adapters -- an immutable
snapshot of the row, taken there and then.  ``record[PAYLOAD]`` is not that:
the payload is the backend's row held **by reference**, deliberately neither
copied nor frozen (``record.py``), and for the tabix backend it is a
``pysam.TupleProxy`` that ``LineBuffer`` may still be holding: write to it and
you mutate a buffered row (see ``line.py``).

**It does NOT get reused as the fetch advances**, which an earlier version of
this note claimed.  ``pysam.asTuple()`` hands up one proxy object PER LINE (as
``TabixGenomicPositionTable.get_line_iterator`` says), so retaining a record
past its iteration keeps its own cells: materialising a region with ``list()``
and reading the payloads afterwards gives each row's real values, measured.
The mutation hazard above is real; the aliasing one was not.

``tuple(record[PAYLOAD])`` reproduces what
``row()`` handed back, and is what a ``row()`` caller migrates to.

**``fchrom`` has no record equivalent, and is the one ``LineBase`` attribute
with no slot to move to.**  There is no FCHROM slot, and ``record[CHROM]`` is
NOT one: ``Line`` carried the file's own contig in ``fchrom`` and the
reference contig in ``chrom``, and under a configured ``chrom_mapping`` those
hold *different* values -- the tabix backend overwrote ``chrom`` with the
mapped reference contig and left ``fchrom`` at the file's.  A record's CHROM
slot is the mapped one, so migrating ``line.fchrom`` to ``record[CHROM]`` is
not an error, it is wrong data, on exactly the tables that configure a map.

The file contig is still readable -- but from the **table**, which is why it is
not a slot.  For the tabular backends it is ``record[PAYLOAD][table.chrom_key]``
(the raw row's contig cell -- literally the expression ``Line.__init__`` read
its own ``fchrom`` from), or ``table.unmap_chromosome(record[CHROM])`` back
through the map.  Both need the table, and a caller holding records has one.
Adding a sixth decoded slot to spare it that is not on the table here: a record
is what *every* backend yields, and a file contig is not something every
backend has to give.  bigWig's payload repeats the already-mapped reference
contig (``BigWigLine.fchrom`` was set from it -- that adapter's ``fchrom`` was
never a file contig at all), and a VCF record's payload is a variant, whose
file contig is ``record[PAYLOAD][VARIANT].contig``.  The slot would mean three
different things, which is the sort of thing the five decoded slots exist to
not do.

``LineBuffer`` is NOT part of that removal and remains exported: it outlived the
adapters it used to hold and now buffers records (see its own note below).

**Removed method: ``LineBuffer.pop_first``.**  ``LineBuffer`` is in ``__all__``
below, so this too is a breaking change to a public name of ``gain``, recorded
here for the same reason.  It had no caller anywhere in the stack (gain or gpf)
and no replacement is wanted: #250 gave the buffer an invariant that a bare
``popleft`` cannot keep.  Eviction has to go through
:meth:`LineBuffer.prune() <.line.LineBuffer.prune>`,
which drops a record only when its ``pos_end`` has fallen below the query --
wherever that record sits (gain#287), not merely while it is at the head.  That
rule is what makes the buffer *complete* from the pruned-to position onwards,
and completeness is what the read path's buffer-hit answer rests on.
``pop_first``
dropped the leftmost record unconditionally, so it could evict one that still
overlapped later queries and leave the buffer answering from a hole -- silently,
and with no fall-through to the file to rescue it.  (It would also leave
``_max_end``/``_max_width`` stale, but only ever *high*, which is the harmless
direction -- see :class:`~.line.LineBuffer`.  The completeness break is the
real one.)
**Changed extension point: a backend now implements
``_load_file_chromosomes``, not ``get_file_chromosomes``.**  Neither name is in
``__all__`` below, so this breaks no public name of ``gain`` -- it is recorded
here because ``get_file_chromosomes`` was an *abstract* method whose docstring
named it the thing "to be overwritten by the subclass", which makes it the
documented way to write a backend, and an out-of-tree backend that overrides
the old name now fails to instantiate (the new abstract hook is unimplemented).
No such backend exists anywhere in the stack; every in-tree one was migrated
with the change.

``get_file_chromosomes`` still exists, unchanged in name, signature and
meaning.  What changed is that it is now CONCRETE on ``GenomicPositionTable``,
memoising per instance over the new hook.  It carried ``functools.cache``
before, which keyed a class-level memo by ``self`` and so pinned every table
that was ever opened for the life of the process -- unbounded growth under
``grr_manage resource-repair``, which builds one table per region task
(gain#345).  A backend migrates by renaming its override and deleting the
decorator; it must not memoise on its own, since the base class now does.

**Changed contract: a CLOSED table refuses reads.**
``TabixGenomicPositionTable``, ``BigWigTable`` and ``VCFGenomicPositionTable``
are in ``__all__`` below, so this is a change to public names of ``gain`` --
recorded here for the same reason as the removals above, because nothing else
records it.  #350 made
``close()`` release everything a table read out of its file, the contig order,
the chromosome map and the ``get_file_chromosomes`` memo included, and the
reads that used to be answered out of that retained state now fail instead.
Four of them fail in one stated way, ``ValueError``, on all four backends:
``get_chromosomes()``, ``get_file_chromosomes()``,
``find_chromosome_length()`` and ``get_chromosome_length()``.  Those four are
what an out-of-tree caller may write an ``except ValueError`` around.
``find_chromosome_length`` is the one that reads the file handle -- the raising
wrapper above it only classifies what it returns -- and it carries the guard for
a reason beyond the diagnostic: its other answers include
``ContigExtent.EMPTY``, which is not an error, so a closed table falling through
to it would report every contig as holding no records and a whole-genome scan
would skip the genome and still record a fresh ``stats_hash`` (gain#509).

**The record reads
(``get_all_records()``, ``get_records_in_region()``) refuse as well, but their
exception type is NOT part of the contract**: neither carries a not-open guard
of its own, so measured on a closed table some backend/method pairs raise the
same ``ValueError`` on their way through ``get_chromosomes()`` while the rest
run into a pre-existing ``assert`` in the fetch path and raise a message-less
``AssertionError`` -- or, under ``python -O``, which strips asserts, whatever
the next line makes of the released state (``AttributeError`` on ``None``,
``KeyError`` off an emptied contig dict).  Those asserts are older than this
contract and were left alone; do not catch on them, and do not read the
uniformity of the first three as covering the record reads.  For an
out-of-tree caller all of it is the difference between an answer and an
exception; the migration is to
read inside the open table's lifetime, or to reopen -- ``open()``
re-establishes all of it, and a reopened table answers exactly as one that was
never closed.  Nothing in-tree was affected, which is why the ledger entry is
the whole mitigation and there is no shim: every in-repo read sits behind
``GenomicScore.is_open()``, and ``gpf`` has no non-test caller of
``get_chromosomes()``/``get_file_chromosomes()`` at all.

#358 then made that contract UNIFORM rather than changing it again, in the two
places the backends disagreed.  ``InmemoryGenomicPositionTable`` answered a
closed ``get_file_chromosomes()`` with ``[]`` -- the scanned-contig list its
``close()`` empties -- and the base class's memo cached that empty answer for
the rest of the table's life; ``BigWigTable`` refused with a bare ``assert``,
which ``python -O`` strips, leaving it answering ``[]`` from an emptied contig
dict.  Both now raise the ``ValueError`` the tabix and VCF backends already
raised, so a caller catches one thing from all four (and a closed VCF table
refuses for a never-opened one's reason too: its guard is the absent handle,
which covers both states).  ``get_chromosome_length`` was brought into line the
same way in the two backends that were not: the in-memory one used to raise out
of the middle of a message it could not finish building -- every contig takes
the no-records branch on a closed table, and that branch interpolates
``get_chromosomes()`` -- and the bigWig one guarded with the same bare
``assert``, which under ``python -O`` let a closed table fall through into that
very branch.  Both now say the table is not open, as tabix and VCF already did.

**Deliberately NOT changed: ``map_chromosome``/``unmap_chromosome`` pass
through on a closed table.**  Both return their argument unchanged when the
chromosome map is ``None``, and ``close()`` sets it to ``None`` -- so a closed
*mapped* table hands reference-space names back as if they were the file's,
which is the one closed-table read that answers instead of refusing.  It stays
that way because the state cannot tell the two cases apart:
``_build_chrom_mapping`` sets ``chrom_map = None`` on an OPEN table that
configures no mapping, so the field does not distinguish closed from
mapping-free, and a table carries no open/closed flag.  Introducing one would
put a new invariant on every backend and a new failure on the read path, which
is exactly what #350 avoided.  Named here so the ambiguity is a decision on
record rather than something a caller has to rediscover; the same note is on
``GenomicPositionTable.close``.

**Changed extension point: ``close()`` is no longer abstract, and a backend's
``close()`` must now CALL UP into the base one.**
``TabixGenomicPositionTable``, ``BigWigTable`` and
``VCFGenomicPositionTable`` are in ``__all__`` below, so this changes public
names of ``gain`` and is recorded for the same reason as everything above.
#354 gave the release policy of #350 a shared implementation:
``GenomicPositionTable.close()`` became CONCRETE and gives up the base class's
own file-derived state -- ``chrom_order``, ``chrom_map``, ``rev_chrom_map``
and the ``get_file_chromosomes`` memo.  The abstract set is now exactly
``open``, ``get_all_records``, ``get_records_in_region``,
``find_chromosome_length`` and ``_load_file_chromosomes``.

**For an out-of-tree backend this breaks in the OPPOSITE shape to the two
renames above: nothing fails at instantiation any more.**  A backend that
already defines ``close()`` keeps working and simply acquires an obligation --
end it with ``super().close()``, or the table holds its contig order and its
chromosome map for as long as its holder lives, which no exception reports and
which ``open()`` would have rebuilt anyway.  A backend that defines NO
``close()`` is the sharper case: it used to be impossible to construct
(``TypeError: Can't instantiate abstract class ... close``) and now constructs
fine, inheriting a ``close()`` that releases the chromosome state and never
touches the backend's own file handle -- so ``GenomicScore.close()`` reports
success over a live pysam/pyBigWig descriptor, which is the fd leak the whole
#345/#350 line of work exists to prevent.

Recorded rather than fixed, and deliberately NOT reverted to an abstract
``close()`` over a separate ``_release_file_state()`` hook (gain#359).  That
would refuse, at the first INSTANTIATION, exactly the out-of-tree backends
that define no ``close()`` -- a backend that already defines one keeps
constructing fine -- to buy coverage the in-tree tests already give, at the
price of changing a documented extension point a second time.  What is
enforced instead is that the tree's own backends cannot slip past those tests
-- ``test_every_backend_in_the_tree_is_in_the_backend_list`` sweeps
``GenomicPositionTable.__subclasses__()`` and fails a concrete backend that no
fixture builds, and ``test_a_closed_table_releases_what_open_established``
then catches both failure modes above on any backend it is handed.  There is
no such sweep for a backend outside this repo, which is what this entry is
for: call up.

**New optional capability: ``get_region_value_arrays`` and the
``supports_value_arrays`` flag that declares it** (gain#398).  ``BigWigTable``,
``TabixGenomicPositionTable`` and ``VCFGenomicPositionTable`` are in ``__all__``
below, so this adds public surface to ``gain`` and is recorded for the same
reason as everything above.

``get_region_value_arrays(chrom, pos_begin, pos_end, value_columns,
batch_size)`` reads a region as batches of column arrays --
``(pos_begin, pos_end, {column index: raw cells})`` -- without building a
``Record`` per row.  It is a fast path for a full sequential scan, and it is
OPTIONAL: the base class refuses with ``TypeError``, and a backend that serves
it overrides the method *and* sets ``supports_value_arrays = True``.  Cells come
back unparsed and rows are not clipped to the region; both stay with the caller,
as on the record path.  ``batch_size`` is a hint -- ``BigWigTable`` ignores it,
its batches being sized by its own adaptive fetch window.

**Ask the flag; do not test the class.**  The capability is NOT derivable from
the class hierarchy -- ``VCFGenomicPositionTable`` subclasses
``TabixGenomicPositionTable``, inherits its implementation, and sets
``supports_value_arrays`` back to ``False``.  An out-of-tree caller reaching for
the method must consult the flag (or
``GenomicScore.supports_region_value_arrays(scores)``, which folds this flag
together with the value types its own parse requires, and is answerable on an
unopened score).  Probing by calling and catching does NOT work: an unguarded
call on a VCF table reaches the inherited tabix implementation and trips its
``assert isinstance(self.pysam_file, pysam.TabixFile)``, yielding a
message-less ``AssertionError`` -- and nothing at all under ``python -O``.

Why the capability is declared rather than inferred, why VCF cannot honour the
contract it inherits, and why this read path exists at all: see
``docs/adr/0001-bulk-read-path-for-statistics.md``.

**Changed payload: a VCF record's PAYLOAD is now a FOUR-element tuple.**
``VCFGenomicPositionTable`` is in ``__all__`` below, so this changes public
surface of ``gain`` and is recorded for the same reason as everything above.
It was ``(variant, allele index)``; it is now
``(variant, allele index, info, info_meta)``, the last two being the pysam
proxies an INFO lookup needs -- ``variant.info`` and ``variant.header.info``.

They are there because pysam allocates a FRESH proxy on every access
(``v.info is v.info`` is False, ~85ns each), so a reader that re-derived them
per score paid ~170ns per score per record: measured, a 20-score read of a
3000-row VCF went from 8.50 to 10.76us/line.  They used to be memoised on the
per-line ``VCFScoreLine``, resolved on its first score read.  With the score
lines removed, reading a value is a pure function of the record
(``_extract_vcf_value``), so the memo had to move into the record -- which is
what a backend-defined payload is for.

The trade is that resolution is EAGER: a record whose scores are never read
pays the ~170ns anyway.  That case is narrow (``AlleleScore`` fetches every
record at a position and reads only the ref/alt match, so a 4-allele position
wastes ~0.5us) and it buys a value read that needs no state, and so no
per-line object to hold it.  An out-of-tree reader that unpacked the payload
as a pair now gets a ``ValueError``; unpack four, or index by the ``VARIANT``
/ ``ALLELE_INDEX`` / ``INFO`` / ``INFO_META`` constants in ``table_vcf``.

**Changed payload: a bigWig record's PAYLOAD is now the VALUE ITSELF.**
``BigWigTable`` is in ``__all__`` below, so this changes public surface of
``gain`` and is recorded for the same reason as everything above.  It was the
four-tuple ``(chrom, pos_begin, pos_end, value)`` -- three fields the record
already carries in its decoded slots, repeated purely so the single value was
addressable at ``payload[3]``; it is now the bare ``float``.  An out-of-tree
reader that indexed the payload gets a ``TypeError`` ('float' object is not
subscriptable); read ``record[PAYLOAD]``, which IS the value, or go through
``GenomicScore.get_score_value_from_record``.

**This entry used to say the shape was deliberately NOT changed.**  It is
kept, inverted, rather than deleted, because the three reasons it gave were
real and someone will meet them again; each is answered below.  What dissolved
them is that the alternative to preserving a shape is not "break the deployed
configs" -- it is to keep the *config* and drop the *shape*.

*(a) "The shape is config surface: every deployed bigWig says ``index: 3``."*
It is config surface, and 16 deployed resources do say it (one with the
comment ``# this makes no sense and should be removed`` already in its yaml).
But the key is answered by ACCEPTING it as a deprecated no-op, not by keeping
a payload shape for it to index into: ``bigwig_scores`` takes ``index: 3`` at
open, reports it once naming the resource, and resolves it -- like the
canonical config that addresses nothing at all -- to the one column a bigWig
has.  No GRR has to change on the day this ships.  Any OTHER index is now
refused at open, by name; before, ``index: 2`` read the position and called
it a score.

That report is at DEBUG, not WARNING, and deliberately: all 150 deployed
bigWig resources carry ``index: 3``, so anything louder fires for every one of
them on every open, which is noise wearing a severity label.  The cost is
that the message does not by itself drive the key out of the GRRs -- that is
a deliberate cleanup pass now, not something a log level nags into happening.
``_warn_inert_bigwig_keys`` splits the same way and says why: endemic keys
(``chrom``/``pos_begin``/``pos_end``/``header``) report at DEBUG, the retired
buffering knobs nobody sets stay at INFO, and keys nobody sets that DO mean
something on another backend (``zero_based``, ``header_mode``) stay at
WARNING, because a message that fires for nearly every resource trains its
reader to ignore the level.

*(b) "``get_region_value_arrays`` reconstructs the four-tuple so a bad index
raises the same ``IndexError`` the record path raises."*  Superseded.  That
reconstruction existed to reproduce a failure; the failure is now prevented.
The record path indexes nothing at all, and a bad index is refused when the
SCORE is opened, in a message naming both the resource and the score -- which
is strictly better than an ``IndexError`` from inside a scan, and it fires
before any file is opened rather than mid-repair.  The bulk read keeps a
backstop of its own for a caller that reaches the table directly: it serves
column 0 and refuses everything else with a ``KeyError`` naming the resource.
The bug that motivated the reconstruction -- a misconfigured index served the
chromosome string, turning an aborted repair into a silently all-zero
histogram -- is unreachable from either direction.

*(c) "It buys nothing measurable."*  That measurement was taken WITHOUT the
parse removal, so it never argued against this change.  It compared payload
widths while the read still went through ``parse_value``; narrowing the tuple
alone saves an index, which is indeed noise.  What the narrowing enables is
the removal of the *parse*: a bigWig value arrives from ``pyBigWig`` as a
``float``, the score is ``type: float`` (anything else is now refused), and
the NA default for a bigWig score is empty (``bigwig_scores``), so
``parse_value`` on that pair is provably the identity -- and the read becomes
``return record[PAYLOAD]``.  That is a real per-record saving, and it is only
available once the payload is the value.

**Renamed attribute: ``BigWigTable.direct_fetch_size`` -> ``fetch_size``, and
the config key with it.**  ``BigWigTable`` is exported, so this is public
surface.  It was "direct" only in contrast to a second, *buffered* fetch
strategy, and that strategy is gone: the table keeps no interval buffer
across calls, and ``use_buffered_threshold`` no longer routes anything.  The
rename is **not** aliased -- the capability survives, so a config naming it
the old way means something specific, and failing validation lets an operator
rename it rather than silently receive the default.

The two retired knobs, ``buffer_fetch_size`` and ``use_buffered_threshold``,
are the opposite case and are handled the opposite way: they configured a
feature that no longer exists, so there is nothing to rename them to and
refusing would take a resource offline to report a key that changes nothing.
They stay accepted by the schema and are warned about (see
``utils._warn_inert_bigwig_keys``).

The private buffer machinery went with them -- ``_buffer``,
``_buffer_region``, ``_fill``, ``_find``, ``_fetch_buffered``, ``_last_pos``
-- and ``_fetch_direct``, the surviving strategy, was first renamed ``_fetch``
and has since been inlined into ``get_records_in_region`` (gain#823, to drop
a per-record tuple and a generator level).  None of those were
exported; they are named here only because the two invariants they carried
were load-bearing enough to have their own regression tests, and both are now
unreachable rather than maintained: a reopened table cannot serve a previous
open's values, and a fetch cannot resume from retained state after
``close()``.

**Why** -- the measurements, the one workload where buffering still wins, and
what would have to be true to reinstate it: see
``docs/adr/0002-remove-bigwig-fetch-buffering.md``.  Bringing it back for
high-latency (``http``/``s3``) repositories is tracked as gain#449.

**Changed signature: ``get_records_in_region(chrom)`` is now REQUIRED and
non-optional.**  It was ``chrom: str | None = None``, and ``None`` meant
"every record in the table" -- each of the three record backends opened the
method with ``if chrom is None: yield from self.get_all_records()``.  An
out-of-tree caller writing ``table.get_records_in_region()`` now gets a
``TypeError``; call :meth:`get_all_records` instead, which is what those three
lines did and what the name says.

Two things made the old shape worth giving up.  The default argument list was
itself a legal call, so ``get_records_in_region()`` -- easy to write by
accident, and written by three tests in this repo -- quietly scanned a whole
genome.  And the delegation was stated three times, once per backend, for a
method that then had two jobs whose only shared code was the delegation
itself.  ``get_region_value_arrays`` had already settled on ``chrom: str``,
so the two region reads now agree.

**The whole-table mode is not gone, only moved.**  It is live -- ``grr_manage
--region-size 0`` computes statistics in a single pass -- but it is expressed
by ITERATING contigs, in ``scan.do_noregion_histograms``, rather than by
handing a null contig down.  No member of the region-read family takes
``chrom=None``:
``GenomicScore.fetch_records`` and ``fetch_region_segment_scores`` both
require a contig, and a caller that wants every record of a table asks the
table (``get_all_records()``).

**``GenomicScore.fetch_region`` is gone; use ``fetch_region_segment_scores``.**
(The replacement was named ``fetch_region_values`` when this entry was
written; the rename is its own entry below.)  There
were two of them and they meant opposite things: on ``PositionScore``
``fetch_region`` was a pure alias of ``fetch_region_values``, while on
``AlleleScore`` it was the real read and ``fetch_region_values`` the adapter
over it -- yielding ``(pos, ref, alt, values)`` where every other kind yields
``(begin, end, values)``.  ``fetch_region_values`` is now the single region
read, uniform across all three kinds.  Its REF/ALT were what the allele
variant added, and nothing consumed them: a caller that needs the nucleotides
reads ``record[REF]`` / ``record[ALT]`` off ``fetch_records``, which is what
``AlleleScoreAnnotator`` and ``AlleleScore._fetch_allele_record`` already
did.

**New export ``ContigExtent`` and a new abstract method
``find_chromosome_length``; ``get_chromosome_length`` is no longer abstract.**
Recorded here because ``__all__`` is this package's public surface and the three
backends in it inherit the method, which makes it a public name of ``gain`` (the
same reason the closed-table note below is recorded).

**For CALLERS this is purely additive.**  ``get_chromosome_length`` keeps its
signature and its contract -- still ``int`` or ``ValueError`` -- and is now
CONCRETE on ``GenomicPositionTable``, a thin raising view of the new hook.  No
existing call site changes.

**For an out-of-tree BACKEND it is a break, in the same shape as the
``get_file_chromosomes`` rename above: the class no longer instantiates.**
``find_chromosome_length`` is an ``@abc.abstractmethod``, so a backend that
implements only ``get_chromosome_length`` -- which used to be the abstract one
-- now fails at construction with ``TypeError: Can't instantiate abstract class
... without an implementation for abstract method 'find_chromosome_length'``.
It is a loud failure at the first instantiation rather than a silent behaviour
change, which is why the hook was left abstract: the alternative, a base
implementation delegating to ``get_chromosome_length``, turns a backend that
overrides neither into infinite recursion at call time.  No such backend exists
anywhere in the stack; all four in-tree ones were migrated with the change.

A backend migrates by renaming its ``get_chromosome_length`` to
``find_chromosome_length``, widening the return type to ``int | ContigExtent``,
and returning a member instead of raising where it has no number.  Overriding
``get_chromosome_length`` as well is allowed but pointless, and lets the two
drift.

The enum is exported because a caller cannot interpret the new method's answer
without it, and its two members are deliberately NOT interchangeable:
``EMPTY`` means the backend PROVED the contig holds no records, ``UNDETERMINED``
means no length could be established and the contig may hold records anyway.
Which one a backend can return is a property of the backend -- the in-memory one
holds the whole file and returns only ``EMPTY``, the tabix and VCF ones read an
index that carries only non-empty contigs and return only ``UNDETERMINED``, and
bigWig reads exact sizes from a header and returns neither.  A caller that
splits contigs into regions must treat the two oppositely: skip a proven-empty
contig, read an undetermined one whole.  Collapsing them -- into ``None``, or
into one ``except ValueError`` -- is what this shape exists to prevent, because
it silently drops records from a scan whose ``stats_hash`` then claims the
resource was scanned (gain#509).

``ValueError`` keeps its old meaning and gains a sharper edge: it now marks the
QUESTION as bad rather than the answer as absent -- a table that is not open, or
a contig outside ``get_chromosomes()``.  Note this splits one case the in-memory
backend used to conflate: an unknown contig still raises, while a contig the
table lists and has no rows for now answers ``EMPTY`` from the hook (and still
raises from ``get_chromosome_length``, with the same message as before).

**Two ``get_chromosome_length`` messages did change, both on the tabix and VCF
backends**, because the raising view is now written once on the base class
instead of per backend.  A contig whose length the probe cannot determine used
to raise ``Could not find contig '<file contig>'`` and now raises ``could not
determine the length of contig <contig> in the table's contigs: [...]``; a
contig that will not unmap used to raise ``error in mapping chromsome ...`` and
now reaches the same message, having become ``UNDETERMINED``.  Both name the
contig in REFERENCE space where the old text named the file contig, so the one
thing the old diagnostic carried that the new one does not is which *file*
contig the probe was looking for -- recoverable from the table's
``chrom_mapping``.  The type is unchanged, so an ``except ValueError`` is
unaffected; only a caller matching on the text is.

**Opening a tabix table can now REFUSE the resource** (gain#553).
``TabixGenomicPositionTable.open()`` reads the coordinate columns off the index
it opened the file with and compares them against the column keys the table
resolves; where they disagree it raises ``MalformedResourceError``, before a
record is read.  Recorded here because every caller of a tabix-backed score
inherits the new failure -- and because the exception is a ``ValueError``, so a
caller already catching one around ``open()`` keeps catching this, and
``grr_manage`` reports it as the resource's fault rather than as an internal
error (ADR 0008).

A table configured over the columns its index was built from opens exactly as
before, at the cost of one fixed-size header read per open.  The refusal is
uniform: it is not a mode, and there is no flag to turn it off -- a resource
whose index filters on one span while its records are read through another
returns records that are fetched and then dropped without a trace, and no
caller has any use for that.

**``GenomicScore.fetch_region_values`` is renamed
``fetch_region_segment_scores``; the old name survives as a deprecated alias**
(gain#729).  The method yields one tuple per underlying RECORD -- a segment,
with that record's own clipped ``(begin, end)`` -- not one value per position,
and "values" is exactly what made callers read it as the latter.  Living on
the shared base, the rename covers ``PositionScore``, ``AlleleScore`` and
``FragmentScore`` at once; ``region_values_from_records`` keeps its name, the
statistics scan composing through it unchanged (ADR 0008).

Unlike ``fetch_region`` above, the old name is NOT gone yet: no in-tree and no
known cross-repo caller used it, but the published
``docs/source/python_interface.rst`` showed it to external readers, so it
stays as a thin forwarder raising ``DeprecationWarning`` until gain#730 -- a
dated removal, per the precedent of gain#343 -- deletes it.

The return type narrowed with the rename: the values slot was
``list[ScoreValue] | None`` and no code path could yield ``None`` there --
both producers build the values slot as a list comprehension over
``_extract_value`` (through ``get_score_values_from_record`` until gain#823
hoisted the extractor out of the loop) -- so the new name promises
``list[ScoreValue]`` and the narrowing runs down the private chain
(``region_values_from_records``, ``_clipped_score_values``,
``_allele_point_values``) and, since gain#734,
through ``fetch_region_weighted_values``' values slot as well.  A caller's
``None`` guard on a yielded values slot is dead code now, as
``aggregate_region``'s was.  ``fetch_position_scores`` keeps its ``| None``:
that one is real, and means "no record covers this position".

**New method, and a new obligation on backend authors:
``buffered_record_count()``** (gain#1120).  ``BigWigTable``,
``TabixGenomicPositionTable`` and ``VCFGenomicPositionTable`` are in
``__all__`` below, so this adds public surface to ``gain`` and is recorded for
the same reason as everything above.

It answers how many records a table is holding from PREVIOUS reads -- not its
contents, and not the last region's size.  The base returns ``0``, which is
the honest answer for a backend that carries nothing across queries: an
in-memory table holds every record and buffers none of them.  Only the tabix
backend (and the VCF one that subclasses it) has a non-zero answer.

The obligation is the reason the method exists. **A caller may stop iterating
a region read at any point**, so a backend that carries state between queries
must release it from a ``finally`` -- the code after a yield loop is never
reached by a generator that is closed part-way. What it retains may not grow
with the number of abandoned reads, and the reads that follow must answer as
though none had been abandoned.

Releasing from a ``finally`` also hands the caller the *timing*: a held
generator can be closed after other queries have moved the table on, so a
backend whose release depends on query order must check that it still is the
current reader (``TabixGenomicPositionTable._prune_if_current``).  A table
serves ONE live region read at a time -- a held generator may be closed
across another query, not resumed.

``test_backend_record_contract.py`` holds every in-tree backend to both
halves.  There is no such sweep for a backend outside this repo, which is
what this entry is for: a backend with cross-query state overrides the method
and releases from a ``finally``.
"""
from .line import LineBuffer
from .table import ContigExtent
from .table_bigwig import BigWigTable
from .table_tabix import TabixGenomicPositionTable
from .table_vcf import VCFGenomicPositionTable
from .utils import build_genomic_position_table

__all__ = [
    "BigWigTable",
    "ContigExtent",
    "LineBuffer",
    "TabixGenomicPositionTable",
    "VCFGenomicPositionTable",
    "build_genomic_position_table",
]
