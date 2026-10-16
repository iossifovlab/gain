# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
import logging
import pathlib
import textwrap
from collections.abc import Iterator
from typing import Any
from unittest.mock import DEFAULT

import gain.dask
import gain.dask.named_cluster
import pytest
import pytest_mock
import yaml
from dask import config
from gain.dask.named_cluster import setup_client, setup_client_from_config


@pytest.fixture
def named_cluster_config() -> dict[str, Any]:
    return {
        "distributed": {
            "scheduler": {
                "unknown-task-duration": "5m",
            },
            "worker": {
                "memory": {
                    "pause": 0.99,
                },
            },
        },
        "default": "local",
        "clusters": {
            "local": {
                "type": "local",
                "params": {
                    "memory_limit": "4GB",
                    "threads_per_worker": 2,
                },
            },
        },
    }


def test_local_cluster_setup_silences_distributed_info_logs(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    """Building the local cluster must keep dask's ``distributed`` logger
    silenced at the default verbosity.

    Regression for iossifovlab/gain#123: ``VerbosityConfiguration`` silences
    ``distributed`` to WARNING at CLI startup, but importing ``distributed``
    (which happens lazily when the cluster is built) resets that logger back
    to INFO via dask's own ``initialize_logging``. The resulting INFO spam
    (e.g. ``Scheduler closing all comms``) then leaks to stderr even with no
    ``-v`` flag. The cluster setup must re-assert the silencing.
    """
    mocker.patch("dask.distributed.LocalCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)

    dist_logger = logging.getLogger("distributed")
    old_level = dist_logger.level
    try:
        # Simulate the post-import clobber: distributed back at INFO.
        dist_logger.setLevel(logging.INFO)
        assert logging.getLogger("distributed.scheduler").isEnabledFor(
            logging.INFO)

        with config.set({"gain_named_cluster": named_cluster_config}):
            client, _ = setup_client()
            assert client

        # After cluster setup, distributed INFO records are suppressed again.
        assert not logging.getLogger("distributed.scheduler").isEnabledFor(
            logging.INFO)
    finally:
        dist_logger.setLevel(old_level)


def test_default(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)

    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    with config.set({"gain_named_cluster": named_cluster_config}):
        client, _ = setup_client()
        assert client

    assert mocked_local.call_count == 1
    assert mocked_local.call_args.kwargs == {
        "memory_limit": "4GB",
        "threads_per_worker": 1,
    }


def test_default_with_number_of_workers(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    with config.set({"gain_named_cluster": named_cluster_config}):
        client, _ = setup_client(number_of_workers=4)
        assert client

    assert mocked_local.call_count == 1
    assert mocked_local.call_args.kwargs["n_workers"] == 4


@pytest.mark.parametrize(
    "config_update, expected_n",
    [
        ({"number_of_threads": 6}, 6),
        ({"number_of_workers": 6}, 6),
    ],
)
def test_default_with_number_of_workers_config(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
    config_update: dict[str, Any],
    expected_n: int,
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", new=DEFAULT)

    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    named_cluster_config["clusters"]["local"].update(config_update)

    with config.set({"gain_named_cluster": named_cluster_config}):
        client, _ = setup_client()
        assert client

    assert mocked_local.call_count == 1
    assert mocked_local.call_args.kwargs == {
        "memory_limit": "4GB", "threads_per_worker": 1}

    assert mocked_local.return_value.scale.call_count == 1
    assert mocked_local.return_value.scale.call_args.kwargs["n"] == expected_n


def test_config_access(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)
    with config.set({"gain_named_cluster": named_cluster_config}):
        client, _ = setup_client()
        assert client

    assert mocked_local.call_count == 1
    assert mocked_local.call_args.kwargs == {
        "memory_limit": "4GB",
        "threads_per_worker": 1,
    }


def test_setup_cluster_from_config_simple(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocked_factory = mocker.patch(
        "gain.dask.named_cluster.set_up_local_cluster", autospec=True)
    mocker.patch.dict(
        gain.dask.named_cluster._CLUSTER_TYPES, {
            "local": mocked_factory,
        },
    )
    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    setup_client_from_config({
        "type": "local",
    }, number_of_workers=2)

    # pylint: disable=no-member
    gain.dask.named_cluster\
        .set_up_local_cluster.assert_called_once_with({  # type: ignore
            "number_of_workers": 2,
        })


def test_setup_sge_cluster(mocker: pytest_mock.MockerFixture) -> None:
    mocked_sge = mocker.patch(
        "dask_jobqueue.SGECluster", autospec=True)

    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    setup_client_from_config({
        "type": "sge",
    }, number_of_workers=2)

    assert mocked_sge.call_count == 1
    assert "number_of_workers" not in mocked_sge.call_args.kwargs


def test_setup_slurm_cluster(mocker: pytest_mock.MockerFixture) -> None:
    mocked_slurm = mocker.patch(
        "dask_jobqueue.SLURMCluster", autospec=True)

    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    setup_client_from_config({
        "type": "slurm",
    }, number_of_workers=2)

    assert mocked_slurm.call_count == 1
    assert "number_of_workers" not in mocked_slurm.call_args.kwargs


@pytest.mark.parametrize("cluster_type", ["kubernetes", "kubgernetes"])
def test_unknown_cluster_type_is_refused_by_name(
    mocker: pytest_mock.MockerFixture,
    cluster_type: str,
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    with pytest.raises(ValueError, match=cluster_type) as excinfo:
        setup_client_from_config({"type": cluster_type})

    assert "local, manual, sge, slurm" in str(excinfo.value)
    assert mocked_client.call_count == 0


def test_bundled_config_names_only_supported_cluster_types() -> None:
    bundled = pathlib.Path(gain.dask.__file__).parent / "named_cluster.yaml"
    named_clusters = yaml.safe_load(bundled.read_text())["gain_named_cluster"]
    supported = {"manual", *gain.dask.named_cluster._CLUSTER_TYPES}

    assert isinstance(named_clusters["clusters"], dict)
    assert all(
        isinstance(conf, dict) and "name" not in conf
        for conf in named_clusters["clusters"].values())
    types = {
        name: conf["type"]
        for name, conf in named_clusters["clusters"].items()}

    assert named_clusters["default"] in types
    assert {
        name: cluster_type
        for name, cluster_type in types.items()
        if cluster_type not in supported
    } == {}


def test_setup_manual_client(mocker: pytest_mock.MockerFixture) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    setup_client_from_config({
        "type": "manual",
        "params": {
            "address": "tcp://localhost:8786",
        },
    }, number_of_workers=2)

    assert mocked_client.call_count == 1

    assert mocked_client.call_args.kwargs == {
        "address": "tcp://localhost:8786",
    }


def test_adjust_default_distributed_config(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    client, _ = setup_client(number_of_workers=4)
    assert client

    assert config.get(
        "distributed.scheduler.unknown-task-duration") == "10m"
    assert config.get(
        "distributed.worker.memory.pause") is False


@pytest.fixture
def restored_dask_config(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    saved = copy.deepcopy(config.config)
    monkeypatch.setattr(gain.dask.config, "_deferred_error", None)
    try:
        yield
    finally:
        config.config.clear()
        config.config.update(saved)


def _load_user_config(config_dir: pathlib.Path, content: str) -> None:
    (config_dir / "named_cluster.yaml").write_text(textwrap.dedent(content))
    config.refresh(paths=[str(config_dir)])


OLD_SHAPE = """\
    dae_named_cluster:
      default: my_sge
      clusters:
        - name: my_sge
          type: sge
          number_of_workers: 7
          params:
            memory: 3GB
            cores: 2
        - name: local
          type: local
          params:
            memory_limit: 1GB
    """


def test_old_key_is_translated_to_the_named_clusters(
    mocker: pytest_mock.MockerFixture,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocked_sge = mocker.patch("dask_jobqueue.SGECluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _load_user_config(tmp_path, OLD_SHAPE)

    with caplog.at_level(logging.WARNING, logger="gain.dask.config"):
        gain.dask.config.translate_legacy_named_cluster()
    _, cluster_config = setup_client("my_sge")

    assert cluster_config == {
        "type": "sge",
        "number_of_workers": 7,
        "params": {"memory": "3GB", "cores": 2},
    }
    assert mocked_sge.call_args.kwargs == {"memory": "3GB", "cores": 2}
    assert config.get("gain_named_cluster.default") == "my_sge"
    warnings = [
        record.getMessage() for record in caplog.records
        if "dae_named_cluster" in record.getMessage()]
    assert len(warnings) == 1
    assert "2026.11.0" in warnings[0]


def test_old_key_keeps_the_bundled_distributed_values(
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    _load_user_config(tmp_path, OLD_SHAPE)

    gain.dask.config.translate_legacy_named_cluster()

    assert config.get(
        "gain_named_cluster.distributed.scheduler.unknown-task-duration",
        default=None,
    ) == "10m"


def test_old_key_is_translated_once(
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _load_user_config(tmp_path, OLD_SHAPE)

    with caplog.at_level(logging.WARNING, logger="gain.dask.config"):
        gain.dask.config.translate_legacy_named_cluster()
        gain.dask.config.translate_legacy_named_cluster()

    assert len([
        record for record in caplog.records
        if "dae_named_cluster" in record.getMessage()]) == 1
    assert config.get("dae_named_cluster", default=None) is None


def test_reconfigure_translates_the_old_key(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # reconfigure() runs at the import of gain.dask. Point the dask config
    # directory at tmp_path, so ensure_file() finds the old user file there
    # and never writes to the real dask config directory.
    monkeypatch.setattr(config, "PATH", str(tmp_path))
    mocker.patch("dask_jobqueue.SGECluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _load_user_config(tmp_path, OLD_SHAPE)

    with caplog.at_level(logging.WARNING, logger="gain.dask.config"):
        gain.dask.config.reconfigure()
    _, cluster_config = setup_client("my_sge")

    assert cluster_config == {
        "type": "sge",
        "number_of_workers": 7,
        "params": {"memory": "3GB", "cores": 2},
    }
    assert len([
        record for record in caplog.records
        if "dae_named_cluster" in record.getMessage()]) == 1


def test_old_key_value_wins_over_the_bundled_value(
    mocker: pytest_mock.MockerFixture,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _load_user_config(tmp_path, OLD_SHAPE)
    assert config.get(
        "gain_named_cluster.clusters.local.params.memory_limit") == "10GB"

    gain.dask.config.translate_legacy_named_cluster()
    setup_client("local")

    assert mocked_local.call_args.kwargs["memory_limit"] == "1GB"


def test_old_key_merges_into_the_hyphenated_new_key(
    mocker: pytest_mock.MockerFixture,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocker.patch("dask.distributed.LocalCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    (tmp_path / "named_cluster.yaml").write_text(textwrap.dedent("""\
        gain-named-cluster:
          clusters:
            mine:
              type: local
        dae_named_cluster:
          clusters:
            - name: old
              type: local
        """))
    # The order of an import of gain.dask: dask collects the user files,
    # then reconfigure() merges the bundled defaults into the spelling
    # that the user file uses. config.refresh() would respell the key.
    bundled = yaml.safe_load(
        (pathlib.Path(gain.dask.config.__file__).parent
         / "named_cluster.yaml").read_text())
    collected = config.collect(paths=[str(tmp_path)], env={})
    config.update_defaults(bundled, config=collected, defaults=[])
    config.update_defaults(
        {"distributed": bundled["gain_named_cluster"]["distributed"]},
        config=collected, defaults=[])
    config.config.clear()
    config.config.update(collected)

    gain.dask.config.translate_legacy_named_cluster()

    assert sorted(
        key for key in config.config
        if key.replace("-", "_") == "gain_named_cluster") == [
        "gain-named-cluster"]
    clusters = config.get("gain_named_cluster.clusters")
    assert {"mine", "old", "local"} <= set(clusters)
    assert config.get("gain_named_cluster.default") == "local"
    assert config.get(
        "gain_named_cluster.distributed.scheduler.unknown-task-duration",
    ) == "10m"
    _, cluster_config = setup_client("old")
    assert cluster_config == {"type": "local"}


def test_list_of_named_clusters_is_refused(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    named_cluster_config["clusters"] = [
        {"name": "local", "type": "local"},
    ]

    with config.set({"gain_named_cluster": named_cluster_config}), \
            pytest.raises(ValueError, match="<name>:") as excinfo:
        setup_client("local")

    assert "gain_named_cluster.clusters" in str(excinfo.value)
    assert mocked_client.call_count == 0


def test_name_key_in_a_cluster_definition_is_refused(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    named_cluster_config["clusters"]["local"]["name"] = "local"

    with config.set({"gain_named_cluster": named_cluster_config}), \
            pytest.raises(ValueError, match="'name' key") as excinfo:
        setup_client("local")

    assert "'local'" in str(excinfo.value)
    assert mocked_client.call_count == 0


def test_unknown_named_cluster_is_refused_with_the_defined_names(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    named_cluster_config["clusters"]["sge_big"] = {"type": "sge"}

    with config.set({"gain_named_cluster": named_cluster_config}), \
            pytest.raises(ValueError, match="'foo'") as excinfo:
        setup_client("foo")

    assert "local, sge_big" in str(excinfo.value)
    assert mocked_client.call_count == 0


def test_old_cluster_definition_replaces_the_bundled_one_of_that_name(
    mocker: pytest_mock.MockerFixture,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocker.patch("dask_jobqueue.SGECluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _load_user_config(tmp_path, """\
        dae_named_cluster:
          clusters:
            - name: sge
              type: sge
              adapt_params:
                minimum: 1
                maximum: 50
              params:
                queue: all.q
        """)
    assert config.get(
        "gain_named_cluster.clusters.sge.number_of_workers") == 150

    gain.dask.config.translate_legacy_named_cluster()
    _, cluster_config = setup_client("sge")

    assert cluster_config == {
        "type": "sge",
        "adapt_params": {"minimum": 1, "maximum": 50},
        "params": {"queue": "all.q"},
    }


def test_list_of_named_clusters_in_a_user_file_is_refused(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    # The user file renames the old key but keeps the old list shape. The
    # bundled defaults that reconfigure() merges must not hide the list.
    monkeypatch.setattr(config, "PATH", str(tmp_path))
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    _load_user_config(tmp_path, """\
        gain_named_cluster:
          clusters:
            - name: local
              type: local
        """)

    gain.dask.config.reconfigure()
    with pytest.raises(ValueError, match="<name>:") as excinfo:
        setup_client("local")

    assert "gain_named_cluster.clusters" in str(excinfo.value)
    assert mocked_client.call_count == 0


@pytest.mark.parametrize("content", [
    "gain_named_cluster:\n  - name: local\n    type: local\n",
    "gain_named_cluster: local\n",
])
def test_non_mapping_new_key_in_a_user_file_is_refused(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    content: str,
) -> None:
    # The user file puts the old list directly under the new key. The
    # bundled defaults that reconfigure() merges must not replace it.
    monkeypatch.setattr(config, "PATH", str(tmp_path))
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    (tmp_path / "named_cluster.yaml").write_text(content)
    config.refresh(paths=[str(tmp_path)])

    gain.dask.config.reconfigure()
    with pytest.raises(
            ValueError, match="'gain_named_cluster' must be a mapping"):
        setup_client("local")

    assert mocked_client.call_count == 0


@pytest.mark.parametrize("legacy_clusters, message", [
    ("\n    - type: sge", "has no 'name' key"),
    ("\n    my_sge:\n      type: sge", "must be a list"),
    ("\n    -", "must be a mapping"),
])
def test_malformed_old_key_is_refused_when_named_clusters_are_read(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    legacy_clusters: str,
    message: str,
) -> None:
    # A malformed old key must not abort the import of gain.dask: a run
    # with -c never reads the named clusters.
    monkeypatch.setattr(config, "PATH", str(tmp_path))
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    (tmp_path / "named_cluster.yaml").write_text(
        "dae_named_cluster:\n  clusters:" + legacy_clusters + "\n")
    config.refresh(paths=[str(tmp_path)])

    gain.dask.config.reconfigure()
    with pytest.raises(ValueError, match=message) as excinfo:
        setup_client("my_sge")

    assert "dae_named_cluster.clusters" in str(excinfo.value)
    assert mocked_client.call_count == 0


@pytest.mark.parametrize("content, message", [
    ("dae_named_cluster:\n  - name: my_sge\n    type: sge\n",
     "'dae_named_cluster' must be a mapping"),
    ("dae_named_cluster: sge\n", "'dae_named_cluster' must be a mapping"),
])
def test_non_mapping_old_key_is_refused_when_named_clusters_are_read(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    content: str,
    message: str,
) -> None:
    # An old key that is not a mapping must not abort the import of
    # gain.dask either.
    monkeypatch.setattr(config, "PATH", str(tmp_path))
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    (tmp_path / "named_cluster.yaml").write_text(content)
    config.refresh(paths=[str(tmp_path)])

    gain.dask.config.reconfigure()
    with pytest.raises(ValueError, match=message):
        setup_client("my_sge")

    assert mocked_client.call_count == 0


def test_translation_refuses_a_non_mapping_new_key_next_to_the_old_key(
    restored_dask_config: None,
) -> None:
    config.config.clear()
    config.config.update({
        "gain_named_cluster": ["local"],
        "dae_named_cluster": {
            "clusters": [{"name": "my_sge", "type": "sge"}]},
    })

    error = gain.dask.config.translate_legacy_named_cluster()

    assert isinstance(error, ValueError)
    assert "'gain_named_cluster' must be a mapping" in str(error)
    assert "dae_named_cluster" not in config.config
