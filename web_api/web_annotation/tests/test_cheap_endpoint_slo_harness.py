# pylint: disable=C0114,C0116
from web_annotation.loadtest import cheap_endpoint_slo as harness


def test_harness_targets_annotate_by_default() -> None:
    """The #164 target stays the default, so recorded runs stay comparable."""
    args = harness.build_parser().parse_args([])

    assert args.target == "annotate"


def test_annotate_target_fires_at_the_annotate_endpoint() -> None:
    args = harness.build_parser().parse_args(
        ["--pipeline-id", "t4c8/t4c8_pipeline"])

    path, payload = harness._slow_request_spec(args)

    assert path == harness.ANNOTATE_PATH
    assert payload["pipeline_id"] == "t4c8/t4c8_pipeline"


def test_validate_target_fires_at_the_validation_endpoint() -> None:
    """``--target validate`` drives the anonymous validation endpoint (#659).

    Its slow request is a config body, not an annotatable: the endpoint takes
    the config text and builds it.
    """
    args = harness.build_parser().parse_args(
        ["--target", "validate", "--validate-config", "- position_score: x"])

    path, payload = harness._slow_request_spec(args)

    assert path == harness.VALIDATE_PATH
    assert payload["config"].startswith("- position_score: x")


def test_each_request_of_a_validate_burst_carries_a_distinct_config() -> None:
    """A burst must present the endpoint's memo with N unseen keys (#833).

    The endpoint remembers a verdict by a digest of the config text, so N
    identical bodies would be one build and N-1 lookups -- and this harness
    exists to measure what N concurrent builds do to the process. The
    distinguishing text is a yaml comment, which the parser discards, so
    every request still builds the identical pipeline.
    """
    args = harness.build_parser().parse_args(
        ["--target", "validate", "--validate-config", "- position_score: x"])

    configs = [
        harness._slow_request_spec(args, index)[1]["config"]
        for index in range(4)
    ]

    assert len(set(configs)) == 4, configs
    assert all(c.startswith("- position_score: x") for c in configs)


def test_record_reports_the_target_it_drove() -> None:
    """The record must say which endpoint produced it, or runs are opaque."""
    args = harness.build_parser().parse_args(["--target", "validate"])

    record = harness._build_record(args, "http://server", [
        {"status": 200, "elapsed_ms": 12.0},
        {"status": "timeout", "elapsed_ms": 30000.0},
    ], harness.CheapSamples(latencies_ms=[1.0, 2.0]), wall_elapsed=2.5)

    assert record["params"]["target"] == "validate"
    # Keyed by the target, so an annotate record keeps the #164 shape.
    assert record["validate"]["count"] == 2
    assert record["validate"]["ok_200"] == 1
