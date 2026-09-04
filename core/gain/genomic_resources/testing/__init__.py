"""Provides tools usefult for testing."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
import pathlib
import re
import shutil
import tempfile
import textwrap
import uuid
from collections.abc import Generator
from typing import Any, Literal, cast, overload
from urllib.parse import urlparse

import pyBigWig
import pysam
from s3fs.core import S3FileSystem

from gain import logging
from gain.genomic_resources.fsspec_protocol import (
    GRR_INTERNAL_DIR,
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
    FsspecRepositoryProtocol,
    build_fsspec_protocol,
    build_inmemory_protocol,
    canonical_public_url,
)
from gain.genomic_resources.gene_models import GeneModels
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing.faulty_filesystem import FaultyFileSystem
from gain.utils.fs_utils import endswith_ci

logger = logging.getLogger(__name__)


def convert_to_tab_separated(content: str) -> str:
    """Convert a string into tab separated file content.

    Useful for testing purposes.
    If you need to have a space in the file content use '||'.
    """
    result = []
    for line in content.split("\n"):
        line = line.strip("\n\r")
        if not line:
            continue
        if line.startswith("##"):
            result.append(line)
        else:
            result.append("\t".join(line.split()))
    text = "\n".join(result)
    text = text.replace("||", " ")
    return text.replace("EMPTY", ".")


def setup_directories(
        root_dir: pathlib.Path,
        content: str | dict[str, Any]) -> None:
    """Set up directory and subdirectory structures using the content."""
    root_dir = pathlib.Path(root_dir)
    root_dir.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        root_dir.write_text(content, encoding="utf8")
    elif isinstance(content, bytes):
        root_dir.write_bytes(content)
    elif isinstance(content, dict):
        for path_name, path_content in content.items():
            setup_directories(root_dir / path_name, path_content)
    else:
        raise TypeError(
            f"unexpected content type: {content} for {root_dir}")


def setup_pedigree(ped_path: pathlib.Path, content: str) -> pathlib.Path:
    ped_data = convert_to_tab_separated(content)
    setup_directories(ped_path, ped_data)
    return ped_path


def setup_denovo(denovo_path: pathlib.Path, content: str) -> pathlib.Path:
    denovo_data = convert_to_tab_separated(content)
    setup_directories(denovo_path, denovo_data)
    return denovo_path


def setup_tabix(
        tabix_path: pathlib.Path, tabix_content: str,
        **kwargs: bool | str | int) -> tuple[str, str]:
    """Set up a tabix file."""
    content = convert_to_tab_separated(tabix_content)
    out_path = tabix_path
    if tabix_path.suffix == ".gz":
        out_path = tabix_path.with_suffix("")
    setup_directories(out_path, content)

    tabix_filename = str(out_path.parent / f"{out_path.name}.gz")
    # ``csi=True`` is forwarded to ``pysam.tabix_index``, which then writes a
    # ``.csi`` index instead of the default ``.tbi``; report the name that is
    # actually produced.
    suffix = ".csi" if kwargs.get("csi") else ".tbi"
    index_filename = f"{tabix_filename}{suffix}"
    force = cast(bool, kwargs.pop("force", False))
    # pylint: disable=no-member
    pysam.tabix_compress(str(out_path), tabix_filename, force=force)
    pysam.tabix_index(tabix_filename, force=force, **kwargs)  # type: ignore

    out_path.unlink()

    return tabix_filename, index_filename


def setup_gzip(gzip_path: pathlib.Path, gzip_content: str) -> pathlib.Path:
    """Set up a gzipped TSV file."""
    content = convert_to_tab_separated(gzip_content)
    out_path = gzip_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if gzip_path.suffix != ".gz":
        out_path = gzip_path.with_suffix("gz")
    with gzip.open(out_path, "wt") as outfile:
        outfile.write(content)
    return out_path


def setup_vcf(
        out_path: pathlib.Path, content: str, *,
        csi: bool = False) -> pathlib.Path:
    """Set up a VCF file using the content."""
    vcf_data = convert_to_tab_separated(content)
    vcf_path = out_path
    if out_path.suffix == ".gz":
        vcf_path = out_path.with_suffix("")

    assert vcf_path.suffix == ".vcf"
    header_path = vcf_path.with_suffix("")
    header_path = header_path.parent / f"{header_path.name}.header.vcf"

    setup_directories(vcf_path, vcf_data)

    # pylint: disable=no-member
    if out_path.suffix == ".gz":
        vcf_gz_filename = str(vcf_path.parent / f"{vcf_path.name}.gz")
        pysam.tabix_compress(str(vcf_path), vcf_gz_filename)
        pysam.tabix_index(vcf_gz_filename, preset="vcf", csi=csi)

    with pysam.VariantFile(str(out_path)) as variant_file:
        header = variant_file.header
        with open(header_path, "wt", encoding="utf8") as outfile:
            outfile.write(str(header))

    if out_path.suffix == ".gz":
        header_gz_filename = str(header_path.parent / f"{header_path.name}.gz")
        pysam.tabix_compress(str(header_path), header_gz_filename)
        pysam.tabix_index(header_gz_filename, preset="vcf")
    return out_path


def setup_dae_transmitted(
    root_path: pathlib.Path,
    summary_content: str,
    toomany_content: str,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Set up a DAE transmitted variants file using passed content."""
    summary = convert_to_tab_separated(summary_content)
    toomany = convert_to_tab_separated(toomany_content)

    setup_directories(root_path, {
        "dae_transmitted_data": {
            "tr.txt": summary,
            "tr-TOOMANY.txt": toomany,
        },
    })

    # pylint: disable=no-member
    pysam.tabix_compress(
        str(root_path / "dae_transmitted_data" / "tr.txt"),
        str(root_path / "dae_transmitted_data" / "tr.txt.gz"))
    pysam.tabix_compress(
        str(root_path / "dae_transmitted_data" / "tr-TOOMANY.txt"),
        str(root_path / "dae_transmitted_data" / "tr-TOOMANY.txt.gz"))

    pysam.tabix_index(
        str(root_path / "dae_transmitted_data" / "tr.txt.gz"),
        seq_col=0, start_col=1, end_col=1, line_skip=1)
    pysam.tabix_index(
        str(root_path / "dae_transmitted_data" / "tr-TOOMANY.txt.gz"),
        seq_col=0, start_col=1, end_col=1, line_skip=1)

    return (root_path / "dae_transmitted_data" / "tr.txt.gz",
            root_path / "dae_transmitted_data" / "tr-TOOMANY.txt.gz")


