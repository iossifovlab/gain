# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
import logging
import pathlib
import re
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", new=DEFAULT)

    mocker.patch(
        "gain.dask.named_cluster.Client",
        autospec=True)

    named_cluster_config["clusters"]["local"].update(config_update)

    with config.set({"gain_named_cluster": named_cluster_config}), \
            caplog.at_level(logging.WARNING, logger="gain.dask.named_cluster"):
        client, _ = setup_client()
        assert client

    assert mocked_local.call_count == 1
    assert mocked_local.call_args.kwargs == {
        "memory_limit": "4GB", "threads_per_worker": 1}

    assert mocked_local.return_value.scale.call_count == 1
    assert mocked_local.return_value.scale.call_args.kwargs["n"] == expected_n
    deprecation_warned = any(
        "'number_of_threads' key" in record.getMessage()
        for record in caplog.records)
    assert deprecation_warned == ("number_of_threads" in config_update)


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


def test_local_params_get_the_call_argument_not_the_definition_count(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocked_local = mocker.patch("dask.distributed.LocalCluster", new=DEFAULT)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    cluster_config: dict[str, Any] = {
        "type": "local", "params": {}, "number_of_workers": 3}

    setup_client_from_config(cluster_config)

    assert cluster_config == {
        "type": "local", "params": {}, "number_of_workers": 3}
    assert mocked_local.return_value.scale.call_args.kwargs["n"] == 3


@pytest.mark.parametrize("cluster_type, cluster_class", [
    ("sge", "dask_jobqueue.SGECluster"),
    ("slurm", "dask_jobqueue.SLURMCluster"),
])
def test_jobqueue_types_keep_the_cluster_definition_unchanged(
    mocker: pytest_mock.MockerFixture,
    cluster_type: str,
    cluster_class: str,
) -> None:
    mocker.patch(cluster_class, autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    cluster_config: dict[str, Any] = {
        "type": cluster_type,
        "params": {"cores": 4, "processes": 2, "number_of_workers": 5}}

    setup_client_from_config(cluster_config, number_of_workers=2)

    assert cluster_config == {
        "type": cluster_type,
        "params": {"cores": 4, "processes": 2, "number_of_workers": 5}}


def test_local_warns_on_threads_per_worker_even_when_it_is_one(
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocked_local = mocker.patch("dask.distributed.LocalCluster", new=DEFAULT)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)

    with caplog.at_level(logging.WARNING, logger="gain.dask.named_cluster"):
        setup_client_from_config({
            "type": "local", "params": {"threads_per_worker": 1}})

    assert "'threads_per_worker' key" in caplog.text
    assert mocked_local.call_args.kwargs["threads_per_worker"] == 1


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


def test_bundled_config_uses_no_deprecated_number_of_threads() -> None:
    bundled = pathlib.Path(gain.dask.__file__).parent / "named_cluster.yaml"
    clusters = yaml.safe_load(bundled.read_text())["gain_named_cluster"][
        "clusters"]

    assert [
        name for name, conf in clusters.items()
        if "number_of_threads" in conf
    ] == []
    assert clusters["slurm"]["number_of_workers"] == 10


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


def test_manual_named_cluster_sets_up_twice_with_the_same_address(
    mocker: pytest_mock.MockerFixture,
    named_cluster_config: dict[str, Any],
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    named_cluster_config["clusters"]["remote"] = {
        "type": "manual",
        "params": {"address": "tcp://localhost:8786"},
    }

    with config.set({"gain_named_cluster": named_cluster_config}):
        setup_client("remote")
        setup_client("remote")

    assert [call.kwargs for call in mocked_client.call_args_list] == [
        {"address": "tcp://localhost:8786"},
        {"address": "tcp://localhost:8786"},
    ]


@pytest.fixture
def mocked_cluster_classes(mocker: pytest_mock.MockerFixture) -> None:
    mocker.patch("dask.distributed.LocalCluster", autospec=True)
    mocker.patch("dask_jobqueue.SGECluster", autospec=True)
    mocker.patch("dask_jobqueue.SLURMCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)


CLUSTER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "local": {
        "type": "local",
        "number_of_workers": 3,
        "params": {"memory_limit": "4GB", "threads_per_worker": 2},
    },
    "sge": {
        "type": "sge",
        "number_of_workers": 3,
        "params": {"cores": 4, "processes": 2, "number_of_workers": 5},
    },
    "slurm": {
        "type": "slurm",
        "adapt_params": {"minimum": 1, "maximum": 5},
        "params": {"cores": 4, "processes": 2, "number_of_workers": 5},
    },
    "manual": {
        "type": "manual",
        "params": {"address": "tcp://localhost:8786", "timeout": 5},
    },
}


@pytest.mark.parametrize("cluster_type", sorted(CLUSTER_DEFINITIONS))
def test_setup_client_from_config_keeps_the_mapping_unchanged(
    mocked_cluster_classes: None,
    cluster_type: str,
) -> None:
    cluster_config = copy.deepcopy(CLUSTER_DEFINITIONS[cluster_type])

    _, returned = setup_client_from_config(
        cluster_config, number_of_workers=2)

    assert cluster_config == CLUSTER_DEFINITIONS[cluster_type]
    assert returned == CLUSTER_DEFINITIONS[cluster_type]
    assert returned is not cluster_config


@pytest.mark.parametrize("cluster_type", sorted(CLUSTER_DEFINITIONS))
def test_setup_client_keeps_the_dask_config_unchanged(
    mocked_cluster_classes: None,
    named_cluster_config: dict[str, Any],
    cluster_type: str,
) -> None:
    named_cluster_config["clusters"] = copy.deepcopy(CLUSTER_DEFINITIONS)

    with config.set({"gain_named_cluster": named_cluster_config}):
        before = copy.deepcopy(config.get("gain_named_cluster"))
        setup_client(cluster_type, number_of_workers=2)
        after = config.get("gain_named_cluster")

    assert after == before


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


def _import_user_config(
    monkeypatch: pytest.MonkeyPatch,
    config_dir: pathlib.Path,
    content: str,
) -> None:
    """Load a user file in the order of an import of gain.dask.

    dask collects the user files, then reconfigure() merges the bundled
    defaults under them. config.refresh() would merge the bundled defaults
    first.
    """
    monkeypatch.setattr(config, "PATH", str(config_dir))
    monkeypatch.setattr(config, "defaults", [])
    (config_dir / "named_cluster.yaml").write_text(textwrap.dedent(content))
    config.config.clear()
    config.config.update(config.collect(paths=[str(config_dir)], env={}))
    gain.dask.config.reconfigure()


def test_user_number_of_threads_wins_over_the_bundled_number_of_workers(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocked_slurm = mocker.patch("dask_jobqueue.SLURMCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, """\
        gain_named_cluster:
          clusters:
            slurm:
              number_of_threads: 50
        """)

    with caplog.at_level(logging.WARNING, logger="gain.dask.named_cluster"):
        setup_client("slurm")

    assert mocked_slurm.return_value.scale.call_args.kwargs["n"] == 50
    assert any(
        "'number_of_threads' key" in record.getMessage()
        for record in caplog.records)


def test_user_null_number_of_threads_clears_the_bundled_number_of_workers(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_slurm = mocker.patch("dask_jobqueue.SLURMCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, """\
        gain_named_cluster:
          clusters:
            slurm:
              number_of_threads: null
              adapt_params:
                minimum: 1
                maximum: 20
        """)

    setup_client("slurm")

    assert mocked_slurm.return_value.scale.call_count == 0
    assert mocked_slurm.return_value.adapt.call_args.kwargs == {
        "minimum": 1, "maximum": 20}


def test_user_number_of_workers_wins_over_the_user_number_of_threads(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_slurm = mocker.patch("dask_jobqueue.SLURMCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, """\
        gain_named_cluster:
          clusters:
            slurm:
              number_of_threads: 50
              number_of_workers: 3
        """)

    setup_client("slurm")

    assert mocked_slurm.return_value.scale.call_args.kwargs["n"] == 3


def test_user_number_of_threads_under_the_hyphenated_key_wins(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_slurm = mocker.patch("dask_jobqueue.SLURMCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, """\
        gain-named-cluster:
          clusters:
            slurm:
              number_of_threads: 50
        """)

    setup_client("slurm")

    assert mocked_slurm.return_value.scale.call_args.kwargs["n"] == 50


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


def _assert_old_key_refusal(refusal: BaseException) -> None:
    message = str(refusal)
    assert "dae_named_cluster" in message
    assert "gain_named_cluster" in message
    assert gain.dask.config.NAMED_CLUSTERS_DOCS_URL in message
    assert re.search(r"\d{4}\.\d+\.\d+", message) is None


def test_old_key_is_refused_when_named_clusters_are_read(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, OLD_SHAPE)

    with pytest.raises(ValueError, match="dae_named_cluster") as excinfo:
        setup_client("my_sge")

    _assert_old_key_refusal(excinfo.value)
    assert config.get("gain_named_cluster.default") == "local"
    assert mocked_client.call_count == 0


@pytest.mark.parametrize("content", [
    pytest.param(
        "dae-named-cluster:\n  clusters:\n    - name: my_sge\n"
        "      type: sge\n",
        id="hyphenated-spelling"),
    pytest.param(
        "dae_named_cluster:\n  clusters:\n    - type: sge\n",
        id="entry-without-name"),
    pytest.param(
        "dae_named_cluster:\n  clusters:\n    my_sge:\n      type: sge\n",
        id="clusters-not-a-list"),
    pytest.param(
        "dae_named_cluster:\n  clusters:\n    -\n",
        id="entry-not-a-mapping"),
    pytest.param(
        "dae_named_cluster:\n  - name: my_sge\n    type: sge\n",
        id="list-value"),
    pytest.param("dae_named_cluster: sge\n", id="string-value"),
    pytest.param("dae_named_cluster: {}\n", id="empty-mapping"),
    pytest.param("dae_named_cluster: null\n", id="null"),
])
def test_any_value_of_the_old_key_is_refused(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    content: str,
) -> None:
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, content)

    with pytest.raises(ValueError, match="dae_named_cluster") as excinfo:
        setup_client("my_sge")

    _assert_old_key_refusal(excinfo.value)
    assert mocked_client.call_count == 0


def test_malformed_new_key_is_refused_before_the_old_key(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, """\
        gain_named_cluster: local
        dae_named_cluster:
          default: my_sge
        """)

    with pytest.raises(
            ValueError, match="'gain_named_cluster' must be a mapping"):
        setup_client("local")


def test_old_key_does_not_stop_a_run_with_a_cluster_config(
    mocker: pytest_mock.MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    mocked_local = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocked_client = mocker.patch(
        "gain.dask.named_cluster.Client", autospec=True)
    _import_user_config(monkeypatch, tmp_path, OLD_SHAPE)
    assert gain.dask.config._deferred_error is not None

    client, _ = setup_client_from_config({"type": "local"})

    assert client is mocked_client.return_value
    assert mocked_local.call_count == 1


def test_old_key_logs_no_warning_and_keeps_the_user_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_file = tmp_path / "named_cluster.yaml"

    with caplog.at_level(logging.DEBUG):
        _import_user_config(monkeypatch, tmp_path, OLD_SHAPE)

    assert not [
        record for record in caplog.records
        if "dae_named_cluster" in record.getMessage()]
    assert user_file.read_bytes() == textwrap.dedent(OLD_SHAPE).encode()


def test_config_without_the_old_key_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    restored_dask_config: None,
) -> None:
    _import_user_config(monkeypatch, tmp_path, """\
        gain_named_cluster:
          default: sge
        """)

    assert gain.dask.config._deferred_error is None
