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


def test_every_validate_request_carries_a_config_the_memo_has_not_seen(
) -> None:
    """Each call must mint a fresh config, not repeat one (#833).

    The endpoint remembers a verdict by a digest of the config text, so a
    repeated body is a dictionary lookup rather than the build this harness
    exists to measure -- and "repeated" reaches across runs as well as within
    a burst, because the recorded recipe drives one long-lived server at
    several K values inside a single TTL window
    (``docs/833-validate-result-memo.md``). A per-call uuid covers both; a
    per-burst index would only have covered the second.

    The distinguishing text is a yaml comment, which the parser discards, so
    every request still builds the identical pipeline.
    """
    args = harness.build_parser().parse_args(
        ["--target", "validate", "--validate-config", "- position_score: x"])

    configs = [harness._slow_request_spec(args)[1]["config"] for _ in range(4)]

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
