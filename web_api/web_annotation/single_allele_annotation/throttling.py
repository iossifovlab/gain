"""Rate throttle for the single-allele annotate endpoint.

The session-scoped keying this module used to define itself now lives in
``web_annotation.throttling``, shared with the pipeline-validation throttle
added for iossifovlab/gain#635. This class only picks the scope, and
therefore the rate: the ``annotate`` scope, i.e. the ``10/minute`` budget the
rate-limit e2e specs assert against.

The scope used to be DRF's ``user``, which reads as the API-wide cap
``UserRateThrottle`` applies when installed globally. Nothing installs it
globally here, so that rate only ever reached this endpoint; it is named
after the endpoint now (iossifovlab/gain#694). The rate is unchanged.
"""
from web_annotation.throttling import SessionScopedUserRateThrottle


class AnnotateUserRateThrottle(SessionScopedUserRateThrottle):
    """Throttle for annotate: its own ``annotate`` scope, session-keyed."""

    scope = "annotate"
