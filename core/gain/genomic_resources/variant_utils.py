import itertools

from gain import logging
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
)

logger = logging.getLogger(__name__)


def normalize_variant(
    chrom: str,
    pos: int,
    ref: str,
    alts: list[str],
    genome: ReferenceGenome,
) -> tuple[str, int, str, list[str]]:
    """Normalize a variant.

    Using algorithm defined in
    the https://genome.sph.umich.edu/wiki/Variant_Normalization
    """

    while True:
        ref, alts, shrunk = _shrink_right(chrom, pos, ref, alts)
        pos, ref, alts, extended = _extend_left(chrom, pos, ref, alts, genome)
        if not (shrunk or extended):
            break

    pos, ref, alts = _trim_left(pos, ref, alts)
    return chrom, pos, ref, alts


def _shrink_right(
    chrom: str, pos: int, ref: str, alts: list[str],
) -> tuple[str, list[str], bool]:
    """Drop the last base when the ref and every alt share it.

    The no-variant case (``ref`` equal to every alt, length 1) does not
    shrink.
    """
    if len(ref) > 0 and all(len(alt) > 0
            and ref[-1] == alt[-1] for alt in alts):
        logger.debug(
            "shrink from right: %s:%d %s>%s", chrom, pos, ref, alts)
        if all(ref == alt for alt in alts) and len(ref) == 1:
            logger.info(
                "no variant: %s:%d %s>%s", chrom, pos, ref, alts)
        else:
            return ref[:-1], [alt[:-1] for alt in alts], True
    return ref, alts, False


def _extend_left(
    chrom: str, pos: int, ref: str, alts: list[str],
    genome: ReferenceGenome,
) -> tuple[int, str, list[str], bool]:
    """Prepend the reference base on the left when an allele is empty."""
    if pos > 1 and (
            len(ref) == 0 or
            any(len(alt) == 0 for alt in alts)):
        logger.debug(
            "moving left variant: %s:%d %s>%s", chrom, pos, ref, alts)
        left = genome.get_sequence(
            chrom, pos - 1, pos - 1)
        return pos - 1, f"{left}{ref}", [f"{left}{alt}" for alt in alts], True
    return pos, ref, alts, False


def _trim_left(
    pos: int, ref: str, alts: list[str],
) -> tuple[int, str, list[str]]:
    """Drop the shared first bases while every allele keeps two or more."""
    while len(ref) >= 2 and all(len(alt) >= 2
            and ref[0] == alt[0] for alt in alts):
        pos += 1
        ref = ref[1:]
        alts = [alt[1:] for alt in alts]
    return pos, ref, alts


def _is_novariant(ref: str, alts: list[str]) -> bool:
    """Return True when every alt equals the one-base ref."""
    return len(ref) == 1 and all(alt == ref for alt in alts)


def _has_ambiguous_pair(ref: str, alts: list[str]) -> bool:
    """Check if a neighbouring pair of alleles is a prefix or suffix pair.

    Each pair of neighbours in ``[ref, *alts]`` is checked. The pair is
    ambiguous when the shorter allele is a prefix or a suffix of the
    longer one.
    """
    for (s1, s2) in itertools.pairwise([ref, *alts]):
        short, long = (s2, s1) if len(s1) > len(s2) else (s1, s2)
        if long.startswith(short) or long.endswith(short):
            return True
    return False


def maximally_extend_variant(
    chrom: str,
    pos: int,
    ref: str,
    alts: list[str],
    genome: ReferenceGenome,
) -> tuple[str, int, str, list[str]]:
    """Maximally extend a variant.

    A no-variant (every alt equal to the one-base ref after normalization)
    comes back as normalized, with no extension.
    """
    chrom, pos, ref, alts = normalize_variant(chrom, pos, ref, alts, genome)
    if _is_novariant(ref, alts):
        return chrom, pos, ref, alts
    if not all(alt[0] == ref[0] for alt in alts):
        left = genome.get_sequence(chrom, pos - 1, pos - 1)
        pos -= 1
        ref = f"{left}{ref}"
        alts = [f"{left}{alt}" for alt in alts]
    if not all(alt[-1] == ref[-1] for alt in alts):
        right = genome.get_sequence(chrom, pos + len(ref), pos + len(ref))
        ref = f"{ref}{right}"
        alts = [f"{alt}{right}" for alt in alts]
    while _has_ambiguous_pair(ref, alts):
        right = genome.get_sequence(chrom, pos + len(ref), pos + len(ref))
        if not right:
            break
        ref = f"{ref}{right}"
        alts = [f"{alt}{right}" for alt in alts]
    return chrom, pos, ref, alts
