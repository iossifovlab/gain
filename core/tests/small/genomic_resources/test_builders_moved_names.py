"""The names that left ``builders`` still import from it, with a warning.

The table, bigWig, VCF INFO, gene and reference genome score builders
moved out of ``gain.genomic_resources.testing.builders`` to sibling
modules, each with its factory (gain#2270). gpf imports ``a_gene_score``
from the old path, so each public moved name stays importable from the old
module for one release and warns there. Private names get no alias.
"""
import ast
import importlib
import pathlib
import warnings

import gain
import pytest
from gain.genomic_resources.testing import builders

PKG = "gain.genomic_resources.testing"
TABLE = f"{PKG}.table_score_builder"
BIGWIG = f"{PKG}.bigwig_score_builder"
VCF = f"{PKG}.vcf_info_score_builder"
GENE = f"{PKG}.gene_score_builder"
GENOME = f"{PKG}.reference_genome_builder"
HELPERS = f"{PKG}.score_builder_helpers"

ALIASED = [
    *((name, TABLE) for name in (
        "AlleleScoreBuilder",
        "FragmentScoreBuilder",
        "PositionScoreBuilder",
        "a_fragment_score",
        "a_position_score",
        "an_allele_score",
    )),
    ("BigWigScoreBuilder", BIGWIG),
    ("a_bigwig_score", BIGWIG),
    ("VcfInfoScoreBuilder", VCF),
    ("a_vcf_info_score", VCF),
    ("GeneScoreBuilder", GENE),
    ("a_gene_score", GENE),
    ("ReferenceGenomeBuilder", GENOME),
    ("a_reference_genome", GENOME),
]

#: Every name that moved, the private ones included, with its new module.
#: No gain code imports any of them from the old module.
MOVED = {
    **dict(ALIASED),
    **dict.fromkeys((
        "_ALLELE_DEFAULT_DATA",
        "_CHROM_MAPPING_FILENAME",
        "_ColumnAddress",
        "_HEADER_MODES",
        "_POSITION_COLUMNS",
        "_REF_ALT_TABLE_CONFIG",
        "_TABIX_FILENAME",
        "_TableScoreBuilder",
        "_realize_tabix_table",
    ), TABLE),
    **dict.fromkeys((
        "_BIGWIG_DEFAULT_CHROM_LENS",
        "_BIGWIG_DEFAULT_DATA",
        "_BIGWIG_FILENAME",
        "_normalize_bedgraph",
    ), BIGWIG),
    **dict.fromkeys((
        "_VCF_DEFAULT_DATA",
        "_VCF_FILENAME",
        "_VCF_HEADER_INDEX_FILENAME",
    ), VCF),
    **dict.fromkeys((
        "_DEFAULT_GENE_COLUMN",
        "_GENE_DATA_FILENAME",
        "_build_gene_score_content",
        "_effective_gene_data",
    ), GENE),
    **dict.fromkeys((
        "_GENOME_BASENAME",
        "_MINIMAL_GENOME_SEQUENCE",
        "_synthesize_fasta",
    ), GENOME),
    **dict.fromkeys((
        "_comment_header",
        "_parse_header",
        "_resolve_column_names",
        "_strip_header",
        "_validate_data_header",
        "_validate_score_specs",
    ), HELPERS),
}

#: The new modules, which ``builders`` must not import.
NEW_MODULES = sorted(set(MOVED.values()))


def test_the_alias_map_names_exactly_the_public_moved_names() -> None:
    aliases = {
        name: f"{builders.__package__}.{module}"
        for name, module in builders._MOVED.items()}

    assert aliases == dict(ALIASED)


@pytest.mark.parametrize(("name", "new_module"), sorted(MOVED.items()))
def test_each_moved_name_is_defined_in_its_new_module(
    name: str, new_module: str,
) -> None:
    source = importlib.import_module(new_module).__file__
    assert source is not None
    tree = ast.parse(pathlib.Path(source).read_text(encoding="utf-8"))

    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(
                target.id for target in node.targets
                if isinstance(target, ast.Name))

    assert name in defined


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_old_path_gives_the_moved_object_and_warns(
    name: str, new_module: str,
) -> None:
    moved = getattr(importlib.import_module(new_module), name)

    with pytest.warns(DeprecationWarning, match=new_module) as record:
        aliased = getattr(builders, name)

    assert aliased is moved
    assert [str(warning.message) for warning in record] == [
        f"{name} moved to {new_module}"]


