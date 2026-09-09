# pylint: disable=W0621,C0114,C0116,W0212,W0613,C0413,C0415
"""Unit tests for the standalone SpliceAI contention probe (#1275).

The probe lives in ``scripts/`` rather than the package: it is a diagnostic
tool, not product code, and #1275 forbids changing any shipped default. It is
therefore imported here by path, the way a user would run it.

Nothing in this module may import TensorFlow or ONNX Runtime at collection
time -- ``test_importing_the_probe_does_not_import_tensorflow`` pins that, and
it is the reason the probe loads its runtime lazily inside the worker.
"""
import pathlib
import subprocess
import sys

import numpy as np
import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import spliceai_contention_probe as probe  # ruff: ignore[module-import-not-at-top-of-file]


def test_digest_output_distinguishes_a_single_ulp_difference() -> None:
    baseline = np.array([[0.1, 0.2, 0.7]], dtype=np.float32)
    drifted = baseline.copy()
    drifted[0, 0] = np.nextafter(drifted[0, 0], np.float32(1.0))

    assert probe.digest_output(baseline) != probe.digest_output(drifted)


def test_digest_output_distinguishes_arrays_that_differ_only_in_shape() -> None:
    values = np.arange(6, dtype=np.float32)

    assert probe.digest_output(values.reshape(2, 3)) \
        != probe.digest_output(values.reshape(3, 2))


def test_probe_predictions_reports_every_answer_when_it_drifts(
) -> None:
    """Positive control: a clean sweep is only meaningful if drift is visible.

    Feeds a predict that returns a slightly different answer each call, the
    shape a load-dependent runtime produces, and pins that the probe reports
    both.
    """
    answers = iter([
        np.array([0.5], dtype=np.float32),
        np.array([0.5000001], dtype=np.float32),
    ])

    digests = probe.probe_predictions(
        lambda _x: next(answers), np.zeros((1, 1), np.float32), iterations=2)

    assert len(set(digests)) == 2


def test_probe_predictions_reports_one_answer_when_stable() -> None:
    digests = probe.probe_predictions(
        lambda _x: np.array([0.5], dtype=np.float32),
        np.zeros((1, 1), np.float32), iterations=4)

    assert len(set(digests)) == 1


def test_summarise_run_counts_one_distinct_output_when_workers_agree() -> None:
    summary = probe.summarise_run(
        processes=3, threads=4,
        worker_digests=[("a", "a"), ("a", "a"), ("a", "a")])

    assert summary.distinct_outputs == 1
    assert summary.total_predictions == 6


def test_summarise_run_flags_the_worker_whose_own_answer_varied() -> None:
    summary = probe.summarise_run(
        processes=3, threads=None,
        worker_digests=[("a", "a"), ("a", "b"), ("a", "a")])

    assert summary.per_worker_distinct == (1, 2, 1)
    assert summary.distinct_outputs == 2


def test_summarise_run_counts_stable_workers_that_disagree() -> None:
    summary = probe.summarise_run(
        processes=2, threads=4, worker_digests=[("a", "a"), ("b", "b")])

    assert summary.per_worker_distinct == (1, 1)
    assert summary.distinct_outputs == 2


def test_summarise_sweep_reports_first_divergent_k_per_thread_setting() -> None:
    runs = [
        probe.summarise_run(1, None, [("a",)]),
        probe.summarise_run(4, None, [("a",), ("b",)]),
        probe.summarise_run(8, None, [("a",), ("c",)]),
        probe.summarise_run(1, 1, [("a",)]),
        probe.summarise_run(4, 1, [("a",), ("a",)]),
    ]

    sweep = probe.summarise_sweep(runs)

    assert sweep.first_divergent_processes == {None: 4, 1: None}


def test_fixed_input_is_identical_across_calls_for_one_seed() -> None:
    """Two calls in one process agree.

    Agreement *between* processes is not shown here -- it cannot be, in a
    single interpreter. That is why every worker reports a digest of the
    input it actually used and `distinct_inputs` invalidates the point when
    they disagree.
    """
    assert probe.digest_output(probe.fixed_input(width=64, seed=7)) \
        == probe.digest_output(probe.fixed_input(width=64, seed=7))


def test_fixed_input_is_a_one_hot_window_of_the_requested_width() -> None:
    x = probe.fixed_input(width=64, seed=7)

    assert x.shape == (1, 64, 4)
    assert (x.sum(axis=2) == 1).all()


_AVX2_CPUINFO = """\
processor\t: 0
model name\t: AMD Ryzen 9 3950X 16-Core Processor
flags\t\t: fpu vme de pse avx avx2 fma
"""

_AVX512_CPUINFO = """\
processor\t: 0
model name\t: Intel(R) Core(TM) i5-11600K
flags\t\t: fpu vme de pse avx avx2 avx512f avx512dq
"""


def test_read_host_info_reads_the_cpu_model_name() -> None:
    host = probe.read_host_info(
        cpuinfo=_AVX2_CPUINFO, hostname="piglet", logical_cpus=32,
        load_average=(2.0, 1.0, 0.5))

    assert host.cpu_model == "AMD Ryzen 9 3950X 16-Core Processor"


def test_read_host_info_detects_avx512_when_the_cpu_has_it() -> None:
    host = probe.read_host_info(
        cpuinfo=_AVX512_CPUINFO, hostname="eyoree", logical_cpus=12,
        load_average=(0.1, 0.1, 0.1))

    assert host.avx512 is True


