"""Pin the offline claims of the "Data frames and AnnData" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/data_frames_and_anndata.rst``. The
examples of the page use placeholder resource ids; mini-GRR holds no
resource of either type, so the tests build them with ``a_data_frame``
and ``an_ann_data``. The default AnnData has the three cells and four
genes of the page's illustration. The tests pin these claims:

- Both data frame loaders return a pandas ``DataFrame`` with no
  ``open()`` or ``load()``.
- ``format:`` selects ``csv`` (the default), ``tsv`` or ``excel``, a TSV
  file is read with a tab separator, and a ``.gz`` file is decompressed.
- ``parameters:`` that make pandas return another type raise a
  ``ValueError`` that names the resource.
- Both AnnData loaders return an ``AnnData`` with ``X``, ``obs`` and
  ``var``, the rows of ``obs`` are cells and those of ``var`` are genes.
- A resource that is not reachable as a local file raises a
  ``ValueError`` that names the resource.
- Without ``format:``, the file name selects ``h5ad``, ``10x_mtx`` or
  ``10x_h5``, and ``h5ad`` is the fallback.
- ``matrix_free=True`` gives a matrix of zeros for the 10x formats and is
  ignored for ``h5ad``.
- An ``h5ad`` AnnData is backed by an open file, and the 10x formats are
  read into memory.
"""
import gzip
import pathlib
from typing import Any, cast

import anndata as ad
import pandas as pd
import pytest
from gain import grr
from gain.genomic_resources.ann_data_resource import resolve_ann_data_format
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_inmemory_test_resource
from gain.genomic_resources.testing.ann_data_builder import an_ann_data
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame

_TABLE = """
    gene  score  label
    G1    0.1    alpha
    G2    0.2    beta
    G3    0.3    gamma
"""


@pytest.fixture(name="frames_repo")
def fixture_frames_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """Data frame resources in the three formats, and a gzipped one."""
    gzipped = gzip.compress(b"gene,score\nG1,0.1\nG2,0.2\n")
    return a_grr() \
        .with_resource("csv", a_data_frame().with_data(_TABLE)) \
        .with_resource(
            "default", a_data_frame().with_data(_TABLE).without_format_key()) \
        .with_resource("tsv", a_data_frame().with_data(_TABLE)
                       .with_format("tsv")) \
        .with_resource("excel", a_data_frame().with_data(_TABLE)
                       .with_format("excel")) \
        .with_resource("gz", a_data_frame().with_raw_content(gzipped)
                       .with_file("data.csv.gz")) \
        .with_resource("chunked", a_data_frame().with_data(_TABLE)
                       .with_parameters({"chunksize": 1})) \
        .build_repo(tmp_path)


@pytest.fixture(name="ann_repo")
def fixture_ann_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """AnnData resources in the three formats."""
    return a_grr() \
        .with_resource("h5ad", an_ann_data()) \
        .with_resource("mtx", an_ann_data().with_format("10x_mtx")) \
        .with_resource("h5", an_ann_data().with_format("10x_h5")) \
        .build_repo(tmp_path)


def test_both_loaders_return_a_data_frame(
    frames_repo: GenomicResourceRepo,
) -> None:
    """The loaders read the table at once, with no ``open()``."""
    from_id = grr.load_data_frame_from_resource_id("csv", frames_repo)
    from_resource = grr.load_data_frame_from_resource(
        frames_repo.get_resource("csv"))

    for frame in (from_id, from_resource):
        assert isinstance(frame, pd.DataFrame)
        assert frame.shape == (3, 3)


@pytest.mark.parametrize("resource_id", ["csv", "default", "tsv", "excel"])
def test_format_selects_the_reader(
    frames_repo: GenomicResourceRepo, resource_id: str,
) -> None:
    """Each format, and the ``csv`` default, reads the same table."""
    frame = grr.load_data_frame_from_resource_id(resource_id, frames_repo)

    assert list(frame.columns) == ["gene", "score", "label"]
    assert frame["gene"].tolist() == ["G1", "G2", "G3"]


