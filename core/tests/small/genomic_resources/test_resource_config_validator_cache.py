"""The resource-config cerberus ``Validator`` is cached per type, per thread.

``ResourceConfigValidationMixin.validate_and_normalize_schema`` used to
rebuild both the schema and its cerberus ``Validator`` on every call, and
neither depends on the config being validated -- together about a third of
the call (gain#905).

Caching them is not free of hazard: a cerberus ``Validator`` keeps per-call
state on the instance (``validate()`` writes ``self.document``, and the
normalized document is read off the instance right afterwards), so one
instance shared between threads can hand a caller another caller's config.
The pipeline-load path is genuinely concurrent.  These tests pin both halves:
the validator is reused, and it is never reused *across threads*.

The normalized document is memoized as well (gain#1059), which is the last
section here.  Its hazards are different ones -- a document is per config,
not per type, and its caller mutates it -- so those tests say so where they
start rather than being mixed in above.
"""
from __future__ import annotations

import copy
import gc
import logging
import pathlib
import threading
import weakref
from collections.abc import Callable, Generator
from typing import Any, cast

import pytest
import pytest_mock
import yaml
from cerberus import Validator
from gain.genomic_resources import resource_implementation
from gain.genomic_resources.aggregators import (
    AGGREGATOR_SCHEMA,
    _build_aggregator_schema,
)
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
)
from gain.genomic_resources.liftover_chain import LiftoverChain
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_implementation import (
    CONFIG_VALIDATOR_CACHE,
    ResourceConfigValidationMixin,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)

from .conftest import RunInThreads

#: Two threads is the whole hazard: one validator, two callers.
WORKERS = 2


@pytest.fixture(autouse=True)
def _forget_cached_validators() -> Generator[None, None, None]:
    """Leave no validator of this module's behind for the next test.

    The cache is process-wide and never invalidated, so without this a
    validator built by one of the tests that patch the module-level
    ``Validator`` would outlive the patch and answer for a later test, with
    whatever barrier or spy it closed over still armed.  Verified to poison
    a later validation.

    It clears in teardown only, which leaves the reverse exposure open: a
    test that patches ``Validator`` and then counts is not told about a
    validator cached before it started.  That is why the counting tests use
    ``_a_one_off_implementation()`` rather than a real one -- a type nothing
    has seen cannot have a validator already.
    """
    yield
    CONFIG_VALIDATOR_CACHE.clear()


def _a_one_off_implementation(
    schema: dict[str, Any] | None = None,
) -> type[ResourceConfigValidationMixin]:
    """Return an implementation type nothing has cached yet.

    A class made here is new every call, so a test using one starts from a
    cold cache whatever ran before it -- which is what makes counting
    ``Validator`` constructions, and now ``validate()`` calls, meaningful.

    ``schema`` is for the tests that need two types to disagree about one
    config; it defaults to the single string field the rest of them use.
    """
    accepted = {"id": {"type": "string"}} if schema is None else schema

    class OneOffImplementation(ResourceConfigValidationMixin):
        @staticmethod
        def get_schema() -> dict[str, Any]:
            return accepted

    return OneOffImplementation


def _spy_on_validator_construction(
    mocker: pytest_mock.MockerFixture,
) -> Any:
    """Count ``Validator(...)`` constructions made by the mixin."""
    return mocker.patch.object(
        resource_implementation, "Validator",
        wraps=resource_implementation.Validator)


def _a_score() -> PositionScoreBuilder:
    """Return the builder every resource in this module is built from.

    Authored once: the data block is what derives the tabix index columns
    and the ``scores:`` block, so four copies of it are four chances for the
    tests to stop describing the same resource while all still passing.
    Builders are frozen, so a caller specialises with a further ``with_*``.
    """
    return (
        a_position_score()
        .with_score("phastCons", "float")
        .with_data(
            """
            chrom  pos_begin  phastCons
            1      10         0.1
            """,
        )
    )


