"""User-facing response text ``web_api`` returns, named.

Populated as literals are lifted out of the views. Those the ``web_e2e``
Playwright specs assert verbatim in a browser are checked against these
constants, without a stack, by ``tests/test_e2e_backend_pins.py``.
Reword one and that test names the spec that pins the old wording;
update the spec in the same change.
"""

#: Any job-creating ``POST /api/jobs/annotate_*`` once the job quota is spent.
JOB_QUOTA_EXCEEDED = "Job quota exceeded!"

#: ``POST /api/single_allele/annotate`` once the single-allele quota is spent.
SINGLE_ALLELE_QUOTA_EXCEEDED = "Single allele query quota exceeded!"

#: ``POST /api/single_allele/annotate`` for an ``annotatable`` it cannot
#: build: not an object, no recognised key set, or a malformed value.
INVALID_ANNOTATABLE = "Invalid annotatable provided!"

#: ``POST /api/single_allele/annotate`` for a DAE-style ``ins(...)`` /
#: ``del(...)`` variant: converting one needs a reference genome, which a
#: single-allele request does not name.
DAE_INDEL_NOT_SUPPORTED = (
    "DAE-style ins/del variants need a reference genome and are not "
    "supported here; send the allele as chrom/pos/ref/alt or vcf_like"
)

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