def setup_bigwig(
    out_path: pathlib.Path,
    content: str,
    chrom_lens: dict[str, int],
) -> pathlib.Path:
    """
    Setup a bigwig format variants file using bedGraph-style content.

    Example:
    chr1	0	100	0.0
    chr1	100	120	1.0
    chr1	125	126	200.0
    """
    assert out_path.parent.exists()
    bw_file = pyBigWig.open(str(out_path), "w")  # pylint: disable=I1101
    bw_file.addHeader(list(chrom_lens.items()), maxZooms=0)

    chrom_col: list[str] = []
    start_col: list[int] = []
    end_col: list[int] = []
    val_col: list[float] = []
    prev_end: int = -1
    prev_chrom: str = ""
    for line in convert_to_tab_separated(content).split("\n"):
        tokens = line.strip().split("\t")
        assert len(tokens) == 4
        chrom = tokens[0]
        start = int(tokens[1])
        end = int(tokens[2])
        val = float(tokens[3])

        assert chrom in chrom_lens
        assert start < end
        if chrom == prev_chrom:
            assert start >= prev_end
        prev_chrom = chrom
        prev_end = end

        chrom_col.append(chrom)
        start_col.append(start)
        end_col.append(end)
        val_col.append(val)

    bw_file.addEntries(chrom_col, start_col, ends=end_col, values=val_col)
    bw_file.close()
    return out_path


def setup_genome(out_path: pathlib.Path, content: str) -> ReferenceGenome:
    """Set up reference genome using the content."""
    if out_path.suffix != ".fa":
        raise ValueError("genome output file is expected to have '.fa' suffix")
    setup_directories(out_path, convert_to_tab_separated(content))

    # pylint: disable=no-member
    pysam.faidx(str(out_path))

    setup_directories(out_path.parent, {
        "genomic_resource.yaml": textwrap.dedent(f"""
            type: genome

            filename: {out_path.name}
        """),
    })
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.reference_genome import (
        build_reference_genome_from_file,
    )
    return build_reference_genome_from_file(str(out_path)).open()


