"""Rate throttle for the anonymous pipeline-validation endpoint.

Its own scope, not the annotate endpoint's: the two have nothing in common
but a base class. Annotate spends real work per call and is budgeted at
``10/minute``; the pipeline editor validates as the user edits, so its rate
must sit above any cadence a human at a keyboard can produce.

The editor debounces its validate call, which puts a ceiling on that
cadence -- see the pipeline editor component. At a 400ms debounce a user
cannot sustain more than 150 requests a minute even typing without pause,
so ``120/minute`` sits above any realistic editing session while bounding
what an unattended client can spend.

It is a backstop, not the fix: what makes a single request cheap is the
size, annotator-count, expansion and query-length bounds on the endpoint
itself (iossifovlab/gain#635). The rate is chosen against what a bounded
request can still cost, so it belongs with those bounds rather than being
tuned on its own.
"""
from web_annotation.throttling import SessionScopedUserRateThrottle


class PipelineValidationRateThrottle(SessionScopedUserRateThrottle):
    """Throttle for config validation, on its own generous scope."""

    scope = "pipeline_validate"
