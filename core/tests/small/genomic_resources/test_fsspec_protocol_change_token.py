# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What decides that a recorded file state is out of date (gain#881).

A ``.state`` is trusted to describe the bytes in the store, and what it
is checked against decides two different failures. Too strict, and an
unchanged file is re-read and re-hashed for nothing -- which is what a
modification time does on s3, where the value depends on whether the
answer came from ``head_object`` (whole seconds) or ``list_objects_v2``
(milliseconds). Too loose, and a real change goes unnoticed and a stale
md5 sum is published into a ``.MANIFEST``, which is a committed artefact.

The store's own change token is what avoids both, and these tests hold
the second line: rounding the recorded timestamp to whole seconds would
make the first failure go away and quietly buy the second one, because
same-size rewrites land in the same whole second routinely.
"""
import pytest
import yaml
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.faulty_filesystem import (
    corrupt_same_length,
)


def _record_state_for(
        proto: FsspecReadWriteProtocol, resource: GenomicResource,
        filename: str) -> str:
    """Record a state describing what is stored now, and return the text."""
    with proto.open_raw_file(resource, filename, "rt") as infile:
        original = str(infile.read())
    proto.save_resource_file_state(
        resource, proto.build_resource_file_state(resource, filename))
    return original


@pytest.mark.grr_full
def test_a_same_size_rewrite_is_detected_without_a_pause(
        fsspec_proto: FsspecReadWriteProtocol,
        grr_scheme: str) -> None:
    """A change is noticed even when nothing waits for the clock to tick.

    Both writes are the test's own and nothing waits between them, so on
    s3 they land in the same whole second -- measured, 11 of 11
    consecutive same-size rewrites did. A comparison that only saw whole
    seconds would find the state still current, keep its now-wrong md5
    sum, and report that the file needs no download.

    The first write matters as much as the second. Recording the state
    against the *fixture's* upload would leave the interval between the
    two versions to however long the fixture happened to take, which is
    long enough to straddle a second boundary and let the modification
    time discriminate after all -- the test would then pass without the
    token having done anything.

    Only where the store offers a token, though. The recorded
    modification time is rounded to a hundredth of a second, so on a
    local filesystem two writes this close land in the same rounded
    value and no comparison of timestamps can separate them. That is a
    pre-existing limit of the recorded resolution, not something this
    change introduced or could fix, and it is why the ``file`` arm is
    skipped here rather than asserted.
    ``test_the_cache_decision_does_not_forgive_one_tick`` covers the
    fallback comparison on its own terms.
    """
    proto = fsspec_proto
    resource = proto.get_resource("one")

    if grr_scheme == "file":
        pytest.skip(
            "the recorded timestamp is rounded to a hundredth of a "
            "second, so two writes this close are indistinguishable "
            "where there is no change token")

    # Given a file this test wrote itself...
    with proto.open_raw_file(resource, "data.txt", "rt") as infile:
        carried_over = infile.read()
    with proto.open_raw_file(resource, "data.txt", "wt") as outfile:
        outfile.write(carried_over)

    # ...whose recorded state describes the bytes stored now
    original = _record_state_for(proto, resource, "data.txt")

    # When it is overwritten with different bytes of the very same length
    with proto.open_raw_file(resource, "data.txt", "wt") as outfile:
        outfile.write(corrupt_same_length(original))

    # Then classifying it against the manifest -- which still carries the
    # md5 sum of the original bytes -- reports that it must be fetched
    verdict = proto.classify_resource_file(resource, resource, "data.txt")

    assert verdict.needs_download, (
        "a same-size rewrite went unnoticed, so the state kept an md5 sum "
        "that no longer describes the stored bytes")


@pytest.mark.grr_local
def test_the_cache_decision_does_not_forgive_one_tick(
        fsspec_proto: FsspecReadWriteProtocol,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Where there is no token, one tick of difference is a difference.

    The recorded timestamp is rounded to a hundredth of a second, so a
    hundredth is the smallest difference the fallback can see at all.
    The cache decision compares for equality and therefore sees it; the
    manifest scan allows a hundredth of a second and does not. Giving the
    cache decision the scan's tolerance -- which is tempting, since two
    sites answering one question ought to answer it the same way -- makes
    it blind to exactly this, and that was a real regression on the way
    to this change, so it is pinned here.

    The timestamps are supplied rather than provoked: a difference of one
    tick is not something two real writes can be made to produce on
    demand. They are also chosen so that their difference is *exactly*
    one hundredth in binary floating point -- ``100.01 - 100.00`` is
    fractionally more than a hundredth, which slips past a
    ``<= 1e-2`` comparison and would make this test pass no matter what
    the tolerance is.
    """
    proto = fsspec_proto
    resource = proto.get_resource("one")
    reported = [0.0]
    monkeypatch.setattr(
        type(proto), "_get_filepath_timestamp",
        lambda _self, _filepath: reported[0])

    # Given a state recorded when the store reported one instant...
    original = _record_state_for(proto, resource, "data.txt")

    # ...and a rewrite the store reports one tick later
    with proto.open_raw_file(resource, "data.txt", "wt") as outfile:
        outfile.write(corrupt_same_length(original))
    reported[0] = 0.01

    # Then the cache decision treats the file as changed
    verdict = proto.classify_resource_file(resource, resource, "data.txt")

    assert verdict.needs_download, (
        "a one-tick timestamp difference was forgiven, so the smallest "
        "change the recorded resolution can express went unnoticed")