def setup_genome_bgz(out_path: pathlib.Path, content: str) -> ReferenceGenome:
    """Set up a bgzipped reference genome using the content.

    Writes a BGZF-compressed FASTA at ``out_path`` (expected to end in
    ``.fa.gz``/``.fa.bgz``) together with its ``.fai`` and ``.gzi`` indexes.
    """
    if not endswith_ci(out_path.name, (".fa.gz", ".fa.bgz")):
        raise ValueError(
            "bgzipped genome output file is expected to have a "
            "'.fa.gz' or '.fa.bgz' suffix")

    plain_path = out_path.parent / out_path.name.rsplit(".", 1)[0]
    setup_directories(plain_path, convert_to_tab_separated(content))

    # pylint: disable=no-member
    pysam.tabix_compress(str(plain_path), str(out_path), force=True)
    plain_path.unlink()
    # faidx on a bgzipped FASTA emits both the .fai and the .gzi index.
    pysam.faidx(str(out_path))

    setup_directories(out_path.parent, {
        "genomic_resource.yaml": textwrap.dedent(f"""
            type: genome

            filename: {out_path.name}
        """),
    })
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.reference_genome import (
        build_reference_genome_from_file,
    )
    return build_reference_genome_from_file(str(out_path)).open()


def setup_gene_models(
        out_path: pathlib.Path,
        content: str,
        fileformat: str | None = None,
        config: str | None = None) -> GeneModels:
    """Set up gene models in refflat format using the passed content."""
    setup_directories(out_path, convert_to_tab_separated(content))

    if config is None:
        config = textwrap.dedent(f"""
            type: gene_models

            filename: {out_path.name}

            format: "{fileformat}"
        """)
    setup_directories(out_path.parent, {"genomic_resource.yaml": config})

    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.gene_models.gene_models_factory import (
        build_gene_models_from_file,
    )
    gene_models = build_gene_models_from_file(
        str(out_path), file_format=fileformat)
    gene_models.load()
    return gene_models


def setup_empty_gene_models(out_path: pathlib.Path) -> GeneModels:
    """Set up empty gene models."""
    content = """
#geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
    """  # ruff: ignore[line-too-long]
    return setup_gene_models(out_path, content, fileformat="refflat")


#: Everything a repository id may not carry if it is to name a directory:
#: see ``is_safe_repo_id``. Substituting rather than dropping keeps two
#: roots that differ only in a stripped character from colliding -- and the
#: digest appended below would separate them anyway.
_UNSAFE_ID_CHARACTER_RE = re.compile(r"[^A-Za-z0-9._-]")


def derive_test_proto_id(
    root: str, *, read_only: bool = False, public_url: str | None = None,
) -> str:
    """Derive a cache-compatible protocol id from a protocol's root.

    The id a testing protocol gets by default must satisfy three constraints
    at once, and the ``<name>-<digest>`` shape is what satisfies all three:

    - it is a single path segment, so ``GenomicResourceCachedRepo`` accepts
      it as a cache directory name (#460) -- the sanitized name cannot
      introduce a separator and the appended digest keeps the whole from
      ever being ``.`` or ``..``;
    - it is unique per distinct root, so two protocols built under
      identically-named temp directories do not trip the group repository's
      duplicate-child-id guard (#445);
    - it is deterministic, so ``FsspecReadOnlyProtocol.__new__``'s
      ``(proto_id, url)`` memo keeps returning one instance per root. A
      random or counter-based id would silently change that identity.

    The leading name is decoration -- it is what makes a cache directory
    readable while debugging; the digest is what carries the uniqueness.

    A read-only protocol gets its own ``-ro`` id over the same root, because
    the memo is keyed on the id and the url alone. Sharing one id between the
    two modes does not yield two protocols -- it is refused (#514) -- and a
    test that wants both modes over one root wants two protocols.

    ``public_url`` folds into the digest for exactly the same reason: it is
    part of a protocol's identity, and a rebuild that would repoint it is
    refused rather than honoured (#841). Two GRRs over one root advertising
    different mirrors are therefore two protocols, not one contested one --
    which is what a test comparing two spellings of an advertised address
    is asking for.
    """
    identity = root if public_url is None else \
        f"{root}\0{canonical_public_url(public_url)}"
    name = _UNSAFE_ID_CHARACTER_RE.sub(
        "_", pathlib.PurePosixPath(root).name)
    suffix = "-ro" if read_only else ""
    return f"{name}-{short_identity_digest(identity)}{suffix}"


