# Plan: Phase 2 — Reconcile Python lint configs

## Context

Phase 1 imported `gpf-web-annotation` into the gain monorepo with its
own `mypy.ini` and `pylintrc` placed in `web_infra/`. The gain monorepo
has root-level `mypy.ini` and `pylintrc` that the existing sub-projects
(`vep_annotator/`, `spliceai_annotator/`, `demo_annotator/`) reference
via symlinks (`mypy.ini -> ../mypy.ini`, `pylintrc -> ../pylintrc`).

Phase 2 reconciles the two pairs. Two complications make a "single
shared config" route messy:

1. **mypy.ini divergence is non-trivial.** `web_infra/mypy.ini` adds
   `plugins = numpy.typing.mypy_plugin, mypy_django_plugin.main` and
   a `[mypy.plugins.django-stubs]` section. Root mypy.ini has many
   `[mypy-X.*] ignore_missing_imports = True` sections (lark, dask_*,
   sklearn, sqlalchemy, etc.) that web_infra's lacks.
2. **Django plugin can't go to root yet.** Adding
   `mypy_django_plugin.main` to root mypy.ini affects mypy runs on
   `core/` and the annotators. The plugin tries to introspect the
   Django settings module — which only resolves cleanly once `web_api`
   is a uv workspace member (Phase 3). Premature promotion risks
   breaking mypy on the rest of the repo.

`pylintrc` divergence is trivial — exactly one line:
`max-returns=8`.

## Approach

- **`pylintrc`**: Add `max-returns=8` to root. Replace `web_infra/pylintrc`
  with a symlink at `web_api/pylintrc -> ../pylintrc`, matching how
  `vep_annotator/pylintrc -> ../pylintrc` works. Delete `web_infra/pylintrc`.

- **`mypy.ini`**: Move `web_infra/mypy.ini` → `web_api/mypy.ini` via
  `git mv` (preserves history). Augment with all `[mypy-X.*]` sections
  that root has and web_api doesn't, so `web_api/mypy.ini` is a proper
  superset of root for library coverage. Don't symlink — keep
  `web_api/mypy.ini` as a per-project file holding the Django plugin
  config. Document inside the file that this is intended to converge
  back to root in Phase 3+ once Django plugin loading is safe globally.

- **`web_api/scripts/backend_linters.sh`**: Currently `cd /wd/` and
  invokes ruff/pylint/mypy with `web_api/web_annotation` as the target.
  That path resolution is fine, but `mypy` and `pylint` will pick up
  configs from the current working directory — if cwd is `/wd/`, they
  use root configs (which lack the Django plugin). Change the script
  to `cd /wd/web_api/` and target `web_annotation` directly, matching
  the annotator convention. Then mypy finds `web_api/mypy.ini` and
  pylint finds `web_api/pylintrc` automatically. Ruff still walks up
  to `gain/ruff.toml` (correct).

- Configs move *out* of `web_infra/` because `web_infra/` is
  cross-cutting orchestration — it has no Python code to lint.

## Step-by-step

1. **Add `max-returns=8` to root `pylintrc`.** Single-line edit.
2. **Move mypy config**: `git mv web_infra/mypy.ini web_api/mypy.ini`.
3. **Augment `web_api/mypy.ini`** with the `[mypy-X.*]` sections that
   root has but web_api doesn't (lark, google.cloud, dask_kubernetes,
   dask_sql, dask_jobqueue, impala, seaborn, gcsfs, raven,
   oauth2_provider, sklearn, bumpversion, invoke, versioneer, ijson,
   s3fs, silk, monkeytype, sqlalchemy, sqlalchemy.*, pyBigWig,
   pyliftover, intervaltree, tensorflow, tensorflow.*, pandas,
   pandas.*).
4. **Drop redundant pylintrc**: `git rm web_infra/pylintrc` and create
   `web_api/pylintrc` as a symlink to `../pylintrc`.
5. **Update `web_api/scripts/backend_linters.sh`**: change `cd /wd/`
   to `cd /wd/web_api/` and use `web_annotation` (without the
   `web_api/` prefix) as the linter target. Update the report-output
   paths to remain absolute (`/wd/web_api/reports/...`) so they don't
   depend on cwd.
6. **Verify**:
   - `ls -la web_api/pylintrc` shows symlink to `../pylintrc`.
   - `cat web_api/mypy.ini` includes Django plugin config and all
     library `[mypy-*]` sections.
   - `git diff --stat` matches expectations (1 root pylintrc edit, 1
     git rm, 1 git mv with edits, 1 symlink add, 1 script edit).

## Out of scope for Phase 2 (later phases)

- Adding `mypy_django_plugin.main` to root mypy.ini (Phase 3+, once
  web_api is a workspace member and django-stubs can resolve settings
  cleanly across the repo).
- Collapsing `web_api/mypy.ini` into a symlink to root (depends on
  the above).
- Reconciling ruff configuration — root `ruff.toml` already covers
  the workspace; web_api has no separate ruff config to merge.
- Removing the `web_infra/Makefile` reference to
  `../django-gpf-web-annotation/...` (separate cross-repo concern).

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/mypy.ini` (root, untouched in Phase 2)
- `/home/lubo/Work/seq-pipeline/gain/pylintrc` (root, +1 line)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/mypy.ini` (moved out)
- `/home/lubo/Work/seq-pipeline/gain/web_infra/pylintrc` (deleted)
- `/home/lubo/Work/seq-pipeline/gain/web_api/mypy.ini` (created from move + augmented)
- `/home/lubo/Work/seq-pipeline/gain/web_api/pylintrc` (created as symlink)
- `/home/lubo/Work/seq-pipeline/gain/web_api/scripts/backend_linters.sh` (cwd change)