def test_gz_file_is_decompressed(frames_repo: GenomicResourceRepo) -> None:
    """A file that ends in ``.gz`` is read with gzip compression."""
    frame = grr.load_data_frame_from_resource_id("gz", frames_repo)

    assert frame["gene"].tolist() == ["G1", "G2"]


def test_parameters_that_change_the_return_type_raise(
    frames_repo: GenomicResourceRepo,
) -> None:
    """``chunksize`` makes pandas return a reader, so the loader refuses."""
    with pytest.raises(ValueError, match="chunked"):
        grr.load_data_frame_from_resource_id("chunked", frames_repo)


def test_both_loaders_return_an_ann_data(
    ann_repo: GenomicResourceRepo,
) -> None:
    """``obs`` rows are the cells, ``var`` rows are the genes."""
    from_id = grr.load_ann_data_from_resource_id("mtx", ann_repo)
    from_resource = grr.load_ann_data_from_resource(
        ann_repo.get_resource("mtx"))

    for ann_data in (from_id, from_resource):
        assert isinstance(ann_data, ad.AnnData)
        assert ann_data.shape == (3, 4)
        assert ann_data.obs.index.tolist() == ["CELL_1", "CELL_2", "CELL_3"]
        assert len(ann_data.var) == 4
        assert cast(Any, ann_data.X).shape == (3, 4)


def test_ann_data_needs_a_local_file() -> None:
    """A resource on a ``memory://`` protocol raises ``ValueError``."""
    resource = build_inmemory_test_resource({
        "genomic_resource.yaml": "type: ann_data\nfile: data.h5ad\n",
        "data.h5ad": "not read",
    })

    with pytest.raises(ValueError, match="cannot load the url"):
        grr.load_ann_data_from_resource(resource)


@pytest.mark.parametrize(("file_name", "file_format"), [
    ("cells.h5ad", "h5ad"),
    ("matrix.mtx", "10x_mtx"),
    ("sample_matrix.mtx.gz", "10x_mtx"),
    ("matrix.h5", "10x_h5"),
    ("cells.dat", "h5ad"),
])
def test_file_name_selects_the_format(
    file_name: str, file_format: str,
) -> None:
    """Without ``format:``, the suffix decides, and ``h5ad`` is the rest."""
    assert resolve_ann_data_format({"file": file_name}) == file_format


@pytest.mark.parametrize("resource_id", ["mtx", "h5"])
def test_matrix_free_gives_zeros_for_10x(
    ann_repo: GenomicResourceRepo, resource_id: str,
) -> None:
    """The annotations stay and the matrix holds no data."""
    ann_data = grr.load_ann_data_from_resource(
        ann_repo.get_resource(resource_id), matrix_free=True)

    assert ann_data.shape == (3, 4)
    assert cast(Any, ann_data.X).nnz == 0
    assert ann_data.obs.index.tolist() == ["CELL_1", "CELL_2", "CELL_3"]


def test_matrix_free_is_ignored_for_h5ad(
    ann_repo: GenomicResourceRepo,
) -> None:
    """An ``h5ad`` AnnData stays backed by its file."""
    ann_data = grr.load_ann_data_from_resource(
        ann_repo.get_resource("h5ad"), matrix_free=True)

    assert ann_data.isbacked
    ann_data.file.close()


def test_h5ad_is_backed_and_the_caller_closes_it(
    ann_repo: GenomicResourceRepo,
) -> None:
    """The page's ``if ann_data.isbacked`` block closes the file."""
    ann_data = grr.load_ann_data_from_resource_id("h5ad", ann_repo)

    assert ann_data.isbacked
    ann_data.file.close()
    assert not ann_data.file.is_open


@pytest.mark.parametrize("resource_id", ["mtx", "h5"])
def test_10x_formats_are_read_into_memory(
    ann_repo: GenomicResourceRepo, resource_id: str,
) -> None:
    """The 10x formats hold no open file."""
    ann_data = grr.load_ann_data_from_resource_id(resource_id, ann_repo)

    assert not ann_data.isbacked