def short_identity_digest(identity: str) -> str:
    """Return the short digest the testing helpers name things by.

    One spelling of "distinguish these by content" -- the width and the
    hash are decided here rather than at each call site, so widening it
    for collisions is one edit.
    """
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def build_inmemory_test_protocol(
        content: dict[str, Any]) -> FsspecReadWriteProtocol:
    """Build and return an embedded fsspec protocol for testing."""
    with tempfile.TemporaryDirectory("embedded_test_protocol") as root_path:
        return build_inmemory_protocol(
            derive_test_proto_id(root_path), root_path, content)


#: Roots :func:`build_faulty_test_protocol` has already been asked for.
#: Never evicted, and process-wide, because the protocol memo it guards
#: is too -- a root released here would be answered by the memo, not
#: rebuilt.
_FAULTY_TEST_PROTOCOL_ROOTS: set[str] = set()


def build_faulty_test_protocol(
        root_path: pathlib.Path,
        content: dict[str, Any] | None = None,
) -> tuple[FsspecReadWriteProtocol, FaultyFileSystem]:
    """Build a protocol whose filesystem can be scripted to fail.

    The protocol is constructed directly, with its filesystem handed to it,
    rather than through :func:`build_fsspec_protocol` -- that builder makes
    a filesystem of its own from the url and would drop the scripted one.

    ``root_path`` is what keeps one test's scripted filesystem out of the
    next one's protocol. Protocols are memoized on ``(proto_id, url)`` and
    a rebuild re-runs ``__init__`` on the live instance, rebinding its
    ``filesystem``: two tests sharing a root would share one protocol, and
    the second test's script would be answering the first test's holder.
    A per-test ``tmp_path`` gives both halves of the key their uniqueness,
    the same discipline :func:`build_filesystem_test_protocol` follows.

    ``content``, when given, populates the repository *before* anything is
    scripted, so a test scripts faults onto a repository that is already
    whole.

    Returns the protocol and its filesystem, because the filesystem is what
    a test scripts and ``proto.filesystem`` is typed as the fsspec base.

    A root is refused the second time it is asked for. Nothing else would
    catch the mistake: ``_refuse_a_reconfiguring_rebuild`` compares the
    credential kwargs and the public url, not ``filesystem``, so a repeat
    root is answered with the incumbent protocol carrying the *new*
    script -- a silent wrong-reason pass rather than an error. The natural
    slip is wanting a source and a destination and reaching for
    ``tmp_path`` for both; give them ``tmp_path / "src"`` and
    ``tmp_path / "dst"``.
    """
    root = str(root_path)
    if root in _FAULTY_TEST_PROTOCOL_ROOTS:
        raise ValueError(
            f"a faulty test protocol was already built over {root}; "
            f"protocols are memoized on (proto_id, url) and a rebuild "
            f"rebinds the filesystem of the instance the first caller "
            f"still holds -- give this one a root of its own, e.g. a "
            f"subdirectory of the test's tmp_path")
    _FAULTY_TEST_PROTOCOL_ROOTS.add(root)

    filesystem = FaultyFileSystem()
    proto = FsspecReadWriteProtocol(
        derive_test_proto_id(root), f"memory://{root}",
        filesystem=filesystem)
    if content:
        copy_proto_genomic_resources(
            proto, build_inmemory_test_protocol(content))
    return proto, filesystem


def build_inmemory_test_repository(
        content: dict[str, Any]) -> GenomicResourceProtocolRepo:
    """Create an embedded GRR repository using passed content."""
    proto = build_inmemory_test_protocol(content)
    return GenomicResourceProtocolRepo(proto)


def build_inmemory_test_resource(
        content: dict[str, Any]) -> GenomicResource:
    """Create a test resource based on content passed.

    The passed content should appropriate for a single resource.
    Example content::

        {
            "genomic_resource.yaml": textwrap.dedent('''
                type: position_score
                table:
                    filename: data.txt
                scores:
                    - id: aaaa
                        type: float
                        desc: ""
                        name: sc
            '''),
            "data.txt": convert_to_tab_separated('''
                #chrom start end sc
                1      10    12  1.1
                2      13    14  1.2
            ''')
        }
    """
    proto = build_inmemory_test_protocol(content)
    return proto.get_resource("")


