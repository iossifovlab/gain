"""Rate throttle for the anonymous pipeline-validation endpoint.

Its own scope, not the annotate endpoint's: the two have nothing in common
but a base class. Annotate spends real work per call and is budgeted at
``10/minute``; the pipeline editor validates as the user edits, so its rate
must sit above any cadence a human at a keyboard can produce.

The editor debounces its validate call, which puts a ceiling on that
cadence -- see the pipeline editor component. ``300/minute`` leaves
comfortable headroom over the debounced worst case while still bounding what
an unattended client can spend. It is a backstop, not the fix: what makes a
single request cheap is the size, annotator-count and query-length bounds on
the endpoint itself (iossifovlab/gain#635).
"""
from web_annotation.throttling import SessionScopedUserRateThrottle


class PipelineValidationRateThrottle(SessionScopedUserRateThrottle):
    """Throttle for config validation, on its own generous scope."""

    scope = "pipeline_validate"
