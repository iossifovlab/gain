"""Rate throttle for the single-allele annotate endpoint.

The session-scoped keying this module used to define itself now lives in
``web_annotation.throttling``, shared with the pipeline-validation throttle
added for iossifovlab/gain#635. This class only picks the scope, and
therefore the rate: DRF's default ``user`` scope, i.e. the ``10/minute``
budget the rate-limit e2e specs assert against.
"""
from web_annotation.throttling import SessionScopedUserRateThrottle


class AnnotateUserRateThrottle(SessionScopedUserRateThrottle):
    """Throttle for annotate: the default ``user`` scope, session-keyed."""