@overload
def build_filesystem_test_protocol(
    root_path: pathlib.Path, *,
    repair: bool = ...,
    proto_id: str | None = ...,
    public_url: str | None = ...,
    read_only: Literal[False] = ...,
) -> FsspecReadWriteProtocol: ...


@overload
def build_filesystem_test_protocol(
    root_path: pathlib.Path, *,
    repair: bool = ...,
    proto_id: str | None = ...,
    public_url: str | None = ...,
    read_only: Literal[True],
) -> FsspecReadOnlyProtocol: ...


def build_filesystem_test_protocol(
    root_path: pathlib.Path, *,
    repair: bool = True,
    proto_id: str | None = None,
    public_url: str | None = None,
    read_only: bool = False,
) -> FsspecRepositoryProtocol:
    """Build and return an filesystem fsspec protocol for testing.

    The root_path is expected to point to a directory structure with all the
    resources.

    Unless ``proto_id`` says otherwise the protocol is named by
    :func:`derive_test_proto_id`, so it can be wrapped in a
    ``GenomicResourceCachedRepo`` without ceremony.

    A ``read_only`` protocol is the shape a repository served from a remote
    is read through -- it is what a test wanting to hand the protocol
    hand-written ``.CONTENTS`` asks for. It cannot repair what it cannot
    write, so ``repair`` must be turned off along with it.

    The derived id is a function of the root and of the mode, and protocols
    are memoized on ``(proto_id, url)``: a second build over a root that
    already has a protocol of that mode returns that same instance, while a
    build in the other mode gets an id -- and so an instance -- of its own.
    Pass an explicit ``proto_id`` when a test wants a genuinely separate
    protocol over one root; an explicit id names one memoized instance, so
    ``build_fsspec_protocol`` refuses to reuse it in the other mode rather
    than answering with the mode built first (#514).

    ``public_url`` is the address a deployment advertises the repository
    at.  It is part of a protocol's identity -- a rebuild that would
    repoint it is refused -- so it joins the derived id too, and two
    protocols over one root advertising different mirrors are two
    protocols, exactly as the two modes are.
    """
    if read_only and repair:
        raise ValueError(
            "a read-only test protocol cannot repair its repository; "
            "pass repair=False along with read_only=True")
    resolved_id = proto_id or derive_test_proto_id(
        str(root_path), read_only=read_only, public_url=public_url)
    proto = build_fsspec_protocol(
        resolved_id,
        str(root_path),
        public_url=public_url,
        read_only=read_only)
    if repair:
        rw_proto = cast(FsspecReadWriteProtocol, proto)
        for res in rw_proto.get_all_resources():
            rw_proto.save_manifest(res, rw_proto.build_manifest(res))
        rw_proto.build_content_file()
    return proto


def build_filesystem_test_repository(
    root_path: pathlib.Path, *,
    proto_id: str | None = None,
    public_url: str | None = None,
) -> GenomicResourceProtocolRepo:
    """Build and return an filesystem fsspec repository for testing.

    The root_path is expected to point to a directory structure with all the
    resources.
    """
    proto = build_filesystem_test_protocol(
        root_path, proto_id=proto_id, public_url=public_url)
    return GenomicResourceProtocolRepo(proto)


def build_filesystem_test_resource(
        root_path: pathlib.Path) -> GenomicResource:
    proto = build_filesystem_test_protocol(root_path)
    return proto.get_resource("")


@contextlib.contextmanager
def build_http_test_protocol(
    root_path: pathlib.Path, *,
    repair: bool = True,
) -> Generator[FsspecReadOnlyProtocol, None, None]:
    """Populate Apache2 directory and construct HTTP genomic resource protocol.

    The Apache2 is used to serve the GRR.
    This root_path directory should be a valid filesystem genomic resource
    repository.
    """
    source_proto = build_filesystem_test_protocol(root_path, repair=repair)
    # This module lives at core/gain/genomic_resources/testing/__init__.py,
    # so four parents up from __file__ is the ``core`` package root.
    http_path = pathlib.Path(__file__).parent.parent.parent.parent
    http_path = http_path / "tests" / ".test_grr"
    assert http_path.parts[-2:] == ("tests", ".test_grr"), http_path
    # Unique per invocation: the python-matrix runs the three core cells
    # (py3.12/3.13/3.14) in parallel against a single host-mounted
    # .test_grr, all running the same tests. Keying the serving directory
    # on root_path.name alone made the cells collide -- one cell's rmtree
    # deleted a directory another was still serving (gain-python-matrix
    # build 30).
    http_path = http_path / f"{root_path.name}-{uuid.uuid4().hex}"
    http_path.mkdir(parents=True, exist_ok=True)
    dest_proto = build_filesystem_test_protocol(http_path)
    copy_proto_genomic_resources(
        dest_proto, source_proto)

    host = os.environ.get("HTTP_HOST", "localhost:28080")
    server_address = f"http://{host}/{http_path.name}"

    try:
        yield build_fsspec_protocol(
            derive_test_proto_id(str(root_path)), server_address)
    except GeneratorExit:
        print("Generator exit")
    finally:
        shutil.rmtree(http_path)


