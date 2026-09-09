# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The work-dir convention a task-graph CLI tool follows (``work_dir``)."""
import pathlib

import pytest
import pytest_mock
from gain.task_graph.work_dir import (
    absolutize_path_args,
    apply_work_dir_defaults,
    maybe_remove_work_dir,
)


def _work_dir_args(work_dir: pathlib.Path, output: pathlib.Path, **over):
    args = {
        "work_dir": str(work_dir),
        "work_dir_created": True,
        "output": str(output),
        "command": "run",
        "keep_parts": False,
        "keep_work_dir": False,
    }
    args.update(over)
    return args


def test_maybe_remove_work_dir_removes_created_dir_on_success(
    tmp_path: pathlib.Path,
) -> None:
    work = tmp_path / "out_work"
    (work / ".task-status").mkdir(parents=True)
    args = _work_dir_args(work, tmp_path / "out.vcf")

    maybe_remove_work_dir(args, result=True)

    assert not work.exists()


def test_maybe_remove_work_dir_keeps_preexisting_dir(
    tmp_path: pathlib.Path,
) -> None:
    work = tmp_path / "preexisting_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf", work_dir_created=False)

    maybe_remove_work_dir(args, result=True)

    assert work.exists()


def test_maybe_remove_work_dir_keeps_dir_when_run_failed(
    tmp_path: pathlib.Path,
) -> None:
    work = tmp_path / "failed_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf")

    maybe_remove_work_dir(args, result=False)

    assert work.exists()


@pytest.mark.parametrize("command", ["list", "status"])
def test_maybe_remove_work_dir_keeps_dir_for_status_commands(
    tmp_path: pathlib.Path, command: str,
) -> None:
    work = tmp_path / "status_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf", command=command)

    maybe_remove_work_dir(args, result=True)

    assert work.exists()


@pytest.mark.parametrize("flag", ["keep_parts", "keep_work_dir"])
def test_maybe_remove_work_dir_keeps_dir_when_opted_out(
    tmp_path: pathlib.Path, flag: str,
) -> None:
    work = tmp_path / "optout_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf", **{flag: True})

    maybe_remove_work_dir(args, result=True)

    assert work.exists()


def test_maybe_remove_work_dir_skips_when_output_inside(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    work = tmp_path / "out_work"
    work.mkdir()
    output_inside = work / "out.vcf"
    args = _work_dir_args(work, output_inside)

    with caplog.at_level("WARNING"):
        maybe_remove_work_dir(args, result=True)

    assert work.exists()
    assert len(caplog.records) == 1
    assert "working directory" in caplog.records[0].getMessage()


def test_maybe_remove_work_dir_rmtree_error_warns_not_raises(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    mocker: pytest_mock.MockerFixture,
) -> None:
    work = tmp_path / "locked_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf")
    mocker.patch(
        "gain.task_graph.work_dir.shutil.rmtree",
        side_effect=OSError("device busy"))

    with caplog.at_level("WARNING"):
        maybe_remove_work_dir(args, result=True)  # must not raise

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "device busy" in warnings[0].getMessage()


def test_maybe_remove_work_dir_logs_info_on_success(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    work = tmp_path / "out_work"
    work.mkdir()
    args = _work_dir_args(work, tmp_path / "out.vcf")

    with caplog.at_level("INFO"):
        maybe_remove_work_dir(args, result=True)

    infos = [r for r in caplog.records if r.levelname == "INFO"]
    assert len(infos) == 1
    assert "removed working directory" in infos[0].getMessage()


def test_absolutize_path_args_uses_the_tools_own_input_key(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # binning_tool has no ``input``; its primary input is the run
    # definition, and the tool names the key.
    monkeypatch.chdir(tmp_path)
    args = {"run_definition": "run.yaml", "output": "bins.h5"}

    absolutize_path_args(args, input_key="run_definition")

    assert args["run_definition"] == str(tmp_path / "run.yaml")
    assert args["output"] == str(tmp_path / "bins.h5")


def test_absolutize_path_args_leaves_empty_paths_alone() -> None:
    args = {"input": "in.vcf", "task_status_dir": None, "work_dir": ""}

    absolutize_path_args(args, input_key="input")

    assert args["task_status_dir"] is None
    assert args["work_dir"] == ""


def test_absolutize_path_args_takes_the_genomic_context_keys_from_the_caller(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    args = {"input": "in.vcf", "task_log_dir": "logs", "grr_directory": "grr"}

    absolutize_path_args(
        args, input_key="input", extra_keys=("grr_directory",))

    assert args["task_log_dir"] == str(tmp_path / "logs")
    assert args["grr_directory"] == str(tmp_path / "grr")


def test_absolutize_path_args_knows_no_genomic_context_keys_of_its_own(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    args = {"input": "in.vcf", "grr_directory": "grr", "grr_filename": "g"}

    absolutize_path_args(args, input_key="input")

    assert args["grr_directory"] == "grr"
    assert args["grr_filename"] == "g"


def test_apply_work_dir_defaults_puts_the_task_dirs_in_an_absolute_work_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A relative output, not absolutized first: the work dir it derives
    # is still absolute, so a later chdir into it cannot lose the task
    # status and log directories.
    monkeypatch.chdir(tmp_path)
    args: dict[str, object] = {"output": "bins.h5"}

    apply_work_dir_defaults(args)

    assert args["work_dir"] == str(tmp_path / "bins_work")
    assert args["work_dir_created"] is True
    assert (tmp_path / "bins_work").is_dir()
    assert args["task_status_dir"] == str(
        tmp_path / "bins_work" / ".task-status")
    assert args["task_log_dir"] == str(tmp_path / "bins_work" / ".task-log")


def test_apply_work_dir_defaults_absolutizes_explicit_relative_dirs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit relative directories, not absolutized first: the step
    # itself resolves them, since the tool chdirs into the work dir next.
    monkeypatch.chdir(tmp_path)
    args: dict[str, object] = {
        "output": "out.vcf",
        "work_dir": "my_work",
        "task_status_dir": "status",
    }

    apply_work_dir_defaults(args)

    assert args["work_dir"] == str(tmp_path / "my_work")
    assert args["task_status_dir"] == str(tmp_path / "status")
    assert args["task_log_dir"] == str(tmp_path / "my_work" / ".task-log")


def test_apply_work_dir_defaults_creates_a_nested_work_dir_with_its_parents(
    tmp_path: pathlib.Path,
) -> None:
    work_dir = tmp_path / "deep" / "nested" / "work"
    args: dict[str, object] = {
        "output": str(tmp_path / "out.vcf"),
        "work_dir": str(work_dir),
    }

    apply_work_dir_defaults(args)

    assert work_dir.is_dir()
    assert args["work_dir_created"] is True