@pytest.fixture(scope="module")
def any_resource(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResource:
    """Return one real resource for the tests that need any.

    It used to serve only to name a resource in a validation error message.
    Since gain#1059 it is also the memo KEY, so the tests below share a memo
    through it -- which the autouse fixture above empties between them.
    """
    tmp_path: pathlib.Path = tmp_path_factory.mktemp("validator_cache")
    return _a_score().build_resource(tmp_path)


def test_repeated_validation_of_one_type_builds_one_validator(
    mocker: pytest_mock.MockerFixture,
    any_resource: GenomicResource,
) -> None:
    implementation = _a_one_off_implementation()
    spy = _spy_on_validator_construction(mocker)

    implementation.validate_and_normalize_schema({"id": "one"}, any_resource)
    implementation.validate_and_normalize_schema({"id": "two"}, any_resource)

    assert spy.call_count == 1


def _an_allele_score_config() -> dict[str, Any]:
    """Return a config only ``AlleleScore``'s schema accepts.

    ``allele_score_mode`` is an allele-score field; a position score's schema
    has no such field and cerberus refuses an unknown one.
    """
    return {
        "type": "allele_score",
        "table": {"filename": "data.txt"},
        "scores": [{"id": "s", "type": "float", "name": "s"}],
        "allele_score_mode": "alleles",
    }


def test_a_type_does_not_validate_against_the_previous_type_schema(
    any_resource: GenomicResource,
) -> None:
    # PositionScore goes first, so it is the type that has just used a
    # validator when AlleleScore asks for one.
    with pytest.raises(ValueError, match="Invalid configuration"):
        PositionScore.validate_and_normalize_schema(
            _an_allele_score_config(), any_resource)

    normalized = AlleleScore.validate_and_normalize_schema(
        _an_allele_score_config(), any_resource)

    assert normalized["allele_score_mode"] == "alleles"
    # A default only the allele-score schema declares, so its presence says
    # the document was normalized by AlleleScore's own schema.
    assert normalized["merge_vcf_scores"] is False


def test_a_permissive_schema_does_not_leak_to_the_next_type(
    any_resource: GenomicResource,
) -> None:
    AlleleScore.validate_and_normalize_schema(
        _an_allele_score_config(), any_resource)

    with pytest.raises(ValueError, match="Invalid configuration"):
        PositionScore.validate_and_normalize_schema(
            _an_allele_score_config(), any_resource)


def test_a_reused_validator_carries_no_errors_from_a_failed_validation(
    mocker: pytest_mock.MockerFixture,
    any_resource: GenomicResource,
) -> None:
    implementation = _a_one_off_implementation()
    spy = _spy_on_validator_construction(mocker)

    with pytest.raises(ValueError, match="Invalid configuration"):
        implementation.validate_and_normalize_schema({"id": 5}, any_resource)
    normalized = implementation.validate_and_normalize_schema(
        {"id": "sound"}, any_resource)

    assert normalized == {"id": "sound"}
    # ... through one validator, so the assertion above is about a validator
    # that has already failed once, not about a second, fresh object.
    assert spy.call_count == 1


def test_an_earlier_normalized_document_survives_a_later_validation(
    mocker: pytest_mock.MockerFixture,
    any_resource: GenomicResource,
) -> None:
    """A caller keeps its document after the validator is used again.

    The normalized document is read off the validator instance, so a reused
    validator would be handing every caller the same object if cerberus
    normalized in place.  It does not -- ``validate()`` assigns a fresh
    ``self.document`` -- and that is what makes reuse safe within a thread.
    """
    implementation = _a_one_off_implementation()
    spy = _spy_on_validator_construction(mocker)

    first = implementation.validate_and_normalize_schema(
        {"id": "first"}, any_resource)
    second = implementation.validate_and_normalize_schema(
        {"id": "second"}, any_resource)

    assert first == {"id": "first"}
    assert second == {"id": "second"}
    assert spy.call_count == 1


_FIXTURE_REPO = pathlib.Path(__file__).parent / "fixtures" / "repo"

# The implementation each fixture resource type is validated by.  Spelled out
# rather than resolved through the plugin registry: the point of the test
# below is that a config normalizes the same way as before, so the type it is
# normalized by has to be beyond doubt.
_IMPLEMENTATION_BY_TYPE: dict[str, type[ResourceConfigValidationMixin]] = {
    "genome": ReferenceGenome,
    "gene_models": GeneModels,
    "liftover_chain": LiftoverChain,
    "position_score": PositionScore,
    "allele_score": AlleleScore,
}


def _config_corpus() -> list[tuple[str, dict[str, Any]]]:
    """Return the resource configs to normalize both ways.

    Every config committed under the fixture repo -- two genomes, a
    gene-models resource, a liftover chain and three position scores -- plus
    an allele score, which no fixture covers and whose schema carries a
    default the others do not.

    The fixture half is found by globbing, so it is asserted rather than
    trusted: were the fixtures ever moved or nested one level deeper, the
    glob would quietly return nothing and this file's central comparison
    would shrink to the one hand-written config while still passing.
    """
    configs = [
        (str(config_path.relative_to(_FIXTURE_REPO)),
         yaml.safe_load(config_path.read_text()))
        for config_path in sorted(
            _FIXTURE_REPO.glob("*/*/genomic_resource.yaml"))
    ]
    found = sorted(config["type"] for _, config in configs)
    assert found == [
        "gene_models", "genome", "genome",
        "liftover_chain", "position_score", "position_score", "position_score",
    ], f"fixture corpus changed shape: {found}"

    configs.append(("an-allele-score", _an_allele_score_config()))
    return configs


_CONFIG_CORPUS = _config_corpus()


def _normalized_without_the_cache(
    implementation: type[ResourceConfigValidationMixin],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Normalize ``config`` the way the mixin did before it cached anything.

    A fresh schema and a fresh ``Validator`` per call -- the code this change
    replaced, kept here as the reference the cached path is measured against.
    """
    validator = Validator(implementation.get_schema())
    assert validator.validate(config), validator.errors
    return dict(validator.document)


def _errors_without_the_cache(
    implementation: type[ResourceConfigValidationMixin],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Return what a fresh validator reports for a config it refuses."""
    validator = Validator(implementation.get_schema())
    assert not validator.validate(copy.deepcopy(config))
    return cast("dict[str, Any]", validator.errors)


@pytest.mark.parametrize(
    "config", [config for _, config in _CONFIG_CORPUS],
    ids=[resource_id for resource_id, _ in _CONFIG_CORPUS],
)
def test_a_resource_config_normalizes_as_it_did_without_the_cache(
    config: dict[str, Any],
    any_resource: GenomicResource,
) -> None:
    implementation = _IMPLEMENTATION_BY_TYPE[config["type"]]

    normalized = implementation.validate_and_normalize_schema(
        copy.deepcopy(config), any_resource)

    assert normalized == _normalized_without_the_cache(
        implementation, copy.deepcopy(config))


def test_a_score_config_still_normalizes_its_generated_aggregator(
    any_resource: GenomicResource,
) -> None:
    """The cached schema still carries the generated aggregator schema.

    The aggregator fragment is derived from the aggregator registry, and that
    generation is the fix for #261 -- a schema frozen in a way that lost it
    would take the fix with it.
    """
    config = {
        "type": "position_score",
        "table": {"filename": "data.txt"},
        "scores": [{
            "id": "s", "type": "float", "name": "s",
            "aggregator": "max",
        }],
    }

    normalized = PositionScore.validate_and_normalize_schema(
        copy.deepcopy(config), any_resource)

    assert normalized["scores"][0]["aggregator"] == "max"
    assert normalized == _normalized_without_the_cache(
        PositionScore, copy.deepcopy(config))


def _rejected_configs() -> list[dict[str, Any]]:
    """Return position-score configs the schema refuses, for two reasons."""
    return [
        {"type": "position_score", "table": {"filename": 7}},
        {"type": "position_score", "not_a_field": "x"},
    ]


def test_a_rejected_config_logs_what_it_logged_without_the_cache(
    caplog: pytest.LogCaptureFixture,
    any_resource: GenomicResource,
) -> None:
    """The reported errors are the same, and do not accumulate on reuse.

    ``validate_and_normalize_schema`` logs ``validator.errors`` before
    raising, and a reused validator is one that has already failed.  Cerberus
    resets ``_errors`` per call, so the two rejections below must report
    exactly what a fresh validator reports -- each alternated twice, so a
    validator that accumulated would show it.
    """
    expected = [
        str(_errors_without_the_cache(PositionScore, config))
        for config in _rejected_configs()
    ]
    # The two rejections must report different text, or alternating them
    # would prove nothing about which one each log line came from.
    assert expected[0] != expected[1]

    logged = []
    for _ in range(2):
        for config in _rejected_configs():
            with caplog.at_level(logging.ERROR), \
                    pytest.raises(ValueError, match="Invalid configuration"):
                PositionScore.validate_and_normalize_schema(
                    copy.deepcopy(config), any_resource)
            logged.append(caplog.records[-1].getMessage())

    for index, message in enumerate(logged):
        assert expected[index % len(expected)] in message


class _Interleaving:
    """An arming switch for the forced interleaving below."""

    def __init__(self) -> None:
        self.barrier: threading.Barrier | None = None
        self.serialize = threading.Lock()


def _install_interleaved_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> _Interleaving:
    """Let a test hold every validator inside ``validate()`` at once.

    The cross-thread hazard is a read of ``validator.document`` that happens
    after another thread's ``validate()`` has overwritten it.  Left to chance
    that window is tiny -- a direct reproduction bled one read in 600 -- so
    the interleaving is forced rather than raced for: the real cerberus call
    is serialized, and the caller is then held at a barrier until every other
    thread has validated too, so whatever a thread reads afterwards it reads
    last.

    The barrier is *armed by the test*, not fixed when the wrapper is
    installed, because the wrapper has to be in place before the validator
    the threads will share is built -- a wrapper installed afterwards would
    never see it, and the test would pass against a shared validator for want
    of any interleaving at all.
    """
    control = _Interleaving()
    build_validator = resource_implementation.Validator

    class Interleaved(build_validator):  # type: ignore[misc,valid-type]
        def validate(self, *args: Any, **kwargs: Any) -> Any:
            with control.serialize:
                validated = super().validate(*args, **kwargs)
            if control.barrier is not None:
                control.barrier.wait(timeout=30)
            return validated

    monkeypatch.setattr(resource_implementation, "Validator", Interleaved)
    return control


def test_two_threads_validating_at_once_each_get_their_own_document(
    monkeypatch: pytest.MonkeyPatch,
    run_in_threads: RunInThreads,
    any_resource: GenomicResource,
) -> None:
    implementation = _a_one_off_implementation()
    control = _install_interleaved_validation(monkeypatch)

    # Warm the cache on this thread first, with the barrier still disarmed:
    # a validator shared between threads is then one both workers certainly
    # find, rather than one they may or may not race to build separately.
    implementation.validate_and_normalize_schema({"id": "warm"}, any_resource)

    control.barrier = threading.Barrier(WORKERS)
    handing_out = threading.Lock()
    score_ids = iter(("alpha", "beta"))

    def validate() -> tuple[str, dict]:
        with handing_out:
            score_id = next(score_ids)
        return score_id, implementation.validate_and_normalize_schema(
            {"id": score_id}, any_resource)

    results, errors = run_in_threads(validate, threads_count=WORKERS)

    assert not errors
    assert dict(results) == {
        "alpha": {"id": "alpha"},
        "beta": {"id": "beta"},
    }


def test_using_a_cached_schema_does_not_change_it(
    any_resource: GenomicResource,
) -> None:
    """Validation leaves the cached schema as ``get_schema()`` built it.

    Sharing one schema between threads is only safe because cerberus treats
    it as read-only, and a cached schema is shared more widely than a rebuilt
    one ever was: ``PositionScore.get_schema()`` splices in the *module-level*
    ``AGGREGATOR_SCHEMA`` object rather than a copy of it, so the cached
    position-score schema, the cached fragment-score schema and the registry's
    own fragment are one object.  A mutation during validation would therefore
    not stay within one type.
    """
    freshly_built = PositionScore.get_schema()

    for score_id in ("first", "second", "third"):
        PositionScore.validate_and_normalize_schema(
            {
                "type": "position_score",
                "table": {"filename": "data.txt"},
                "scores": [{
                    "id": score_id, "type": "float", "name": score_id,
                    "aggregator": "max",
                }],
            },
            any_resource,
        )

    assert CONFIG_VALIDATOR_CACHE.schema_for(PositionScore) == freshly_built
    assert _build_aggregator_schema() == AGGREGATOR_SCHEMA


# The normalized *document* memo (gain#1059).  Everything above is about the
# schema and the validator, neither of which depends on the config.  The
# normalize-and-validate pass does, and it was the remaining 85% of a
# statistics task that reads nothing -- so its result is memoized too, keyed
# by the config object a resource was constructed from.


def _count_validations(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], int]:
    """Return a counter of cerberus ``validate()`` calls made by the mixin.

    Counts the normalize-and-validate pass itself rather than ``Validator``
    constructions, which the validator cache already suppresses -- a memo
    that skipped the pass and one that did not are indistinguishable by the
    construction spy the tests above use.
    """
    validations = 0

    class Counting(resource_implementation.Validator):  # type: ignore[misc,valid-type]
        def validate(self, *args: Any, **kwargs: Any) -> bool:
            nonlocal validations
            validations += 1
            return cast("bool", super().validate(*args, **kwargs))

    monkeypatch.setattr(resource_implementation, "Validator", Counting)
    return lambda: validations


def test_revalidating_one_config_object_runs_cerberus_once(
    monkeypatch: pytest.MonkeyPatch,
    any_resource: GenomicResource,
) -> None:
    implementation = _a_one_off_implementation()
    validations = _count_validations(monkeypatch)
    config = {"id": "memoized"}

    first = implementation.validate_and_normalize_schema(config, any_resource)
    second = implementation.validate_and_normalize_schema(config, any_resource)

    assert first == {"id": "memoized"}
    assert second == {"id": "memoized"}
    assert validations() == 1


@pytest.fixture
def a_shared_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """Return a position score to construct repeatedly, per test.

    Function-scoped, unlike ``any_resource``: these tests turn on which
    construction of one resource filled the memo, so they start from a
    resource nothing has normalized yet.
    """
    return _a_score().build_resource(tmp_path)


def test_a_score_config_mutation_does_not_reach_the_next_score(
    a_shared_resource: GenomicResource,
) -> None:
    """A construction that FILLED the memo may edit what it was given.

    ``GenomicScore.__init__`` writes into the document it is handed
    (``config["id"] = resource_id``) and then keeps it for the object's
    lifetime, so a memo that stored the object it handed its first caller
    would be edited by that caller and would answer with the edit ever after.
    """
    first = PositionScore(a_shared_resource)

    first.config["scores"][0]["id"] = "clobbered"
    second = PositionScore(a_shared_resource)

    assert second.config is not first.config
    assert second.config["scores"][0]["id"] == "phastCons"
    assert second.config["id"] == a_shared_resource.resource_id


def test_the_construction_that_fills_the_memo_is_detached_too(
    tmp_path: pathlib.Path,
) -> None:
    """The FIRST caller's document is its own as well, and cerberus's is not.

    ``validate()`` copies the config shallowly and rebuilds only the mappings
    its schema describes, so a field the schema does not reach into --
    ``meta.labels`` here, ``default_annotation`` in a deployed resource --
    comes back as the very object in the resource's config.  Handing that
    straight to the caller that missed the memo, and a detached copy to every
    caller that hits it, would make "whose dict is this" depend on cache
    state; worse, the edit below would reach ``resource.config`` while the
    memo went on answering with the snapshot taken before it.
    """
    resource = (
        _a_score()
        .with_labels(reference_genome="hg38")
        .build_resource(tmp_path)
    )

    # The first construction of this resource: nothing is memoized yet.
    score = PositionScore(resource)
    score.config["meta"]["labels"]["reference_genome"] = "clobbered"

    labels = resource.get_config()["meta"]["labels"]
    assert labels["reference_genome"] == "hg38"


def test_a_memo_hit_may_be_edited_without_reaching_the_next_score(
    a_shared_resource: GenomicResource,
) -> None:
    """And so may a construction that was ANSWERED by the memo.

    The sibling test above only reaches the document cerberus built, which
    is nobody else's whatever the memo does with it -- so on its own it says
    nothing about how deeply the memo copies on the way *out*.  This one
    edits a document the memo handed over, and asks a third construction:
    a shallow copy shares every nested dict, so the edit below would arrive
    through ``scores`` and stay there for the life of the entry.
    """
    PositionScore(a_shared_resource)
    from_the_memo = PositionScore(a_shared_resource)

    from_the_memo.config["scores"][0]["id"] = "clobbered"
    later = PositionScore(a_shared_resource)

    assert later.config["scores"][0]["id"] == "phastCons"
    assert later.config["table"] is not from_the_memo.config["table"]


def test_two_resources_with_equal_configs_do_not_evict_each_other(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """The resource is in the key so that a repository does not thrash.

    Two resources of one type with byte-identical configs is the ordinary
    case in a GRR, and a scan alternates between resources.  Keyed by type
    alone the memo would hold one slot per type and every alternation would
    miss, which is why the entry hangs off the resource -- and, since the
    keys are weak, why it also lets go.

    What the key does NOT have to buy is each resource's own
    ``config["id"]``: ``GenomicScore.__init__`` writes that on its own copy
    and after normalization, so no keying scheme could get it wrong.  The
    two resources here are built with equal configs anyway, because that is
    the case a content-keyed memo would merge and this one must not.

    A one-off implementation rather than ``PositionScore``, so that the
    validator counted below is built after ``Validator`` is patched -- with
    a real type the count would sit at zero whether the memo hit or missed.
    """
    repo = (
        a_grr()
        .with_resource("alpha", _a_score())
        .with_resource("beta", _a_score())
        .build_repo(tmp_path)
    )
    alpha = repo.get_resource("alpha")
    beta = repo.get_resource("beta")
    assert alpha.get_config() == beta.get_config()
    implementation = _a_one_off_implementation(PositionScore.get_schema())
    validations = _count_validations(monkeypatch)

    for resource in (alpha, beta, alpha, beta):
        implementation.validate_and_normalize_schema(
            resource.get_config(), resource)

    # Two, not four: the second pass over each resource is answered from the
    # entry the first pass left, which the other resource has not displaced.
    assert validations() == 2
    assert PositionScore(alpha).config["id"] == "alpha"
    assert PositionScore(beta).config["id"] == "beta"


def test_a_second_config_for_one_resource_is_normalized_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
    any_resource: GenomicResource,
) -> None:
    """A resource does not stand for one config, so the memo cannot.

    ``validate_and_normalize_schema`` takes the config as an argument rather
    than reading it off the resource, and callers do pass something other
    than ``resource.config`` -- the corpus tests above normalize a dozen
    configs against one resource.  A memo keyed on the resource alone would
    answer the second call with the first call's document; the memo hits
    only on the config *object* it was filled from.
    """
    implementation = _a_one_off_implementation()
    validations = _count_validations(monkeypatch)

    first = implementation.validate_and_normalize_schema(
        {"id": "first"}, any_resource)
    second = implementation.validate_and_normalize_schema(
        {"id": "second"}, any_resource)

    assert first == {"id": "first"}
    assert second == {"id": "second"}
    assert validations() == 2


def test_an_invalid_config_is_refused_every_time_it_is_offered(
    monkeypatch: pytest.MonkeyPatch,
    any_resource: GenomicResource,
) -> None:
    """A failed validation leaves nothing behind to hit on.

    The memo is filled after the raise, not before it, so the second offer
    of a rejected config is validated afresh and rejected afresh -- rather
    than being answered from a half-filled entry, or worse, accepted.
    """
    implementation = _a_one_off_implementation()
    validations = _count_validations(monkeypatch)
    config = {"id": 5}

    for _ in range(2):
        with pytest.raises(ValueError, match="Invalid configuration"):
            implementation.validate_and_normalize_schema(config, any_resource)

    assert validations() == 2


def test_one_config_object_is_normalized_once_per_implementation(
    monkeypatch: pytest.MonkeyPatch,
    any_resource: GenomicResource,
) -> None:
    """The implementation type is part of the key, not just the config.

    The same document means different things to different schemas -- here
    one a permissive schema accepts and a stricter one refuses -- so a memo
    that forgot the type would hand the second type the first's answer and
    turn a refusal into an acceptance.

    Both types are made here rather than reaching for ``AlleleScore`` and
    ``PositionScore``: a real type's validator may already be cached from an
    earlier test, built before ``_count_validations`` patched ``Validator``,
    and the count would then come up short depending on what ran first.
    """
    permissive = _a_one_off_implementation({
        "id": {"type": "string"}, "mode": {"type": "string"},
    })
    strict = _a_one_off_implementation({"id": {"type": "string"}})
    validations = _count_validations(monkeypatch)
    config = {"id": "shared", "mode": "alleles"}

    normalized = permissive.validate_and_normalize_schema(
        config, any_resource)
    with pytest.raises(ValueError, match="Invalid configuration"):
        strict.validate_and_normalize_schema(config, any_resource)

    assert normalized == {"id": "shared", "mode": "alleles"}
    assert validations() == 2


def test_clearing_the_cache_forgets_memoized_documents(
    monkeypatch: pytest.MonkeyPatch,
    any_resource: GenomicResource,
) -> None:
    """``clear()`` forgets documents for the reason it forgets validators.

    A document normalized by a schema built while ``Validator`` was patched
    would otherwise outlive the patch and be handed to a later test, which
    is exactly the poisoning the autouse fixture above exists to prevent.
    """
    implementation = _a_one_off_implementation()
    config = {"id": "memoized"}
    implementation.validate_and_normalize_schema(config, any_resource)
    validations = _count_validations(monkeypatch)

    CONFIG_VALIDATOR_CACHE.clear()
    normalized = implementation.validate_and_normalize_schema(
        config, any_resource)

    assert normalized == {"id": "memoized"}
    assert validations() == 1


def test_a_memoized_document_does_not_keep_its_resource_alive(
    any_resource: GenomicResource,
) -> None:
    """The memo outlives nothing.

    A GRR walk constructs an implementation per resource, so an entry that
    held its resource strongly would hold every resource of a repository for
    the life of the process.  The keys are weak, as the type keys beside them
    already are.

    The resource is built here rather than taken from a repository on
    purpose: a repository caches the resources it hands out, so one obtained
    through it is alive whatever the memo does, and the assertion below would
    pass for the wrong reason.  Only the protocol is borrowed.
    """
    implementation = _a_one_off_implementation()

    def normalize_a_throwaway_resource() -> weakref.ref[GenomicResource]:
        resource = GenomicResource(
            "throwaway", (0,), any_resource.proto, {"id": "x"})
        implementation.validate_and_normalize_schema(
            resource.get_config(), resource)
        return weakref.ref(resource)

    reference = normalize_a_throwaway_resource()
    gc.collect()

    assert reference() is None


def test_two_threads_hitting_one_memo_each_get_their_own_document(
    run_in_threads: RunInThreads,
    any_resource: GenomicResource,
) -> None:
    """One memo entry, two callers, two documents.

    This is the hazard that forced the *validator* to be per-thread, asked
    of the memo -- which answers it differently: the entry is shared between
    threads and needs no lock, because what leaves it is always a copy.  A
    memo that returned its own document would hand both threads one object,
    and whichever wrote ``config["id"]`` last would win.
    """
    implementation = _a_one_off_implementation()
    config = {"id": "shared"}
    # Warm on this thread, so both workers certainly hit the entry rather
    # than possibly racing to fill it separately.
    implementation.validate_and_normalize_schema(config, any_resource)

    def normalize() -> dict:
        return implementation.validate_and_normalize_schema(
            config, any_resource)

    results, errors = run_in_threads(normalize, threads_count=WORKERS)

    assert not errors
    assert results == [{"id": "shared"}, {"id": "shared"}]
    assert results[0] is not results[1]