def s3_test_server_endpoint() -> str:
    host = os.environ.get("MINIO_HOST", "localhost:29000")
    # Accept hostname-only MINIO_HOST (default to MinIO's standard 9000)
    # as well as host:port.
    if urlparse(f"//{host}").port is None:
        host = f"{host}:9000"
    return f"http://{host}"


def s3_test_protocol() -> FsspecReadWriteProtocol:
    """Build an S3 fsspec testing protocol on top of existing S3 server."""
    endpoint_url = s3_test_server_endpoint()
    s3filesystem = build_s3_test_filesystem()
    bucket_url = build_s3_test_bucket(s3filesystem)
    return cast(
        FsspecReadWriteProtocol,
        build_fsspec_protocol(
            derive_test_proto_id(bucket_url), bucket_url,
            endpoint_url=endpoint_url))


def build_s3_test_filesystem(
        endpoint_url: str | None = None) -> S3FileSystem:
    """Create an S3 fsspec filesystem connected to the S3 server."""
    if "AWS_SECRET_ACCESS_KEY" not in os.environ:
        os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin"  # ruff: ignore[hardcoded-password-string]
    if "AWS_ACCESS_KEY_ID" not in os.environ:
        os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
    if endpoint_url is None:
        endpoint_url = s3_test_server_endpoint()
    assert endpoint_url is not None
    s3filesystem = S3FileSystem(
        anon=False, client_kwargs={"endpoint_url": endpoint_url})
    s3filesystem.invalidate_cache()
    return s3filesystem


def build_s3_test_bucket(s3filesystem: S3FileSystem | None = None) -> str:
    """Create an s3 test buckent."""
    with tempfile.TemporaryDirectory("s3_test_bucket") as tmp_path:
        if s3filesystem is None:
            s3filesystem = build_s3_test_filesystem()
        bucket_url = f"s3://test-bucket{tmp_path}"
        s3filesystem.mkdir(bucket_url, acl="public-read")
        return bucket_url


@contextlib.contextmanager
def build_s3_test_protocol(
    root_path: pathlib.Path,
) -> Generator[FsspecReadWriteProtocol, None, None]:
    """Construct fsspec genomic resource protocol.

    The S3 bucket is populated with resource from filesystem GRR pointed
    by the root_path.
    """
    endpoint_url = s3_test_server_endpoint()
    s3filesystem = build_s3_test_filesystem(endpoint_url)
    bucket_url = build_s3_test_bucket(s3filesystem)

    proto = cast(
        FsspecReadWriteProtocol,
        build_fsspec_protocol(
            derive_test_proto_id(bucket_url), bucket_url,
            endpoint_url=endpoint_url))
    copy_proto_genomic_resources(
        proto,
        build_filesystem_test_protocol(root_path))

    yield proto


def copy_proto_genomic_resources(
        dest_proto: FsspecReadWriteProtocol,
        src_proto: FsspecReadOnlyProtocol) -> None:
    """Publish every resource of ``src_proto`` into ``dest_proto``.

    Populating a *fresh* s3 protocol takes a bulk path -- see
    :func:`_bulk_populate_genomic_resources` -- which is the same
    repository for a fraction of the round trips (gain#862).  Every other
    destination is populated resource by resource through the protocol.

    The bulk path is only taken for a destination that is still empty: it
    uploads what the source has and so, unlike
    :meth:`ReadWriteRepositoryProtocol.copy_resource`, cannot remove a
    file that has left the manifest since.
    """
    if dest_proto.scheme == "s3" and not dest_proto.filesystem.find(
            dest_proto.url):
        _bulk_populate_genomic_resources(dest_proto, src_proto)
        return

    for res in src_proto.get_all_resources():
        dest_proto.copy_resource(res)
    dest_proto.build_content_file()
    dest_proto.filesystem.invalidate_cache()


