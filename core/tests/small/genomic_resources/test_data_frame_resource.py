# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pandas as pd
import pytest
from gain.genomic_resources.data_frame_resource import (
    load_data_frame_from_resource,
    load_data_frame_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_inmemory_test_protocol,
    copy_proto_genomic_resources,
    s3_test_protocol,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from pandas.testing import assert_frame_equal

# The one table every format test authors, and the frame it must produce.
# Stated INDEPENDENTLY of the builder on purpose: the builder parses this
# same block with pandas to realize the xlsx, so asserting against a frame
# it handed back would be circular on exactly the separator and dtype axes
# these tests vary -- see gain#434.
DATA = """
    gene  score  count  label
    G1    0.25   10     alpha
    G2    0.50   20     beta
    G3    0.75   30     gamma
"""


def expected_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "gene": ["G1", "G2", "G3"],
        "score": [0.25, 0.50, 0.75],
        "count": [10, 20, 30],
        "label": ["alpha", "beta", "gamma"],
    })


@pytest.mark.parametrize("file_format", ["csv", "tsv", "excel"])
def test_round_trip_per_format(
    tmp_path: pathlib.Path, file_format: str,
) -> None:
    resource = (
        a_data_frame()
        .with_format(file_format)
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    assert_frame_equal(
        load_data_frame_from_resource(resource), expected_frame())


def test_tsv_is_tab_separated_not_a_single_column(
    tmp_path: pathlib.Path,
) -> None:
    # gain#434: csv and tsv shared one branch and neither passed ``sep``, so
    # a tab-separated file loaded as ONE column whose name was the entire
    # header line -- silently, with every value a string.
    resource = (
        a_data_frame()
        .with_format("tsv")
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    frame = load_data_frame_from_resource(resource)

    assert list(frame.columns) == ["gene", "score", "count", "label"]
    assert frame.shape == (3, 4)


def test_all_three_formats_carry_the_same_table(
    tmp_path: pathlib.Path,
) -> None:
    # No external oracle needed: the same authored block through three
    # formats must land on the same frame.
    base = a_data_frame().with_data(DATA)
    frames = {
        file_format: load_data_frame_from_resource(
            base.with_format(file_format).build_resource(
                tmp_path / file_format))
        for file_format in ("csv", "tsv", "excel")
    }

    assert_frame_equal(frames["csv"], frames["tsv"])
    assert_frame_equal(frames["csv"], frames["excel"])


def test_a_missing_format_key_defaults_to_csv(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_data_frame()
        .without_format_key()
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    assert_frame_equal(
        load_data_frame_from_resource(resource), expected_frame())


def test_an_explicit_sep_parameter_overrides_the_tsv_default(
    tmp_path: pathlib.Path,
) -> None:
    # The tsv separator is a DEFAULT, not a hard-coding: a config that
    # already spells out its own ``sep`` keeps winning.
    resource = (
        a_data_frame()
        .with_declared_format("tsv")
        .with_raw_content("gene;score\nG1;0.25\nG2;0.50\n")
        .with_parameters({"sep": ";"})
        .build_resource(tmp_path)
    )

    assert list(load_data_frame_from_resource(resource).columns) == [
        "gene", "score"]


def test_the_parameters_block_reaches_the_reader(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_data_frame()
        .with_raw_content(textwrap.dedent("""\
            # provenance: somewhere
            gene,score
            G1,0.25
            G2,NULL
            """))
        .with_parameters({"comment": "#", "na_values": ["NULL"]})
        .build_resource(tmp_path)
    )

    frame = load_data_frame_from_resource(resource)

    assert list(frame.columns) == ["gene", "score"]
    assert frame["score"].isna().tolist() == [False, True]


def test_loading_a_parameterless_resource_does_not_mutate_its_config(
    tmp_path: pathlib.Path,
) -> None:
    # ``config.get("parameters", {})`` hands back the resource's own cached
    # dict; applying the tsv separator default in place would leak into
    # every later load through the same resource object.
    resource = (
        a_data_frame()
        .with_format("tsv")
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    load_data_frame_from_resource(resource)

    assert resource.get_config().get("parameters", {}) == {}
    assert_frame_equal(
        load_data_frame_from_resource(resource), expected_frame())


def test_missing_resource_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing resource"):
        load_data_frame_from_resource(None)


def test_a_resource_of_another_type_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_position_score().build_resource(tmp_path)

    with pytest.raises(ValueError, match="wrong resource type"):
        load_data_frame_from_resource(resource)


def test_a_config_without_a_file_key_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_data_frame()
        .without_file_key()
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="missing file parameter"):
        load_data_frame_from_resource(resource)


def test_an_unknown_format_is_rejected(tmp_path: pathlib.Path) -> None:
    resource = (
        a_data_frame()
        .with_declared_format("parquet")
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="Unknown format parquet"):
        load_data_frame_from_resource(resource)


@pytest.mark.parametrize("parameters", [{"chunksize": 1}, {"iterator": True}])
def test_parameters_that_do_not_yield_a_frame_are_rejected(
    tmp_path: pathlib.Path, parameters: dict[str, object],
) -> None:
    # ``parameters`` is an unrestricted passthrough, and some of it changes
    # what pandas returns.  The loader promises a DataFrame, so it says so
    # here rather than handing a TextFileReader to the caller.
    resource = (
        a_data_frame()
        .with_data(DATA)
        .with_parameters(parameters)
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="not a data frame"):
        load_data_frame_from_resource(resource)


def test_load_from_resource_id_uses_the_passed_repository(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource("tables/genes", a_data_frame().with_data(DATA))
        .build_repo(tmp_path)
    )

    assert_frame_equal(
        load_data_frame_from_resource_id("tables/genes", grr),
        expected_frame())


@pytest.mark.grr_rw
def test_the_data_file_resolves_through_the_resource_not_the_cwd(
    tmp_path: pathlib.Path,
    grr_scheme: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The regression guard for the bug fixed by hand in 86e78a1c8: the
    # loader used to hand the bare ``file:`` value to pandas, resolving it
    # against the process working directory instead of the resource.  Run
    # from a directory where no such file exists, across every writable
    # protocol at once.
    content = {
        "tables/genes": {
            "genomic_resource.yaml":
                "type: data_frame\nfile: data.csv\nformat: csv\n",
            "data.csv": "gene,score\nG1,0.25\nG2,0.50\n",
        },
    }

    root = tmp_path / "grr"
    setup_directories(root, content)

    if grr_scheme == "inmemory":
        proto = build_inmemory_test_protocol(content=content)
    elif grr_scheme == "s3":
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
        proto = s3_test_protocol()
        copy_proto_genomic_resources(
            proto, build_filesystem_test_protocol(root))
    else:
        proto = build_filesystem_test_protocol(root)
    repo = GenomicResourceProtocolRepo(proto)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(elsewhere)

    frame = load_data_frame_from_resource(repo.get_resource("tables/genes"))

    assert list(frame.columns) == ["gene", "score"]
    assert frame["score"].tolist() == [0.25, 0.50]