@pytest.mark.grr_full
def test_the_manifest_scan_rehashes_a_same_size_rewrite(
        fsspec_proto: FsspecReadWriteProtocol,
        grr_scheme: str,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest must never publish an md5 sum of bytes that are gone.

    The scan trusts a recorded state whose file looks unchanged and
    copies the md5 sum straight out of it, which is what makes a rebuild
    cheap. A same-size rewrite it cannot see therefore does not merely
    cost nothing -- it writes the *previous* md5 sum into the
    ``.MANIFEST``, a committed artefact every client trusts.

    The modification time is forced to whole seconds here, which is what
    a store that reports it that way would give. Against MinIO the scan
    is saved by an accident rather than by the rule: it lists, and the
    listing reports milliseconds where the recorded state came from a
    ``head_object`` that reports whole seconds, so the two disagree and
    the file is re-hashed for the wrong reason. Take that disagreement
    away -- as a store with a second-granular clock does, and real S3 is
    believed to be one -- and the timestamp can no longer tell these two
    versions apart at all. The change token still can.
    """
    proto = fsspec_proto
    resource = proto.get_resource("one")

    if grr_scheme == "file":
        pytest.skip(
            "the local filesystem offers no change token, so a "
            "second-granular clock leaves nothing that could tell the "
            "two versions apart")

    # Skipping on the scheme rather than on the token's absence is the
    # point: were this to skip whenever no token came back, the token
    # disappearing -- the regression this guards -- would silence the
    # guard instead of failing it.
    assert proto.get_resource_file_change_token(
        resource, "data.txt") is not None

    # Given a store whose modification time only counts whole seconds
    unpatched = type(proto)._get_filepath_timestamp
    monkeypatch.setattr(
        type(proto), "_get_filepath_timestamp",
        lambda self, filepath: float(int(unpatched(self, filepath))))

    # ...and a file with a recorded state describing the bytes stored now
    original = _record_state_for(proto, resource, "data.txt")
    superseded_md5 = proto.compute_md5_sum(resource, "data.txt")

    # When it is overwritten with different bytes of the very same length
    with proto.open_raw_file(resource, "data.txt", "wt") as outfile:
        outfile.write(corrupt_same_length(original))

    # Then the manifest describes the bytes that are there now
    manifest = proto.build_manifest(resource)

    assert manifest["data.txt"].md5 != superseded_md5, (
        "the manifest published the md5 sum of the overwritten bytes")
    assert manifest["data.txt"].md5 == proto.compute_md5_sum(
        resource, "data.txt")


@pytest.mark.grr_full
def test_a_state_written_before_there_were_tokens_still_loads(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    """A ``.state`` already on disk has no token key, and must still work.

    The document is written from the dataclass, so every state written
    from now on carries the key; every state written before this does
    not. Reading one of those must not fail, and the file it describes
    must still be judged by the modification time it was recorded
    against, until something rebuilds it.
    """
    proto = fsspec_proto
    resource = proto.get_resource("one")
    _record_state_for(proto, resource, "data.txt")

    # Given the state document as it was written before tokens existed
    path = proto._get_resource_file_state_path(resource, "data.txt")
    with proto.filesystem.open(path, "rt") as infile:
        document = yaml.safe_load(infile.read())
    del document["change_token"]
    with proto.filesystem.open(path, "wt") as outfile:
        outfile.write(yaml.safe_dump(document))

    # When it is loaded
    state = proto.load_resource_file_state(resource, "data.txt")

    # Then it loads, carrying no token
    assert state is not None
    assert state.change_token is None

    # ...and the untouched file it describes is judged current by the
    # modification time, leaving the tokenless state exactly as it was.
    # `needs_download` alone would not show this: it is decided by the
    # md5 sum, which is the same whether the state was trusted or
    # rebuilt from the very same unchanged bytes. A rebuild is visible
    # instead in the token the rebuilt state would have acquired.
    verdict = proto.classify_resource_file(resource, resource, "data.txt")
    assert not verdict.needs_download

    reloaded = proto.load_resource_file_state(resource, "data.txt")
    assert reloaded is not None
    assert reloaded.change_token is None, (
        "the state was rebuilt, so the modification time did not judge "
        "the untouched file current")
