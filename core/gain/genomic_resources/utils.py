

from collections.abc import Callable
from typing import Any

from gain import logging
from gain.genomic_resources.repository import GenomicResource
from gain.utils.log_safety import escape_unsafe_characters

logger = logging.getLogger(__name__)


def read_resource_id_label(
    resource: GenomicResource, label: str,
) -> str | None:
    """The named label's value, when that label names another resource.

    Read through the accessor that narrows both ``meta`` levels, the way
    every other label reader does (gain#654, gain#1004) -- and then
    narrowed once more, because that accessor promises a *mapping* and
    says nothing about what is in it (gain#1050).  A value that cannot
    be a resource id reads as absent and is reported.  An absent label,
    and the explicit YAML null the production GRRs carry, are not
    curator mistakes and stay silent.

    Taken by label name rather than hard-wired to ``reference_genome``,
    because four labels across three modules name a resource this way:
    ``reference_genome`` on gene models (gain#1050) and on scores, and
    ``source_genome``/``target_genome`` on a liftover chain.  All four
    read a free-form YAML value into a ``str | None``, and unnarrowed
    all four died the same way -- the int in a regex, the list and the
    dict wherever the id was first hashed -- with a ``TypeError`` that
    named neither the resource nor the label (gain#1053).

    Lives here rather than beside any one of its callers, and beside
    :func:`build_chrom_mapping`, which is the same kind of thing: a read
    of a resource's own configuration that several resource types share.

    Whitespace is deliberately NOT stripped: only an empty value is
    narrowed away, so a padded id or the trailing newline a folded
    scalar leaves still reaches resolution and fails there naming
    itself.  Stripping would be a normalization policy for ids rather
    than a narrowing, and that is a separate decision.
    """
    value = resource.get_labels().get(label)
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    reason = "empty" if isinstance(value, str) \
        else f"a {type(value).__name__}, not a string"
    logger.warning(
        "resource <%s>: meta.labels.%s is %s; reading it as absent -- "
        "fix the resource's 'genomic_resource.yaml'",
        escape_unsafe_characters(resource.resource_id), label, reason)
    return None


def build_chrom_mapping(
    resource: GenomicResource | None,
    config: dict[str, Any] | None = None,
) -> Callable[[str], str | None] | None:
    """Build chromosome mapping function from resource config.

    The resource config may contain `chrom_mapping` section with
    `filename`, `add_prefix` and `del_prefix` keys. The `filename` points
    to a file with two columns: original chromosome names and mapped names.

    These keys are mutually exclusive, only one of them may be present.

    Args:
        resource: genomic resource with config
    Returns:
        function that maps chromosome names or None if no mapping is defined
    """
    if config is None:
        if resource is None:
            raise ValueError("Either resource or config must be provided")
        config = resource.get_config()
    chrom_mapping_config = config.get("chrom_mapping")
    if chrom_mapping_config is None:
        return None

    filename = chrom_mapping_config.get("filename")

    if filename is not None:
        assert chrom_mapping_config.get("add_prefix") is None
        assert chrom_mapping_config.get("del_prefix") is None
        assert chrom_mapping_config.get("mapping") is None

        if resource is None:
            raise ValueError(
                "Resource must be provided when filename is used")

        mapping = {}
        with resource.open_raw_file(filename) as f:
            for line in f:
                original, mapped = line.strip().split("\t")[:2]
                mapping[original] = mapped

        def map_chromosome(chrom: str) -> str | None:
            return mapping.get(chrom)

        return map_chromosome

    add_prefix = chrom_mapping_config.get("add_prefix")
    if add_prefix:
        assert chrom_mapping_config.get("del_prefix") is None
        assert chrom_mapping_config.get("mapping") is None

        def add_prefix_func(chrom: str) -> str:
            return f"{add_prefix}{chrom}"

        return add_prefix_func

    del_prefix = chrom_mapping_config.get("del_prefix")
    if del_prefix:
        assert chrom_mapping_config.get("mapping") is None

        def del_prefix_func(chrom: str) -> str:
            if chrom.startswith(del_prefix):
                return chrom[len(del_prefix):]
            return chrom
        return del_prefix_func

    mapping = chrom_mapping_config.get("mapping")
    if mapping:
        def chrom_mapping(chrom: str) -> str | None:
            return mapping.get(chrom)

        return chrom_mapping

    raise ValueError(
        f"Invalid chrom_mapping configuration: "
        f"{resource}; {chrom_mapping_config}")
