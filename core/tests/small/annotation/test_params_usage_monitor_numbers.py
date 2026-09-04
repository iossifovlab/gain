"""The numeric accessors on ``ParamsUsageMonitor`` (gain#1166).

One place where an annotator parameter that has to be a number is read:
absent means the default, a string that spells a number is accepted, and
everything a number cannot be is refused as the pipeline LOADS, naming
the key the way the user spelled it.
"""
from collections.abc import Callable

import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
    ParamsUsageMonitor,
)


def test_an_absent_number_parameter_answers_the_default() -> None:
    params = ParamsUsageMonitor({})

    assert params.get_number("min_overlap", default=0.5) == 0.5


@pytest.mark.parametrize("read, default", [
    pytest.param(ParamsUsageMonitor.get_number, 0.5, id="get_number"),
    pytest.param(ParamsUsageMonitor.get_integer, 7, id="get_integer"),
])
def test_a_parameter_written_with_no_value_means_the_default(
    read: Callable[..., float | None], default: float,
) -> None:
    """``min_overlap:`` with nothing after it is YAML for ``None``.

    A key with no value says as little as no key at all, and said as
    little before any of this: the fraction reader took ``None`` for
    "no threshold" (gain#1125).  It keeps meaning that.
    """
    params = ParamsUsageMonitor({"min_overlap": None})

    assert read(params, "min_overlap", default=default) == default


def test_an_absent_integer_parameter_answers_the_default() -> None:
    params = ParamsUsageMonitor({})

    assert params.get_integer("region_length_cutoff", default=500_000) \
        == 500_000


def test_reading_a_number_declares_the_parameter() -> None:
    """Reading a parameter is what DECLARES it.

    A pipeline is refused for naming a key no annotator read, so an
    accessor that looked the value up behind the monitor's back would
    turn every parameter read through it into an unused one.
    """
    params = ParamsUsageMonitor({"promoter_len": 100})

    params.get_integer("promoter_len")

    assert params.get_unused_keys() == set()


def test_a_boolean_is_no_number_even_though_it_is_an_int() -> None:
    """``bool`` subclasses ``int``, so ``float(True)`` is 1.0.

    Left to the numeric check alone, ``min_overlap: true`` would be
    admitted as the threshold 1.0 -- one the user never asked for,
    applied in silence.
    """
    params = ParamsUsageMonitor({"min_overlap": True})

    with pytest.raises(AnnotationConfigurationError, match="min_overlap"):
        params.get_number("min_overlap")


def test_a_string_that_spells_a_number_means_that_number() -> None:
    """The annotation editor posts strings.

    Its form controls hold text, so a number typed there arrives as
    ``"0.5"``.  Refusing it would 400 the editor on its own output.
    Quoting the value in hand-written YAML lands here too and means the
    same thing.
    """
    params = ParamsUsageMonitor({"min_overlap": "0.5"})

    assert params.get_number("min_overlap") == 0.5


def test_a_string_that_spells_no_number_is_refused_by_name() -> None:
    params = ParamsUsageMonitor({"min_overlap": "half"})

    with pytest.raises(AnnotationConfigurationError, match="min_overlap"):
        params.get_number("min_overlap")


@pytest.mark.parametrize("configured", [
    pytest.param(1.5, id="above-the-maximum"),
    pytest.param(-0.5, id="below-the-minimum"),
])
def test_a_number_outside_the_range_is_refused_by_name(
    configured: float,
) -> None:
    params = ParamsUsageMonitor({"min_overlap": configured})

    with pytest.raises(AnnotationConfigurationError, match="min_overlap"):
        params.get_number("min_overlap", minimum=0.0, maximum=1.0)


@pytest.mark.parametrize("configured", [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="inf"),
    pytest.param("nan", id="nan-spelled-out"),
    pytest.param("1e400", id="a-string-that-overflows-to-inf"),
])
def test_a_value_that_is_no_finite_number_is_refused_unbounded(
    configured: float | str,
) -> None:
    """Refused for being what it is, not for missing a bound.

    ``nan`` and ``inf`` both survive ``float()``, and a caller that asks
    for no range -- the accessor is shared, so one will -- has nothing
    left to refuse them with.  Neither is a length or a share of one.
    """
    params = ParamsUsageMonitor({"min_overlap": configured})

    with pytest.raises(AnnotationConfigurationError, match="min_overlap"):
        params.get_number("min_overlap")


def test_a_number_too_large_for_a_float_is_read_not_refused() -> None:
    """A whole number is answered whole, however large.

    Widening the answer to ``float`` would raise ``OverflowError`` here
    -- out of the accessor whose whole contract is that a bad value
    comes back as an ``AnnotationConfigurationError`` naming the key,
    and this value is not even bad.
    """
    beyond_float_range = 10**400
    params = ParamsUsageMonitor({"cutoff": beyond_float_range})

    assert params.get_number("cutoff", minimum=0) == beyond_float_range


def test_a_refusal_names_the_annotator_that_configured_the_key() -> None:
    """A pipeline holds many annotators; the key alone says which one."""
    params = ParamsUsageMonitor({"promoter_len": "big"}, owner="effect")

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        params.get_integer("promoter_len")

    assert str(excinfo.value) == \
        "effect configures promoter_len: 'big', which is not a number."


def test_a_refusal_keeps_the_configured_value_on_one_line() -> None:
    """The value reaches a logged message, so it is caller text.

    ``float()`` accepts the whitespace around a number, line breaks
    included, so a value that carries one parses and then fails the
    range check -- and interpolating it raw emits a second,
    fully-formed-looking log record (gain#642, gain#655).
    """
    params = ParamsUsageMonitor({"min_overlap": "2\n"})

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        params.get_number("min_overlap", minimum=0.0, maximum=1.0)

    message = str(excinfo.value)
    assert "\n" not in message
    assert "2\\x0a" in message


def test_a_parameter_that_is_no_scalar_at_all_is_refused_by_name() -> None:
    params = ParamsUsageMonitor({"promoter_len": [100]})

    with pytest.raises(AnnotationConfigurationError, match="promoter_len"):
        params.get_integer("promoter_len")


def test_an_integer_parameter_spelled_as_a_string_comes_back_an_int() -> None:
    """A length is counted in bases, and the count has to stay an ``int``.

    ``EffectAnnotator`` takes ``promoter_len`` as one and does index
    arithmetic with it, so answering ``100.0`` here would push a float
    into positions.
    """
    params = ParamsUsageMonitor({"promoter_len": "100"})

    read = params.get_integer("promoter_len")

    assert read == 100
    assert isinstance(read, int)


def test_a_fractional_value_is_no_length() -> None:
    """Refused rather than truncated: 1.5 bases is not 1, it is a typo."""
    params = ParamsUsageMonitor({"promoter_len": 1.5})

    with pytest.raises(AnnotationConfigurationError, match="promoter_len"):
        params.get_integer("promoter_len")


@pytest.mark.parametrize("spelling", [int, str], ids=["number", "quoted"])
def test_an_integer_too_large_for_a_float_survives_intact(
    spelling: type,
) -> None:
    """Read as an ``int``, never round-tripped through ``float``.

    ``float(2**53 + 1)`` is ``2**53``, so a conversion on the way through
    would answer a DIFFERENT number than the one configured -- silently.
    Both spellings, because the quoted one is the editor's.
    """
    beyond_float_precision = 2**53 + 1
    params = ParamsUsageMonitor({
        "region_length_cutoff": spelling(beyond_float_precision)})

    assert params.get_integer("region_length_cutoff") \
        == beyond_float_precision
