"""User-facing response text ``web_api`` returns, named.

Populated as literals are lifted out of the views; the six here today are
the ones the ``web_e2e`` Playwright specs assert verbatim in a browser,
which ``tests/test_e2e_backend_pins.py`` checks against these constants
without a stack.  Reword one and that test names the spec that pins the
old wording; update the spec in the same change.
"""

#: Any job-creating ``POST /api/jobs/annotate_*`` once the job quota is spent.
JOB_QUOTA_EXCEEDED = "Job quota exceeded!"

#: ``POST /api/single_allele/annotate`` once the single-allele quota is spent.
SINGLE_ALLELE_QUOTA_EXCEEDED = "Single allele query quota exceeded!"

#: ``POST /api/jobs/validate_columns`` for a selection no annotatable fits.
CANNOT_BUILD_ANNOTATABLE = "Cannot build annotatable from selected columns!"

#: ``POST /api/login`` with a wrong e-mail / password pair.
INVALID_LOGIN_CREDENTIALS = "Invalid login credentials"

#: ``POST /api/register`` for an address that already has an account.
EMAIL_ALREADY_IN_USE = "This email is already in use"

#: ``POST /api/forgotten_password``; rendered with ``.format(email=...)``.
RESET_LINK_EMAIL_SENT = (
    "An e-mail has been sent to {email} containing the reset link"
)