def test_read_host_info_reports_no_avx512_on_an_avx2_only_cpu() -> None:
    host = probe.read_host_info(
        cpuinfo=_AVX2_CPUINFO, hostname="piglet", logical_cpus=32,
        load_average=(2.0, 1.0, 0.5))

    assert host.avx512 is False


def test_importing_the_probe_does_not_import_a_model_runtime() -> None:
    """The parent must reach the workers with the runtime uninitialised.

    Thread settings only take effect before a runtime initialises. If merely
    importing the probe pulled TensorFlow in, the parent would carry an
    initialised runtime into every worker and `--threads` would silently do
    nothing -- the sweep's second axis would be a fiction.
    """
    probe_import = subprocess.run(
        [sys.executable, "-c",
         ("import sys; sys.path.insert(0, sys.argv[1]);"
          "import spliceai_contention_probe;"
          "print('tensorflow' in sys.modules,"
          " 'onnxruntime' in sys.modules)"),
         str(_SCRIPTS)],
        capture_output=True, text=True, check=True)

    assert probe_import.stdout.strip() == "False False"


def test_summarise_run_carries_the_threads_the_runtime_reported() -> None:
    """The thread readings survive the fold and reach the report.

    This pins plumbing only: both backends echo the request rather than
    reading the pool back, so it is `observed_os_threads` that distinguishes
    a pinned worker from an auto one, and neither is checked here.
    """
    summary = probe.summarise_run(
        processes=2, threads=1,
        worker_digests=[("a",), ("a",)],
        observed_intra_op_threads=(1, 1),
        observed_os_threads=(69, 69))

    assert summary.threads == 1
    assert summary.observed_intra_op_threads == (1, 1)
    assert summary.observed_os_threads == (69, 69)


def test_default_process_sweep_includes_the_core_count() -> None:
    """#1275: the sweep must reach the CLI's default job count, i.e. nproc.

    The green integration tier sits at 5 workers; production runs one worker
    per core. A sweep that stopped below the core count would re-measure what
    CI already covers and miss the shape the default actually produces.
    """
    assert probe.default_process_sweep(logical_cpus=12) == (1, 2, 4, 8, 12)


def test_default_process_sweep_does_not_repeat_an_exact_power_of_two() -> None:
    assert probe.default_process_sweep(logical_cpus=8) == (1, 2, 4, 8)


def test_parse_args_defaults_to_tensorflow_and_the_annotator_window() -> None:
    config = probe.parse_args([])

    assert config.backend == "tensorflow"
    assert config.width == probe.DEFAULT_WIDTH


def test_parse_args_reads_an_explicit_process_list() -> None:
    config = probe.parse_args(["--processes", "1,4,32"])

    assert config.processes == (1, 4, 32)


def test_parse_args_reads_auto_as_the_runtimes_own_thread_default() -> None:
    """`auto` is a real arm: it is what the shipped backend does today."""
    config = probe.parse_args(["--threads", "auto,1"])

    assert config.threads == (None, 1)


def test_parse_args_rejects_a_non_positive_iteration_count() -> None:
    with pytest.raises(SystemExit):
        probe.parse_args(["--iterations", "0"])


def test_probe_mirrors_the_shipped_onnx_intra_op_default() -> None:
    """The ONNX arm is only a baseline if it is the shipped configuration.

    The probe re-states the constant rather than importing it, so that it
    stays runnable without `gain.annotation`. This pins the copy to the
    original.
    """
    from spliceai_annotator.spliceai_backend_onnx import (
        DEFAULT_ONNX_INTRA_OP_THREADS,
    )

    assert probe.ONNX_INTRA_OP_THREADS == DEFAULT_ONNX_INTRA_OP_THREADS


def test_a_run_is_not_reproducible_when_workers_saw_different_inputs() -> None:
    """Input drift must not be reported as runtime drift.

    Each worker builds the fixed input itself. If two workers disagreed about
    what they predicted on, identical-looking agreement -- or disagreement --
    would say nothing about the runtime.
    """
    summary = probe.summarise_run(
        processes=2, threads=1, worker_digests=[("a",), ("a",)],
        input_digests=("x", "y"))

    assert summary.distinct_inputs == 2
    assert summary.reproducible is False


def test_a_run_that_predicted_nothing_is_not_reproducible() -> None:
    summary = probe.summarise_run(
        processes=2, threads=1, worker_digests=[(), ()])

    assert summary.reproducible is False


def test_run_point_reports_agreement_through_the_real_pool(tmp_path) -> None:
    """End-to-end over spawn, the barrier and the pool, without a model.

    The orchestration is what a spurious 'reproducible' would come from, so
    it is exercised here rather than trusted.
    """
    config = probe.parse_args(
        ["--backend", "fake", "--processes", "3", "--iterations", "2"])

    summary = probe.run_point(config, processes=3, threads=None)

    assert summary.total_predictions == 6
    assert summary.distinct_outputs == 1
    assert summary.reproducible is True


def test_run_point_sees_divergence_through_the_real_pool() -> None:
    """The same path, with a runtime that deliberately drifts.

    Without this, a clean sweep could mean the plumbing never looks.
    """
    config = probe.parse_args(
        ["--backend", "fake-drift", "--processes", "3", "--iterations", "2"])

    summary = probe.run_point(config, processes=3, threads=None)

    assert summary.distinct_outputs > 1
    assert summary.reproducible is False
    assert summary.max_abs_deviation is not None
    assert summary.max_abs_deviation > 0.0
