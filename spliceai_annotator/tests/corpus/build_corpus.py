"""Build the frozen SpliceAI differential-test corpus.

Re-runnable corpus builder (issue iossifovlab/gain#320).  It cuts sequence
windows + gene-model rows out of the *real* hg38 / GENCODE GRR, rebases every
locus onto a local contig that starts at position 1 (so deep loci do not
produce chromosome-sized FASTA), and emits:

* ``tests/fixtures/hg38/genome``      -- a ``genome`` resource (the cut contigs)
* ``tests/fixtures/hg38/gene_models`` -- a ``gene_models`` resource (rebased
  real transcripts + a couple of crafted transcripts for the mixed-strand and
  near-chromosome-end rejection axes)
* ``tests/corpus/corpus_manifest.json`` -- every corpus variant with its
  intended ``distance`` and expected disposition.

Requires the real GRR at ``/data/cephfs/seqpipe/grr`` (``GRR_ROOT`` env var to
override).  It does **not** run TensorFlow -- the baseline is pinned separately
by ``regenerate_baseline.py``.

Run:  ``python -m tests.corpus.build_corpus``  (from the spliceai_annotator dir,
inside the worktree venv).
"""
from __future__ import annotations

import itertools
import json
import os
import pathlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from gain.genomic_resources.gene_models.gene_models import (
    GeneModels,
)
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

from tests.corpus.fixture_io import (
    write_gene_models_resource,
    write_genome_resource,
)
from tests.corpus.rebasing import rebase_transcript

GRR_ROOT = os.environ.get("GRR_ROOT", "/data/cephfs/seqpipe/grr")
GENE_MODELS_ID = "hg38/gene_models/GENCODE/49/comprehensive/CHR"
GENOME_ID = "hg38/genomes/GRCh38.p14"

HERE = pathlib.Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "hg38"
GENOME_DIR = FIXTURES / "genome"
GENE_MODELS_DIR = FIXTURES / "gene_models"
MANIFEST_PATH = HERE / "corpus_manifest.json"

FASTA_FILENAME = "corpus.fa"
GENE_MODELS_FILENAME = "gene_models.txt"

# The maximum half-window the annotator ever fetches is width//2 at
# distance=5000 (= 10100); flank every locus a little beyond that so any
# in-gene position is a valid, full-window annotatable at every distance.
FLANK = 10300

DISTANCES = [0, 50, 500, 5000]

# Loci to cut, each a cluster of real overlapping GENCODE genes on chr21.
#   sondonson: SON(+) and DONSON(-) overlap -> multi-gene, both strands,
#              multi-transcript.
#   col6a2:    COL6A2(+), 36 transcripts -> multi-transcript, + strand; also
#              hosts the crafted mixed-strand and near-chromosome-end genes.
LOCI: list[dict[str, Any]] = [
    {"local_chrom": "sondonson", "chrom": "chr21", "genes": ["SON", "DONSON"]},
    {"local_chrom": "col6a2", "chrom": "chr21", "genes": ["COL6A2"]},
]

BASES = "ACGT"


@dataclass
class CorpusVariant:
    """One frozen corpus variant, addressed on a local contig."""

    id: str
    contig: str
    pos: int
    ref: str
    alt: str
    distance: int
    category: str
    expect: str  # "annotated" | "rejected"
    reason: str = ""


@dataclass
class _Locus:
    local_chrom: str
    window_start: int
    seq: str
    transcripts: list[TranscriptModel]
    genes: dict[str, list[TranscriptModel]] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.seq)

    def base_at(self, pos: int) -> str:
        return self.seq[pos - 1].upper()

    def other_base(self, pos: int) -> str:
        cur = self.base_at(pos)
        for b in BASES:
            if b != cur:
                return b
        return "A"


def _cut_locus(
    spec: dict[str, Any],
    genome: ReferenceGenome,
    by_gene: dict[tuple[str, str], list[TranscriptModel]],
) -> _Locus:
    chrom = spec["chrom"]
    gene_tms: dict[str, list[TranscriptModel]] = {}
    for gene in spec["genes"]:
        gene_tms[gene] = by_gene[chrom, gene]

    all_tms = [t for tms in gene_tms.values() for t in tms]
    txmin = min(t.tx[0] for t in all_tms)
    txmax = max(t.tx[1] for t in all_tms)
    window_start = txmin - FLANK
    window_end = txmax + FLANK
    seq = genome.get_sequence(chrom, window_start, window_end)
    assert len(seq) == window_end - window_start + 1

    local_chrom = spec["local_chrom"]
    rebased_genes: dict[str, list[TranscriptModel]] = {}
    rebased_all: list[TranscriptModel] = []
    for gene, tms in gene_tms.items():
        rebased = [rebase_transcript(t, window_start, local_chrom) for t in tms]
        rebased_genes[gene] = rebased
        rebased_all.extend(rebased)

    return _Locus(
        local_chrom=local_chrom,
        window_start=window_start,
        seq=seq,
        transcripts=rebased_all,
        genes=rebased_genes,
    )


