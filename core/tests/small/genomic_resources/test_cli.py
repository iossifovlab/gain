# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import logging
import os
import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.cli import (
    _create_contents_db,
    _create_grr_repo,
    _find_resources,
    cli_manage,
)
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)
from gain.utils.fs_utils import find_directory_with_a_file


@pytest.fixture
def repo_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, GenomicResourceProtocolRepo]:
    path = tmp_path_factory.mktemp("cli_hist_repo_fixture")
    demo_gtf_content = "TP53\tchr3\t300\t200"
    setup_directories(
        path,
        {
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
            },
            "sub": {
                "two(1.0)": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "gene_models": {
                        "genes.txt": demo_gtf_content,
                    },
                },
            },
        })
    repo = build_filesystem_test_repository(path)
    return path, repo


def test_cli_manifest(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    # Given
    path, _repo = repo_fixture
    (path / GR_CONTENTS_FILE_NAME).unlink(missing_ok=True)
    (path / "one" / ".MANIFEST").unlink(missing_ok=True)
    assert not (path / GR_CONTENTS_FILE_NAME).is_file()
    assert not (path / "one" / ".MANIFEST").is_file()

    # When
    cli_manage(["repo-manifest", "-R", str(path)])

    # Then
    assert (path / "one/.MANIFEST").is_file()


def test_cli_without_arguments(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    mocker: pytest_mock.MockerFixture,
    capsys: pytest.CaptureFixture,
) -> None:
    # Given
    path, _repo = repo_fixture
    cli_manage(["repo-manifest", "-R", str(path)])
    mocker.patch("os.getcwd", return_value=str(path))
    capsys.readouterr()

    # When
    with pytest.raises(SystemExit):
        cli_manage([])

    # Then
    out, err = capsys.readouterr()

    assert err == ""
    assert "list" in out
    assert "repo-init" in out
    assert "repo-manifest,resource-manifest" in out
    assert "repo-stats,resource-stats" in out
    assert "repo-info,resource-info" in out
    assert "repo-repair,resource-repair" in out


def test_cli_list(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    path, _repo = repo_fixture

    cli_manage(["list", "-R", str(path)])
    out, err = capsys.readouterr()

    assert err == ""
    assert out == (
        "basic                0        2 7.0 B        manage one\n"
        "gene_models          1.0      2 50.0 B       manage sub/two\n"
    )


def test_cli_list_filtered_by_query(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    """``list -q`` selects with the annotator wildcard language."""
    path, _repo = repo_fixture

    cli_manage(["list", "-R", str(path), "-q", "sub/*"])
    out, err = capsys.readouterr()

    assert err == ""
    assert out == (
        "gene_models          1.0      2 50.0 B       manage sub/two\n"
    )


def test_cli_list_filtered_by_type_and_search_term(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    """``list -t`` and ``list -s`` were previously ``grr_browse``-only.

    Both are applied in SQL against the FTS index, so unlike ``-q`` they
    need one built.
    """
    path, _repo = repo_fixture
    cli_manage(["repo-manifest", "-R", str(path)])
    # Only `one` reaches the index: this fixture's `sub/two` declares an
    # unknown `file:` key, so indexing it fails and it is skipped.
    _create_contents_db(build_filesystem_test_protocol(path, repair=False))
    capsys.readouterr()

    cli_manage(["list", "-R", str(path), "-t", "basic"])
    out, err = capsys.readouterr()
    assert err == ""
    assert "manage one" in out
    assert "sub/two" not in out

    cli_manage(["list", "-R", str(path), "-s", "one"])
    out, err = capsys.readouterr()
    assert err == ""
    assert "manage one" in out
    assert "sub/two" not in out


def test_cli_list_query_matching_nothing_lists_nothing(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    path, _repo = repo_fixture

    cli_manage(["list", "-R", str(path), "-q", "no/such/*"])
    out, err = capsys.readouterr()

    assert err == ""
    assert out == ""


def test_cli_list_survives_a_resource_whose_labels_are_not_a_mapping(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One malformed ``meta.labels`` must not truncate the listing.

    ``meta.labels`` is free-form YAML and a resource can declare it as a
    scalar; the label clause used to reach that value and end the command
    in an ``AttributeError`` naming neither the resource nor what was
    wrong with it (gain#654). The listing reports it and goes on.
    """
    (
        a_grr()
        .with_resource(
            "scores/aaa", a_position_score().with_labels(domain="alpha"))
        .with_resource(
            "scores/broken", a_position_score().with_raw_labels("some text"))
        .with_resource(
            "scores/zzz", a_position_score().with_labels(domain="alpha"))
        .build_repo(tmp_path)
    )
    capsys.readouterr()

    with caplog.at_level(logging.WARNING):
        cli_manage(["list", "-R", str(tmp_path), "-q", '*[domain="alpha"]'])

    out, err = capsys.readouterr()
    assert err == ""
    assert "scores/aaa" in out
    assert "scores/zzz" in out
    assert "scores/broken" not in out
    assert any(
        "scores/broken" in record.getMessage() and "str" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


def test_cli_list_rejects_a_malformed_query(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad ``-q`` is a usage error, not a traceback out of the listing."""
    path, _repo = repo_fixture

    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["list", "-R", str(path), "-q", 'sub/*[bad="x"'])

    assert excinfo.value.code == 1
    assert 'sub/*[bad="x"' in caplog.text


def test_cli_list_with_an_empty_query_lists_everything(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    path, _repo = repo_fixture

    cli_manage(["list", "-R", str(path), "-q", ""])
    out, err = capsys.readouterr()

    assert err == ""
    assert "manage one" in out
    assert "manage sub/two" in out


def test_cli_list_rejects_a_malformed_search_term(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad ``-s`` is a usage error, like a bad ``-q`` (gain#632).

    The index has to exist for the term to reach FTS5 at all -- without
    one, ``-s`` fails earlier, on the missing index, and would not
    exercise this.
    """
    path, _repo = repo_fixture
    cli_manage(["repo-manifest", "-R", str(path)])
    _create_contents_db(build_filesystem_test_protocol(path, repair=False))

    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["list", "-R", str(path), "-s", '"'])

    assert excinfo.value.code == 1
    assert '"' in caplog.text
    # The traceback is the thing being replaced, so its absence is the
    # assertion worth making -- the FTS complaint itself is carried into
    # the message on purpose.
    assert "Traceback" not in caplog.text


def test_cli_list_with_an_empty_search_term_lists_everything(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
) -> None:
    """``-s ""`` is an unset filter, not a search for nothing (gain#633).

    This fixture has no FTS index, which is the point: an empty term must
    not be the one filter that demands one.
    """
    path, _repo = repo_fixture

    cli_manage(["list", "-R", str(path), "-s", ""])
    out, err = capsys.readouterr()

    assert err == ""
    assert "manage one" in out
    assert "manage sub/two" in out


def test_cli_list_by_type_without_an_index_says_so(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``-s``/``-t`` need an FTS index; say that instead of dying.

    A checked-out GRR has a `.CONTENTS.json` and no `.CONTENTS.sqlite3.gz`,
    which is exactly the repository `grr_manage` is usually pointed at.
    """
    path, _repo = repo_fixture

    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["list", "-R", str(path), "-t", "basic"])

    assert excinfo.value.code == 1
    assert "index" in caplog.text.lower()


def test_cli_list_without_repo_argument(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    capsys: pytest.CaptureFixture,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Given
    path, _repo = repo_fixture
    cli_manage(["repo-manifest", "-R", str(path)])
    mocker.patch("os.getcwd", return_value=str(path))
    capsys.readouterr()

    # When
    cli_manage(["list"])

    # Then
    out, err = capsys.readouterr()
    assert err == ""
    assert out == (
        f"working with repository: {path!s}\n"
        "basic                0        2 7.0 B        manage one\n"
        "gene_models          1.0      2 50.0 B       manage sub/two\n"
    )


def test_find_repo_dir_simple(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    # Given
    path, _repo = repo_fixture
    (path / GR_CONTENTS_FILE_NAME).unlink(missing_ok=True)
    (path / "one" / ".MANIFEST").unlink(missing_ok=True)
    os.chdir(path)
    res = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)
    assert res is None

    # When
    cli_manage(["repo-manifest", "-R", str(path)])
    res = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)

    # Then
    assert res == path


def test_find_resource_dir_simple(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    path, _repo = repo_fixture

    cli_manage(["repo-manifest", "-R", str(path)])
    os.chdir(path / "sub" / "two(1.0)" / "gene_models")

    repo_dir = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)
    assert repo_dir is not None
    assert repo_dir == path

    resource_dir = find_directory_with_a_file(GR_CONF_FILE_NAME)
    assert resource_dir is not None
    assert resource_dir == path / "sub" / "two(1.0)"

    path = pathlib.Path(resource_dir)
    assert str(path.relative_to(repo_dir)) == "sub/two(1.0)"


def test_find_resource_with_version(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    path, repo = repo_fixture

    cli_manage(["repo-manifest", "-R", str(path)])
    os.chdir(path / "sub" / "two(1.0)" / "gene_models")

    resourses = _find_resources(repo.proto, str(path))
    assert resourses
    assert len(resourses) == 1
    res = resourses[0]
    assert res is not None
    assert res.resource_id == "sub/two"
    assert res.version == (1, 0)


def test_find_resource_without_version(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    path, repo = repo_fixture

    cli_manage(["repo-manifest", "-R", str(path)])
    os.chdir(path / "one")
    resourses = _find_resources(repo.proto, str(path))
    assert resourses
    assert len(resourses) == 1
    res = resourses[0]
    assert res is not None
    assert res.resource_id == "one"
    assert res.version == (0,)


def test_find_resource_with_resource_id(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    path, repo = repo_fixture

    resourses = _find_resources(repo.proto, str(path), resource="sub/two")
    assert resourses
    assert len(resourses) == 1
    res = resourses[0]

    assert res is not None
    assert res.resource_id == "sub/two"
    assert res.version == (1, 0)


def test_repo_init(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    # Given
    path, _repo = repo_fixture
    (path / GR_CONTENTS_FILE_NAME).unlink(missing_ok=True)
    (path / GR_CONTENTS_FILE_NAME[:-3]).unlink(missing_ok=True)

    # When
    cli_manage(["repo-init", "-R", str(path)])

    # Then
    assert (path / GR_CONTENTS_FILE_NAME).exists()
    assert (path / GR_CONTENTS_FILE_NAME[:-3]).exists()


def test_repo_init_inside_repo(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
) -> None:
    # Given
    path, _repo = repo_fixture
    (path / GR_CONTENTS_FILE_NAME).unlink(missing_ok=True)
    (path / GR_CONTENTS_FILE_NAME[:-3]).unlink(missing_ok=True)

    (path / "inside").mkdir()
    cli_manage(["repo-init", "-R", str(path)])

    # When
    with pytest.raises(SystemExit, match="1"):
        cli_manage(["repo-init", "-R", str(path / "inside")])

    # Then
    assert not (path / "inside" / GR_CONTENTS_FILE_NAME).exists()
    assert not (path / "inside" / GR_CONTENTS_FILE_NAME[:-3]).exists()


def test_grr_manage_version_report(
    capsys: pytest.CaptureFixture,
) -> None:
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli_manage(["--version"])

    out, _err = capsys.readouterr()
    assert out.startswith("GAIn version: ")


@pytest.mark.parametrize("flag", ["-n", "--dry-run", "-f", "--force"])
def test_repo_init_refuses_dry_run_and_force(
    repo_fixture: tuple[pathlib.Path, GenomicResourceProtocolRepo],
    flag: str,
) -> None:
    """`repo-init` must not offer flags it has never honoured (#415).

    It mounted the Force/Dry run group but read neither value, so
    `repo-init -n` initialised the repository for real - writing the
    content file and a state for every file it hashed, which is exactly
    what `--dry-run` promises not to do (#257).
    """
    # Given a directory that is not yet a GRR
    path, _repo = repo_fixture
    (path / GR_CONTENTS_FILE_NAME).unlink(missing_ok=True)
    (path / GR_CONTENTS_FILE_NAME[:-3]).unlink(missing_ok=True)

    # When 'repo-init' is asked for a dry run, or forced
    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-init", flag, "-R", str(path)])

    # Then argparse rejects the flag outright
    assert excinfo.value.code == 2

    # ... and the repository was left uninitialised
    assert not (path / GR_CONTENTS_FILE_NAME).exists()
    assert not (path / GR_CONTENTS_FILE_NAME[:-3]).exists()


# ---------------------------------------------------------------------------
# gain#445: the user GRR definition nested under the CLI's synthetic group
# ---------------------------------------------------------------------------

def test_id_less_user_definition_gets_a_stable_id(
    tmp_path: pathlib.Path,
) -> None:
    definition_path = tmp_path / "grr.yaml"
    definition_path.write_text(
        "type: http\nurl: https://grr.example.com\n")

    repo = _create_grr_repo(
        argparse.Namespace(grr=str(definition_path)), str(tmp_path / "local"))

    assert isinstance(repo, GenomicResourceGroupRepo)
    assert [child.repo_id for child in repo.children] == [
        "local", "default_grr"]


def test_user_definition_keeps_its_own_id(
    tmp_path: pathlib.Path,
) -> None:
    definition_path = tmp_path / "grr.yaml"
    definition_path.write_text(
        "id: my-grr\ntype: http\nurl: https://grr.example.com\n")

    repo = _create_grr_repo(
        argparse.Namespace(grr=str(definition_path)), str(tmp_path / "local"))

    assert isinstance(repo, GenomicResourceGroupRepo)
    assert [child.repo_id for child in repo.children] == ["local", "my-grr"]


def test_user_definition_named_local_still_builds(
    tmp_path: pathlib.Path,
) -> None:
    # ``id: "local"`` is what the GRR definitions shipped in this repo use --
    # ``web_api/scripts/grr-definition-dir.yaml`` and
    # ``spliceai_annotator/tests/integration_grr_definition.yaml`` -- so it
    # must not collide with the id the CLI gives its own child (#445).
    definition_path = tmp_path / "grr.yaml"
    definition_path.write_text(
        'id: "local"\ntype: http\nurl: https://grr.example.com\n')

    repo = _create_grr_repo(
        argparse.Namespace(grr=str(definition_path)), str(tmp_path / "local"))

    assert isinstance(repo, GenomicResourceGroupRepo)
    child_ids = [child.repo_id for child in repo.children]
    assert child_ids[1] == "local"
    assert len(set(child_ids)) == len(repo.children) == 2


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "just a string\n"])
def test_a_malformed_definition_file_is_reported_as_a_bad_definition(
    tmp_path: pathlib.Path,
    content: str,
) -> None:
    """A definition file that is not a mapping must not crash the CLI.

    The id normalisation reads ``id`` off the loaded definition, which is
    whatever ``yaml.safe_load`` returned -- ``None`` for an empty file, a
    list, a bare string. Calling ``.get`` on that raises ``AttributeError``
    and buries the real problem; the definition must reach the factory so
    the user is told their definition is invalid.
    """
    definition_path = tmp_path / "grr.yaml"
    definition_path.write_text(content)

    with pytest.raises(ValueError, match="invalid GRR definition"):
        _create_grr_repo(
            argparse.Namespace(grr=str(definition_path)),
            str(tmp_path / "local"))