def _bulk_populate_genomic_resources(
        dest_proto: FsspecReadWriteProtocol,
        src_proto: FsspecReadOnlyProtocol) -> None:
    """Populate an empty s3 protocol by staging locally and uploading once.

    Copying resource by resource over s3 costs a few hundred small
    synchronous round trips -- an existence check and a directory listing
    per file, a read-back to checksum what was just written, and a
    copy-plus-delete to publish it out of the staging name.  None of that
    work is about s3; it is the protocol being careful about a store it
    cannot see.  So the repository is assembled on local disk first --
    through this very function, against a filesystem protocol, so the
    result is identical by construction -- and then handed to the store in
    one batched transfer.

    A resource's protocol-internal ``.grr`` directory is not uploaded.
    Its ``.state`` documents each record the modification time of the
    object they describe, which does not exist until that object has been
    uploaded, so they are rebuilt against the store afterwards.  Which
    resources exist, and each file's md5, are taken from the staged
    repository rather than re-read from the store: they are the same
    bytes, they are already on local disk, and the copy has verified each
    against the manifest it came from.  So no object is read back.

    The tail matters as much as the transfer.  A file-by-file copy leaves
    the destination with its resource memo WARM (``build_content_file``
    enumerates) and the s3fs listing cache EMPTY, and that combination is
    what keeps a freshly published repository from looking stale: the
    caller's first enumeration is answered from the memo without listing
    s3, so ``modified()`` falls through to a ``head_object``, which is
    what the states recorded.  Leave the memo cold instead and the first
    enumeration lists, ``list_objects_v2`` fills the cache with
    ``LastModified`` values MinIO reports to the millisecond where the
    HEAD reports whole seconds, and ``classify_resource_file`` then finds
    every file drifted and rewrites its state.  So this ends the way the
    file-by-file copy ends: memo dropped, rebuilt, listing cache cleared.
    """
    filesystem = dest_proto.filesystem
    dest_url = dest_proto.url.rstrip("/")

    with tempfile.TemporaryDirectory("_grr_bulk_staging") as staging:
        staging_path = pathlib.Path(staging)
        staging_proto = build_filesystem_test_protocol(staging_path)
        copy_proto_genomic_resources(staging_proto, src_proto)

        local_paths = []
        object_urls = []
        for path in sorted(staging_path.rglob("*")):
            relative = path.relative_to(staging_path)
            if not path.is_file() or GRR_INTERNAL_DIR in relative.parts:
                continue
            local_paths.append(str(path))
            object_urls.append(f"{dest_url}/{relative.as_posix()}")
        filesystem.put(local_paths, object_urls)

        filesystem.invalidate_cache()
        for staged_res in staging_proto.get_all_resources():
            resource = GenomicResource(
                staged_res.resource_id, staged_res.version, dest_proto)
            for entry in staged_res.get_manifest():
                dest_proto.save_resource_file_state(
                    resource,
                    dest_proto.build_resource_file_state(
                        resource, entry.name, md5=entry.md5))

    dest_proto.invalidate()
    dest_proto.get_all_resources_dict()
    filesystem.invalidate_cache()


@contextlib.contextmanager
def proto_builder(
    scheme: str, content: dict,
) -> Generator[
        FsspecReadOnlyProtocol | FsspecReadWriteProtocol,
        None, None]:
    """Build a test genomic resource protocol with specified content."""
    with tempfile.TemporaryDirectory("s3_test_bucket") as tmp_path:
        root_path = pathlib.Path(tmp_path)
        setup_directories(root_path, content)

        if scheme == "file":
            try:
                yield build_filesystem_test_protocol(root_path)
            except GeneratorExit:
                print("Generator exit")
            return
        if scheme == "s3":
            with build_s3_test_protocol(root_path) as proto:
                try:
                    yield proto
                except GeneratorExit:
                    print("Generator exit")
            return
        if scheme == "http":
            with build_http_test_protocol(root_path) as proto:
                try:
                    yield proto
                except GeneratorExit:
                    print("Generator exit")
            return

    raise ValueError(f"unexpected protocol scheme: <{scheme}>")


@contextlib.contextmanager
def resource_builder(
        scheme: str, content: dict) -> Generator[GenomicResource, None, None]:
    with proto_builder(scheme, content) as proto:
        yield proto.get_resource("")