def test_from_import_of_a_moved_name_warns() -> None:
    gene = importlib.import_module(GENE)

    with pytest.warns(DeprecationWarning, match=f"moved to {GENE}"):
        # The import statement is the seam that gpf uses.
        from gain.genomic_resources.testing.builders import (
            a_gene_score,
        )

    assert a_gene_score is gene.a_gene_score


@pytest.mark.parametrize(
    "name", ["a_grr", "a_basic_resource", "GRRBuilder", "_DATA_FILENAME"])
def test_a_name_that_stayed_does_not_warn(name: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        value = getattr(builders, name)

    assert value is not None


@pytest.mark.parametrize(
    "name", sorted(name for name in MOVED if name.startswith("_")))
def test_a_private_moved_name_is_gone_from_the_old_module(name: str) -> None:
    with pytest.raises(AttributeError):
        getattr(builders, name)


def test_an_unknown_name_raises_attribute_error() -> None:
    unknown = "no_such_name"

    with pytest.raises(AttributeError):
        getattr(builders, unknown)


def _from_module(node: ast.ImportFrom, own_package: list[str]) -> str:
    """Resolve the module of a from-import against the file's package."""
    module = node.module or ""
    if not node.level:
        return module
    base = own_package[:len(own_package) - node.level + 1]
    return ".".join([*base, module] if module else base)


def _imported_modules(tree: ast.Module) -> set[str]:
    """List the modules that ``builders`` imports, by absolute name."""
    own_package = builders.__package__.split(".")
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _from_module(node, own_package)
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


def test_builders_imports_none_of_the_new_modules() -> None:
    assert builders.__file__ is not None
    tree = ast.parse(
        pathlib.Path(builders.__file__).read_text(encoding="utf-8"))

    imported = _imported_modules(tree)

    assert sorted(imported.intersection(NEW_MODULES)) == []


#: The workspace members of gain, relative to the workspace root.
MEMBERS = [
    "core",
    "demo_annotator",
    "spliceai_annotator",
    "vep_annotator",
    "web_api",
]


def moved_name_imports(root: pathlib.Path, package: str = "") -> list[str]:
    """List each import of a moved name from the old module under ``root``.

    ``package`` is the dotted name of ``root``, empty when ``root`` holds
    top-level packages. A relative import resolves against the package of
    its file. Each entry is ``<file>: <name>``.
    """
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root)
        own_package = [
            *([package] if package else []), *rel.with_suffix("").parts,
        ][:-1]
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if _from_module(node, own_package) != builders.__name__:
                continue
            offenders.extend(
                f"{rel.as_posix()}: {alias.name}"
                for alias in node.names if alias.name in MOVED)
    return offenders


def _plant(root: pathlib.Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_fence_finds_absolute_and_relative_imports(
    tmp_path: pathlib.Path,
) -> None:
    testing = "genomic_resources/testing"
    _plant(tmp_path, f"{testing}/absolute.py",
           f"from {PKG}.builders import a_gene_score\n")
    _plant(tmp_path, f"{testing}/relative.py",
           "from .builders import _strip_header, a_grr\n")
    _plant(tmp_path, f"{testing}/new_path.py",
           "from .gene_score_builder import a_gene_score\n")

    offenders = moved_name_imports(tmp_path, "gain")

    assert offenders == [
        f"{testing}/absolute.py: a_gene_score",
        f"{testing}/relative.py: _strip_header",
    ]


def gain_workspace(gain_file: pathlib.Path) -> pathlib.Path | None:
    """Return the workspace root of a source checkout of gain.

    Return ``None`` when gain is an installed package, for example in the
    conda integration test bed. The workspace members are then absent.
    """
    workspace = gain_file.parent.parent.parent
    if (workspace / "core" / "gain").resolve() != gain_file.parent.resolve():
        return None
    return workspace


@pytest.mark.parametrize("member", MEMBERS)
def test_nothing_in_gain_imports_a_moved_name_from_the_old_module(
    member: str,
) -> None:
    workspace = gain_workspace(pathlib.Path(gain.__file__))
    if workspace is None:
        pytest.skip("gain is installed, not a source checkout")
    root = workspace / member
    assert root.is_dir(), f"no workspace member {member}"

    # This file imports through the alias on purpose.
    this_file = pathlib.Path(__file__).resolve().relative_to(workspace)
    offenders = [
        offender for offender in moved_name_imports(root)
        if not f"{member}/{offender}".startswith(
            f"{this_file.as_posix()}: ")]

    assert offenders == [], (
        f"import these names from their new modules: {offenders}")
