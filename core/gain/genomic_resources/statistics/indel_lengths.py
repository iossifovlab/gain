"""The indel groups' names for the exact-lengths record.

An allele score's insertions and deletions are stored as
:class:`~gain.genomic_resources.statistics.exact_lengths.ExactLengths`
records -- an exact length map clamped at the shared clamp, plus four
scalars -- and read back through that module's tally, merge, ladder and
row rules.  Nothing about those rules is indel-specific; this module
only spells them the way the allele statistics and their tests do.

Every name here is an alias, not a subclass or a wrapper: an
``IndelLengths`` IS an ``ExactLengths``, and a file written through
either spelling is the same file.
"""
from gain.genomic_resources.statistics.exact_lengths import (
    LENGTH_MAP_CLAMP,
    NO_LENGTHS,
    ExactLengths,
    LengthStatisticsRow,
    LengthTally,
    length_ladder,
    merged_lengths,
    merged_tallies,
)

#: The shared clamp under its indel-era name.
INDEL_LENGTH_CLAMP = LENGTH_MAP_CLAMP
IndelLengths = ExactLengths
NO_INDELS = NO_LENGTHS
IndelTally = LengthTally
IndelStatisticsRow = LengthStatisticsRow
indel_length_ladder = length_ladder
merged_indels = merged_lengths

__all__ = [
    "INDEL_LENGTH_CLAMP",
    "NO_INDELS",
    "IndelLengths",
    "IndelStatisticsRow",
    "IndelTally",
    "indel_length_ladder",
    "merged_indels",
    "merged_tallies",
]
