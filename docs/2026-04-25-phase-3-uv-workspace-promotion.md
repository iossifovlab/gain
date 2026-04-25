# Plan: Phase 3 — Promote `web_api/` to a uv workspace member

## Context

Phases 1 and 2 of the gpf-web-annotation merge are complete on `master`:
the code is in place at `web_api/`, `web_ui/`, `web_e2e/`, `web_infra/`,
and Python lint configs are reconciled.

Phase 3 now joins `web_api/` into the gain monorepo's uv workspace so
that `uv.lock` covers the whole repo and `uv sync --all-packages
--all-groups` resolves a single environment that can run both
`gain-core` and the Django backend. This is the prerequisite for
Phase 5 (CI unification): once `web_api` is part of the workspace,
the root `Jenkinsfile` can run its tests the same way it runs `core`'s.

There is no Python-level coupling today between `web_api` and
`gain-core` (the agent confirmed `web_api/web_annotation/` does not
import `gain`). The conda-channel based runtime coupling stays intact
and continues to drive the existing CI; this plan does not touch that.

## Scope

**In scope:**
- Update `web_api/pyproject.toml` to declare its actual runtime deps
  and a per-project `[dependency-groups] dev`.
- Switch the build system to `hatchling + hatch-vcs` with a dynamic
  version, matching the convention used by `core`, `vep_annotator`,
  `spliceai_annotator`, `demo_annotator`.
- Drop `web_api/MANIFEST.in` (it references
  `gpf_web_annotation_frontend/static` and
  `gpf_web_annotation_frontend/templates`, paths that belong to a
  separate repository — the file has been dead since Phase 1's import).
- Add `web_api` to root `pyproject.toml`'s
  `[tool.uv.workspace].members` and a corresponding entry in
  `[tool.uv.sources]`.
- Regenerate `uv.lock` via `uv sync --all-packages --all-groups`.
- Verify the workspace resolves cleanly and the Django stack imports
  via uv.

**Out of scope (later phases):**
- Renaming the PyPI package from `django-gpf-web-annotation` to
  `gain-web-api`. Keeping the existing name avoids breaking any
  external packaging or deployment that references it, and the
  workspace pattern doesn't require the name match the directory.
- Adding `gain-core` as a `web_api` runtime dependency (Phase 8 — they
  remain decoupled at the Python level for now; runtime coupling
  is via conda packaging).
- Creating `web_api/conda-recipe/recipe.yaml` (`gain_*` conda packages
  exist; `django-gpf-web-annotation` ships into the docker image
  differently — recipe generation is part of any future conda
  packaging unification).
- Folding `web_api/environment.yml` and `dev-environment.yml` into
  the root conda env files (Phase 4).
- Renaming the Django app `web_annotation` (separate, large blast
  radius — out of merge scope entirely).
- Removing the upstream `gpf-conda-packaging` coupling (Phase 8).

## Recommended approach

The `web_api/pyproject.toml` becomes structurally similar to
`vep_annotator/pyproject.toml` (build-system, dynamic version,
`[dependency-groups] dev`, `[tool.hatch.*]` blocks) but keeps:
- its existing PyPI name `django-gpf-web-annotation`
- its existing `[project.scripts]` entry
  (`validate_vcf_file = "web_annotation.jobs.validate_vcf_file:main"`)
- no `gain.annotation.annotators` entry points (it's a Django app,
  not an annotator plugin)

Hatchling's default behavior includes all files inside declared
package directories, so templates/, static/, and `about.md` get
packaged automatically — no `[tool.hatch.build.force-include]`
gymnastics needed. Tests are excluded explicitly.

## Critical files

- `/home/lubo/Work/seq-pipeline/gain/web_api/pyproject.toml` (rewrite)
- `/home/lubo/Work/seq-pipeline/gain/web_api/MANIFEST.in` (delete)
- `/home/lubo/Work/seq-pipeline/gain/pyproject.toml` (add member + source)
- `/home/lubo/Work/seq-pipeline/gain/uv.lock` (regenerated)
- `/home/lubo/Work/seq-pipeline/gain/web_api/.gitignore` (add the
  runtime-generated `web_annotation/tests/fixtures/grr/grr_definition.yaml`
  — a leftover dead rule from web_infra/.gitignore)

## Reference files

- `/home/lubo/Work/seq-pipeline/gain/vep_annotator/pyproject.toml` —
  the canonical workspace-member template.
- `/home/lubo/Work/seq-pipeline/gain/web_api/environment.yml` —
  source of runtime deps.
- `/home/lubo/Work/seq-pipeline/gain/web_api/dev-environment.yml` —
  source of dev-group deps.

## Risks and known unknowns

- **Resolver conflicts**: The most common failure mode is a transitive
  dep version disagreement (e.g., `pyyaml`, `requests`, `urllib3`,
  `cryptography`). uv usually resolves these by upgrading. If a
  resolver error appears, the fix is almost always to widen a
  constraint on the web_api side.
- **`python-magic`**: pure-Python wrapper for libmagic; the system
  library has to be available at runtime. The conda env provides it;
  pip installation in a barebones venv may need
  `apt-get install libmagic1` separately. Not a Phase 3 problem.
- **Django version drift**: PyPI's latest `django>=5.2,<5.3` resolves
  to `5.2.13` while conda pins `5.2.5`. Both satisfy the constraint;
  this is acceptable for the workspace but the docker image uses the
  conda-pinned version at runtime.
- **Hatch-vcs version derivation**: the package version is derived
  from git tags. With no matching tag,
  `fallback-version = "0.0.0.dev0"` applies, identical to the other
  workspace members.

## Verification end-to-end

```bash
cd /home/lubo/Work/seq-pipeline/gain

# 1. Workspace resolves
uv sync --all-packages --all-groups

# 2. No drift
uv lock --check

# 3. Django backend imports work via uv
uv run --package django-gpf-web-annotation python -c \
  "import django, channels, rest_framework, daphne; print(django.VERSION)"

# 4. Django self-check
DJANGO_SETTINGS_MODULE=web_annotation.test_settings \
  uv run --package django-gpf-web-annotation \
  python web_api/manage.py check

# 5. Existing gain tests still pass (sample)
cd core && uv run pytest -n 4 tests/small/utils/
```

## Outcome

Phase 3 landed on `master`:

- Commit: `Promote web_api to a uv workspace member (Phase 3)`
- All five verification steps above pass.
- `uv.lock` grows from 3172 to 3644 lines (Django stack added).
- 277 `gain-core` tests pass under `uv run`, confirming no regression.
- `python web_api/manage.py check` reports "System check identified
  no issues".

Phase 4 (consolidate conda envs) is the next step: fold
`web_api/environment.yml` and `web_api/dev-environment.yml` into the
root `environment.yml` and `dev-environment.yml` so the conda
workflow mirrors what uv now provides.
