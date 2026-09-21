"""The stored chromosome lengths of a score, at the repair seam (gain#1576).

A repair writes ``statistics/chrom_lengths.json`` -- every source's
answer per contig, one block per source, plus what the file was derived
from -- under a freshness gate of its own, beside the statistics hash
rather than inside it: re-pointing the ``reference_genome`` label
refreshes this one file and never the histograms (``docs/source/grr.rst``
promises as much).
"""

import json
import logging
import pathlib
from typing import Any, cast

import pytest
import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
)

from .conftest import md5_of
from .test_genomic_scores_impl_chrom_lengths import (
    BIGWIG_GENOME_CHR1_LENGTH,
    BIGWIG_HEADER_LENGTHS,
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    a_labelled_bigwig_score_grr,
    a_labelled_tabix_score_grr,
    patch_tabix_probe,
    set_label,
)


def _stored(statistics: pathlib.Path) -> dict[str, Any]:
    """The lengths file as written; raises when it is not JSON."""
    return cast(
        dict[str, Any],
        json.loads((statistics / "chrom_lengths.json").read_text()))


def resource_stats(
    tmp_path: pathlib.Path, resource_id: str, *args: str,
) -> None:
    """``grr_manage resource-stats`` on one resource, single-threaded."""
    cli_manage([
        "resource-stats", "-r", resource_id, "-R", str(tmp_path), "-j", "1",
        *args])


def resync_the_manifest(tmp_path: pathlib.Path, resource_id: str) -> None:
    """Bring the manifest up to date with the files as they are now.

    Deleting the lengths file, or editing the config, would otherwise
    make the manifest pass that opens every run report the resource --
    and a dry run counts that too, gate or no gate.  With the manifest
    current, only the lengths gate can count it.
    """
    cli_manage([
        "resource-manifest", "-r", resource_id, "-R", str(tmp_path)])