def _canonical(tms: list[TranscriptModel]) -> TranscriptModel:
    return max(tms, key=lambda t: t.tx[1] - t.tx[0])


def _intron_positions(
    tm: TranscriptModel, locus: _Locus, margin: int = 150,
) -> list[int]:
    """Deep-intronic (null) positions: midpoints between adjacent exons."""
    positions = []
    exons = sorted(tm.exons, key=lambda e: e.start)
    for left, right in itertools.pairwise(exons):
        if right.start - left.stop < 2 * margin:
            continue
        mid = (left.stop + right.start) // 2
        if _is_interior(mid, locus, 5000):
            positions.append(mid)
    return positions


def _splice_positions(tm: TranscriptModel, locus: _Locus) -> list[int]:
    """Positions on the invariant splice dinucleotides of internal exons."""
    positions: list[int] = []
    exons = sorted(tm.exons, key=lambda e: e.start)
    for exon in exons[1:-1]:
        sites = (exon.start - 1, exon.start, exon.stop, exon.stop + 1)
        positions.extend(
            pos for pos in sites if _is_interior(pos, locus, 500))
    return positions


def _is_interior(pos: int, locus: _Locus, distance: int) -> bool:
    half = (10000 + 2 * distance + 1) // 2
    return half < pos <= locus.length - half


def _take(items: list[int], count: int) -> list[int]:
    """Evenly sample ``count`` items from ``items``."""
    if count >= len(items):
        return items
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


# ---- corpus size knobs (tune to hit the ~500 target vs wall-clock) --------
N_NULL_D50 = 55
N_NULL_D500 = 18
N_NULL_D5000 = 6
N_SPLICE_D50 = 30
N_SPLICE_D500 = 8
N_SHORT_DEL = 6
N_BOUNDARY_DEL = 3
N_LONG_DEL = 5
N_SHORT_INS = 6
N_LONG_INS = 5


