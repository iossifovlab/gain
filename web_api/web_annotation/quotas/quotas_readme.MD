# Initial quotas implementation

## Overview

Quotas are used to limit the amount of requests any user can make to the API.
Anonymous users have lesser quotas and are mapped to their IP address.
The jobs API and single allele query API both use these quotas.

## Default limits

Default quota values are defined in settings_default.py.
We have 4 quota types:
- jobs
- single allele queries
- variants
- attributes

Quota default values are divided between registered and anonymous users.

### Quota default values

| Quota type            | Period  | Anonymous | Registered |
|-----------------------|---------|----------:|-----------:|
| Jobs                  | Daily   |        10 |        100 |
| Jobs                  | Monthly |       100 |      1 000 |
| Single allele queries | Daily   |       100 |      1 000 |
| Single allele queries | Monthly |     1 000 |     10 000 |
| Variants              | Daily   |   100 000 |  1 000 000 |
| Variants              | Monthly | 1 000 000 | 10 000 000 |
| Attributes            | Daily   | 1 000 000 | 10 000 000 |
| Attributes            | Monthly |10 000 000 |100 000 000 |


## Single allele queries

Single allele queries annotate 1 variant at a time and use their own query quota and the attribute quota.
For each non-internal attribute that is annotated with the pipeline used in the query, 1 attribute in the quota is used.

## Jobs

Jobs use their own job quota, the variant quota and the attribute quota.
Variants are deducted by the variant count in the input file, and attributes are deducted by the number of non-internal attributes that are annotated with the pipeline used in the job.

## Management commands

4 new management commands have been added to manage.py:
- 'export_quotas' to export all quotas to a csv file
- 'refreshdaily' to refresh the daily quotas for all users
- 'refreshmonthly' to refresh the monthly quotas for all users
- 'add_units' to add a month's worth of units to a specific user. The user ID is sent as a positional argument. (e.g. `manage.py add_units 123` will add a month's worth of units to the user with ID 123)

## Extra units
Quotas support extra units that currently can be added with the `add_units` management command.
When expending quotas, extra units are used only when the free daily and monthly quotas are completely exhausted.
If any of the extra quotas is fully consumed, all extra quotas become 0.

## API
A new API endpoint has been added to get the user's current quota status: `GET /api/quotas` will return all the quota details about the user.