def repair_the_labelled_score(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the labelled tabix score ``score`` and repair it; returns
    its statistics directory."""
    a_labelled_tabix_score_grr().build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return tmp_path / "score" / "statistics"


def test_repair_stores_every_sources_answer_and_what_they_derive_from(
    tmp_path: pathlib.Path,
) -> None:
    """The genome lists chr1 and not chrM: chr1 is in both blocks, chrM
    only in the table's."""
    score_dir = tmp_path / "score"

    statistics = repair_the_labelled_score(tmp_path)

    assert _stored(statistics) == {
        "format": 1,
        "derived_from": {
            "reference_genome": "genome",
            "files_md5": {
                "data.txt.gz": md5_of((score_dir / "data.txt.gz").read_bytes()),
                "data.txt.gz.tbi": md5_of(
                    (score_dir / "data.txt.gz.tbi").read_bytes()),
            },
        },
        "table_source": "tabix_estimate",
        "sources": {
            "reference_genome": {"chr1": CHR1_GENOME_LENGTH},
            "tabix_estimate": {
                "chr1": CHR1_PROBE_BOUND,
                "chrM": CHRM_PROBE_BOUND,
            },
        },
    }


def test_a_bigwig_score_stores_its_headers_lengths_in_one_block(
    tmp_path: pathlib.Path,
) -> None:
    """Unlabelled, the header is the only source the score has."""
    (
        a_grr()
        .with_resource(
            "score",
            a_bigwig_score()
            .with_data("""
                chr1  10  20  0.1
                chr2  10  20  0.2
            """)
            .with_chrom_lens({"chr1": 100, "chr2": 200}))
        .build_repo(tmp_path)
    )

    resource_stats(tmp_path, "score")

    assert _stored(tmp_path / "score" / "statistics")["sources"] == {
        "bigwig": {"chr1": 100, "chr2": 200},
    }


def test_a_labelled_bigwig_score_stores_the_genomes_and_the_headers(
    tmp_path: pathlib.Path,
) -> None:
    """Two exact sources; the genome lists chr1 only, the header both."""
    a_labelled_bigwig_score_grr().build_repo(tmp_path)

    resource_stats(tmp_path, "score")

    assert _stored(tmp_path / "score" / "statistics")["sources"] == {
        "bigwig": BIGWIG_HEADER_LENGTHS,
        "reference_genome": {"chr1": BIGWIG_GENOME_CHR1_LENGTH},
    }


def test_a_full_build_manifests_the_lengths_file(
    tmp_path: pathlib.Path,
) -> None:
    repair_the_labelled_score(tmp_path)

    manifest = (tmp_path / "score" / ".MANIFEST").read_text()
    assert "statistics/chrom_lengths.json" in manifest


def _mtimes(statistics: pathlib.Path) -> dict[pathlib.Path, int]:
    return {path: path.stat().st_mtime_ns for path in statistics.iterdir()}


def test_repointing_the_label_rewrites_the_lengths_file_alone(
    tmp_path: pathlib.Path,
) -> None:
    """Adding or re-pointing the label needs no data rebuild -- the
    promise ``grr.rst`` makes.  The hash never learns about the label;
    the lengths file's own gate is what notices it."""
    statistics = repair_the_labelled_score(tmp_path)
    lengths_file = statistics / "chrom_lengths.json"
    before = _mtimes(statistics)
    set_label(tmp_path, "score", "reference_genome", "other_genome")

    resource_stats(tmp_path, "score")

    after = _mtimes(statistics)
    assert after.pop(lengths_file) > before.pop(lengths_file)
    assert after == before
    assert _stored(statistics)["derived_from"]["reference_genome"] == \
        "other_genome"


def test_an_unchanged_score_has_nothing_rewritten_and_no_table_opened(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The lengths gate holds too: the same key on both sides, compared
    without opening the score."""
    statistics = repair_the_labelled_score(tmp_path)
    before = _mtimes(statistics)
    assert statistics / "chrom_lengths.json" in before
    opened = mocker.spy(GenomicScore, "open")

    resource_stats(tmp_path, "score")

    opened.assert_not_called()
    assert _mtimes(statistics) == before


def test_a_dry_run_never_runs_the_tabix_probe_to_check_the_lengths_file(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The gate compares keys; it derives nothing.  On a tabix score the
    probe is the expensive rung the stored file exists to spare."""
    repair_the_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    resync_the_manifest(tmp_path, "score")
    probe = patch_tabix_probe(mocker)

    with pytest.raises(SystemExit) as exit_status:
        resource_stats(tmp_path, "score", "--dry-run")

    # The status is the COUNT of resources needing an update (gain#364).
    assert exit_status.value.code == 1
    probe.assert_not_called()


def test_a_table_file_missing_without_a_sidecar_still_fails_the_resource(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing says the bytes exist anywhere; the lengths gate has no
    say, and the resource fails as any read of it would."""
    repair_the_labelled_score(tmp_path)
    (tmp_path / "score" / "data.txt.gz").unlink()

    with caplog.at_level(logging.ERROR), \
            pytest.raises(SystemExit) as exit_status:
        resource_stats(tmp_path, "score")

    assert exit_status.value.code == 1
    assert "failed resources" in caplog.text
    assert "score" in caplog.text


@pytest.mark.parametrize("content", [
    pytest.param(b"", id="truncated"),
    pytest.param(b"\xff\xfe{", id="not-utf8"),
    pytest.param(b'{"format": 0, "sources": {}}', id="another-format"),
    pytest.param(
        b'{"format": true, "derived_from": {"reference_genome": null, '
        b'"files_md5": {}}, "table_source": "tabix_estimate", '
        b'"sources": {"tabix_estimate": {"chr1": 1}}}',
        id="format-that-merely-equals-one"),
    pytest.param(
        b'{"format": 1, "derived_from": {"reference_genome": null, '
        b'"files_md5": {}}, "table_source": "tabix_estimate", '
        b'"sources": {"reference_genome": {"chr1": "empty"}, '
        b'"tabix_estimate": {"chr1": 1}}}',
        id="reason-outside-the-tables-block"),
    pytest.param(
        b'{"format": 1, "derived_from": {"reference_genome": null, '
        b'"files_md5": {}}, "table_source": "tabix_estimate", '
        b'"sources": {"nonsense": {"chr1": 1}, "tabix_estimate": {}}}',
        id="unknown-source"),
    pytest.param(
        b'{"format": 1, "derived_from": {"reference_genome": null, '
        b'"files_md5": {}}, "table_source": "tabix_estimate", '
        b'"sources": {"tabix_estimate": {"chr1": null}}}',
        id="neither-length-nor-reason"),
])
def test_an_unreadable_lengths_file_is_rewritten_by_an_ordinary_run(
    tmp_path: pathlib.Path, content: bytes,
) -> None:
    """Unreadable is stale, not fatal: every other statistics file is
    recoverable by a run, and this one must not need a manual rm."""
    statistics = repair_the_labelled_score(tmp_path)
    (statistics / "chrom_lengths.json").write_bytes(content)
    resync_the_manifest(tmp_path, "score")

    resource_stats(tmp_path, "score")

    stored = _stored(statistics)
    assert stored["format"] == 1
    assert stored["sources"]["reference_genome"] == {
        "chr1": CHR1_GENOME_LENGTH}