def _build_variants(loci: dict[str, _Locus]) -> list[CorpusVariant]:
    variants: list[CorpusVariant] = []

    def add(  # pylint: disable=too-many-positional-arguments
        vid: str, locus: _Locus, pos: int, ref: str, alt: str,
        distance: int, category: str, expect: str, reason: str = "",
    ) -> None:
        variants.append(CorpusVariant(
            id=vid, contig=locus.local_chrom, pos=pos, ref=ref, alt=alt,
            distance=distance, category=category, expect=expect, reason=reason,
        ))

    for lname, locus in loci.items():
        canon = {g: _canonical(tms) for g, tms in locus.genes.items()}

        # ---- null (deep intronic) SNVs across every gene / distance --------
        null_pos: list[int] = []
        for tm in canon.values():
            null_pos.extend(_intron_positions(tm, locus))
        null_pos = sorted(set(null_pos))
        for i, pos in enumerate(_take(null_pos, N_NULL_D50)):
            add(f"{lname}_null50_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 50, "snv_null", "annotated")
        for i, pos in enumerate(_take(null_pos, N_NULL_D500)):
            add(f"{lname}_null500_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 500, "snv_null", "annotated")
        for i, pos in enumerate(_take(null_pos, N_NULL_D5000)):
            add(f"{lname}_null5000_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 5000, "snv_null", "annotated")

        # ---- splice-site SNVs ---------------------------------------------
        splice_pos: list[int] = []
        for tm in canon.values():
            splice_pos.extend(_splice_positions(tm, locus))
        splice_pos = sorted(set(splice_pos))
        for i, pos in enumerate(_take(splice_pos, N_SPLICE_D50)):
            add(f"{lname}_splice50_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 50, "snv_splice", "annotated")
        for i, pos in enumerate(_take(splice_pos, N_SPLICE_D500)):
            add(f"{lname}_splice500_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 500, "snv_splice", "annotated")

        # ---- deletions -----------------------------------------------------
        # short:    del_len (2) <= distance -> annotated; batch == sequential.
        # boundary: del_len (50) == distance -> the largest still-annotated
        #           deletion; batch == sequential must still hold exactly.
        # long:     del_len (89) > distance -> refused ("deletion longer than
        #           distance"): the batch padding path mis-reconstructs it
        #           (the sequential path matches Illumina SpliceAI, batch does
        #           not), so the annotator rejects it outright.
        del_anchor = _take(
            null_pos, N_SHORT_DEL + N_BOUNDARY_DEL + N_LONG_DEL)
        short_anchor = del_anchor[:N_SHORT_DEL]
        bound_anchor = del_anchor[N_SHORT_DEL:N_SHORT_DEL + N_BOUNDARY_DEL]
        long_anchor = del_anchor[N_SHORT_DEL + N_BOUNDARY_DEL:]
        for i, pos in enumerate(short_anchor):
            ref = locus.seq[pos - 1:pos - 1 + 3].upper()
            add(f"{lname}_del_short_{i}", locus, pos, ref, ref[0], 50,
                "del_short", "annotated")
        for i, pos in enumerate(bound_anchor):
            ref = locus.seq[pos - 1:pos - 1 + 51].upper()
            add(f"{lname}_del_boundary_{i}", locus, pos, ref, ref[0], 50,
                "del_boundary", "annotated")
        for i, pos in enumerate(long_anchor):
            ref = locus.seq[pos - 1:pos - 1 + 90].upper()
            add(f"{lname}_del_long_{i}", locus, pos, ref, ref[0], 50,
                "reject_del_too_long", "rejected",
                "deletion_longer_than_distance")

        # ---- insertions (short + long) ------------------------------------
        ins_anchor = _take(null_pos, N_SHORT_INS + N_LONG_INS)
        for i, pos in enumerate(ins_anchor[:N_SHORT_INS]):
            base = locus.base_at(pos)
            add(f"{lname}_ins_short_{i}", locus, pos, base, base + "CCCC", 50,
                "ins_short", "annotated")
        for i, pos in enumerate(ins_anchor[N_SHORT_INS:]):
            base = locus.base_at(pos)
            add(f"{lname}_ins_long_{i}", locus, pos, base, base + "CA" * 60, 50,
                "ins_long", "annotated")

        # ---- distance=0 : every variant auto-rejected (ref len > 0) -------
        for i, pos in enumerate(_take(null_pos, 4)):
            add(f"{lname}_dist0_{i}", locus, pos, locus.base_at(pos),
                locus.other_base(pos), 0, "snv_null", "rejected",
                "distance0_ref_too_long")

        # ---- rejections in _is_valid_annotatable (no model call) ----------
        rej_pos = _take(null_pos, 4)
        add(f"{lname}_rej_complex", locus, rej_pos[0],
            locus.seq[rej_pos[0] - 1:rej_pos[0] + 1].upper(), "GT", 50,
            "reject_complex", "rejected", "complex")
        add(f"{lname}_rej_reflong", locus, rej_pos[1],
            locus.seq[rej_pos[1] - 1:rej_pos[1] - 1 + 101].upper(),
            locus.base_at(rej_pos[1]), 50,
            "reject_ref_too_long", "rejected", "ref_too_long")
        add(f"{lname}_rej_altlong", locus, rej_pos[2],
            locus.base_at(rej_pos[2]),
            locus.base_at(rej_pos[2]) + "T" * 201, 50,
            "reject_alt_too_long", "rejected", "alt_too_long")
        add(f"{lname}_rej_strange", locus, rej_pos[3],
            locus.base_at(rej_pos[3]), "<DEL>", 50,
            "reject_strange_alt", "rejected", "strange_alt")

    # ---- wrong-reference rejection (col6a2, real transcript, full window) --
    col = loci["col6a2"]
    wr_pos = _take(_intron_positions(
        _canonical(col.genes["COL6A2"]), col), 2)
    for i, pos in enumerate(wr_pos):
        add(f"col6a2_rej_wrongref_{i}", col, pos, col.other_base(pos),
            col.base_at(pos), 50, "reject_wrong_ref", "rejected", "wrong_ref")

    return variants


# Local coordinates on the col6a2 contig for the crafted rejection genes.
MIXSTR_TX = (48000, 49000)
MIXSTR_VARIANT_POS = 48500


def _synthetic_transcripts(col: _Locus) -> list[TranscriptModel]:
    """Craft the two transcripts the pure-rejection axes need.

    * ``MIXSTR`` -- one gene, two transcripts on opposite strands, overlapping
      -> the annotator rejects the whole record (mixed strands).
    * ``EDGEGENE`` -- a transcript against the right end of the (last) contig,
      so a variant near the contig end has a truncated window ->
      "near chromosome end" rejection.
    """
    def noncoding(tr_id: str, strand: str, gene: str,
                  start: int, stop: int) -> TranscriptModel:
        return TranscriptModel(
            gene=gene, tr_id=tr_id, tr_name=tr_id, chrom=col.local_chrom,
            strand=strand, tx=(start, stop), cds=(start, start),
            exons=[Exon(start, stop, -1)],
            attributes={"gene_biotype": "unprocessed_pseudogene"},
        )

    edge_start = col.length - 6000
    edge_stop = col.length - 50
    return [
        noncoding("MIXSTR_plus", "+", "MIXSTR", *MIXSTR_TX),
        noncoding("MIXSTR_minus", "-", "MIXSTR", *MIXSTR_TX),
        noncoding("EDGEGENE_tx", "+", "EDGEGENE", edge_start, edge_stop),
    ]


def _synthetic_variants(col: _Locus) -> list[CorpusVariant]:
    variants = []
    # mixed-strand: full window, ref matches -> reaches the per-gene loop.
    variants.append(CorpusVariant(
        id="col6a2_reject_mixed_strand", contig=col.local_chrom,
        pos=MIXSTR_VARIANT_POS, ref=col.base_at(MIXSTR_VARIANT_POS),
        alt=col.other_base(MIXSTR_VARIANT_POS), distance=50,
        category="reject_mixed_strand", expect="rejected",
        reason="mixed_strands"))
    # near-chromosome-end: variant near the right end of the last contig so
    # pos + width//2 runs past EOF -> short sequence -> rejection.
    edge_pos = col.length - 200
    variants.append(CorpusVariant(
        id="col6a2_reject_near_end", contig=col.local_chrom,
        pos=edge_pos, ref=col.base_at(edge_pos),
        alt=col.other_base(edge_pos), distance=500,
        category="reject_near_end", expect="rejected",
        reason="near_chromosome_end"))
    return variants


def main() -> None:
    """Cut the corpus and write the fixture GRR + manifest."""
    grr = build_genomic_resource_repository(
        {"id": "real", "type": "directory", "directory": GRR_ROOT})
    gene_models = build_gene_models_from_resource(
        grr.get_resource(GENE_MODELS_ID))
    gene_models.load()
    genome = build_reference_genome_from_resource_id(GENOME_ID, grr)
    genome.open()

    by_gene: dict[tuple[str, str], list[TranscriptModel]] = defaultdict(list)
    for tm in gene_models.transcript_models.values():
        by_gene[tm.chrom, tm.gene].append(tm)

    loci: dict[str, _Locus] = {}
    for spec in LOCI:
        loci[spec["local_chrom"]] = _cut_locus(spec, genome, by_gene)

    variants = _build_variants(loci)

    # crafted transcripts + variants live on the last contig (col6a2).
    col = loci["col6a2"]
    synth_tms = _synthetic_transcripts(col)
    variants.extend(_synthetic_variants(col))

    # ---- emit the fixture GRR -----------------------------------------------
    # col6a2 MUST be the last contig so the near-end variant reads past EOF
    # (rather than bleeding into a following contig's bytes).
    contigs = {
        name: locus.seq
        for name, locus in loci.items() if name != "col6a2"
    }
    contigs["col6a2"] = col.seq
    write_genome_resource(GENOME_DIR, FASTA_FILENAME, contigs)

    all_transcripts: list[TranscriptModel] = []
    for name, locus in loci.items():
        if name == "col6a2":
            continue
        all_transcripts.extend(locus.transcripts)
    all_transcripts.extend(col.transcripts)
    all_transcripts.extend(synth_tms)
    fixture_gene_models = GeneModels(grr.get_resource(GENE_MODELS_ID))
    fixture_gene_models.transcript_models = {
        t.tr_id: t for t in all_transcripts}
    write_gene_models_resource(
        GENE_MODELS_DIR, GENE_MODELS_FILENAME, fixture_gene_models)

    # Persist each local contig's real hg38 origin so a downstream consumer
    # (e.g. the #321 integration tier) can de-rebase a corpus variant back to
    # real coordinates: real_pos = local_pos + window_start - 1 (the inverse of
    # rebase_pos). The fixture tier reads only manifest["variants"], so this
    # richer contigs schema is inert here.
    real_chrom_by_local = {spec["local_chrom"]: spec["chrom"] for spec in LOCI}
    manifest = {
        "genome_resource": "hg38/genome",
        "gene_models_resource": "hg38/gene_models",
        "distances": DISTANCES,
        "contigs": {
            name: {
                "length": len(seq),
                "real_chrom": real_chrom_by_local[name],
                "window_start": loci[name].window_start,
            }
            for name, seq in contigs.items()
        },
        "variants": [asdict(v) for v in variants],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    genome.close()
    n_model = sum(1 for v in variants if v.expect == "annotated")
    print(f"corpus: {len(variants)} variants "
          f"({n_model} model-call, {len(variants) - n_model} rejected)")
    print(f"contigs: { {n: len(s) for n, s in contigs.items()} }")
    fa_bytes = (GENOME_DIR / FASTA_FILENAME).stat().st_size
    print(f"fasta on-disk: {fa_bytes} bytes")


if __name__ == "__main__":
    main()
