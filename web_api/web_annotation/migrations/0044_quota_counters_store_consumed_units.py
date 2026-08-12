"""Convert the quota period counters from remaining units to consumed ones.

gain#750. Each of the six period counters used to store the units a row had
*left*; it now stores the units it has *used*, so that the limit it is
measured against can be read from configuration at every check instead of
being baked into the row at the last period refresh.

The conversion is ``consumed = limit - remaining``, per quota type, using
each type's limits as configured at the moment the migration runs. That does
bake in the limits of the moment -- which is the coupling being removed --
but only once, and there is nothing else to convert against: a row records no
history of the limit it was refreshed to.

Two consequences worth stating:

- **Rows above their current limit clamp to zero.** ``limit - remaining``
  goes negative for a row left holding more than the limit now allows. A
  negative consumption would read as headroom beyond the limit, so it is
  clamped: under live limits such a user has consumed nothing against the
  new one.

  Two different rows land there, and this treats them alike. One is the
  residue of a limit having been *lowered*, where clamping is plainly
  right. The other is an admin grant deliberately placed above the limit,
  which the clamp revokes -- the row comes back at exactly its limit rather
  than at the grant. Distinguishing them needs information the row does not
  carry; the grant is re-issuable from the admin panel, which after this
  change stores it as a negative consumption and so keeps it. ADR 0019
  records the decision and says to re-issue such grants after deploying.
- **The three extra-unit fields are not touched.** They store a granted
  balance with no limit to measure against, and keep meaning "remaining";
  converting them would spend the grant the moment this ran.

The reverse converts back, with the same clamp, so a deployment can be rolled
back to the previous release. Both directions lose information: a row that
clamps in one direction does not come back.
"""

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F, Value
from django.db.models.functions import Greatest

#: Each quota model and the ``QUERY_QUOTAS`` block configuring its limits.
#: Spelled out rather than read off the models, because a migration runs
#: against historical models, which carry no methods -- ``_quota_config`` in
#: particular is not available here.
_QUOTA_MODELS = (
    ("UserQuota", "user"),
    ("AnonymousUserQuota", "anonymous"),
    ("SessionQuota", "anonymous"),
)

#: The counters to convert: every period counter, and only those. Kept as a
#: literal for the same reason -- a migration must describe the schema as it
#: was when written, not follow a model constant that later changes.
_PERIOD_COUNTERS = (
    "daily_jobs", "monthly_jobs",
    "daily_variants", "monthly_variants",
    "daily_attributes", "monthly_attributes",
)


def _flip_counters(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,  # noqa: ARG001
) -> None:
    """Flip every period counter between remaining and consumed units.

    Serves both directions: ``limit - x`` is its own inverse, so subtracting
    from the limit converts remaining into consumed and consumed back into
    remaining. Two things break that symmetry, so the reverse is a rollback
    path rather than a way to recover the old values: a row that clamped on
    the way forward comes back as a full allowance rather than as what it
    held, and the limits are read from configuration *again* on the way
    back, so a limit changed in between shifts every row.

    One ``UPDATE`` per table rather than a row at a time: the anonymous and
    session quota tables carry a row per IP and per session and are unbounded
    in production, and this runs inside ``RunPython``'s transaction, so a
    per-row loop would both load the whole table into memory and hold every
    row locked for the duration.
    """
    for model_name, config_key in _QUOTA_MODELS:
        model = apps.get_model("web_annotation", model_name)
        limits = settings.QUERY_QUOTAS[config_key]
        model.objects.update(**{
            field: Greatest(Value(0), Value(limits[field]) - F(field))
            for field in _PERIOD_COUNTERS
        })


class Migration(migrations.Migration):
    """Data-only: the columns keep their names, types and defaults."""

    dependencies = [
        ("web_annotation", "0043_rename_annotation_type_columns_to_tabular"),
    ]

    operations = [
        migrations.RunPython(_flip_counters, _flip_counters),
    ]
