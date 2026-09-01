# GAIn Monorepo — Agent Guide

This file provides guidance to Claude Code when working
with code in this repository.

## Project Overview

GAIn (Genomic Annotation Infrastructure) is the
annotation engine and genomic resource framework used by
the GPF (Genotypes and Phenotypes in Families) system.
This repository hosts `core` plus a set of
annotator plugins.

## Environment Setup

Two supported workflows — pick one.

### Conda/Mamba

```bash
mamba env create --name gain --file ./environment.yml
mamba env update --name gain --file ./dev-environment.yml
conda activate gain

pip install -e core
pip install -e demo_annotator     # optional
pip install -e vep_annotator      # optional
pip install -e spliceai_annotator # optional
```

### uv workspace

The repo root is a virtual `gain-monorepo` project
(`[tool.uv] package = false`) that coordinates a
`[tool.uv.workspace]` of five members: `core`, `web_api`,
`demo_annotator`, `vep_annotator`, `spliceai_annotator`.
Runtime deps live in each member's pyproject; dev tools
live in each member's own `dev` dependency group.
`uv.lock` is committed. Default `uv sync` installs only
`gain-core` + `gain-web-api`; the annotator
plugins are workspace members but optional.

```bash
uv sync                              # core + web_api only
uv sync --all-packages --all-groups  # everything
uv sync --package gain-spliceai-annotator --group dev   # just one
source .venv/bin/activate   # optional; `uv run` works without activation
```

## Commands

### Testing

```bash
# Run a single test file
cd core && pytest -v tests/small/path/to/test_file.py

# Run a test module
cd core && pytest -v tests/small/module/

# Run GAIn tests in parallel
cd core && pytest -v -n 10 tests/
```

Test markers in `core/pytest.ini`: `grr_rw`,
`grr_local`, `grr_full`, `grr_http`, `grr_tabix`.

All tests run with `PYTHONHASHSEED=0`.

The GRR info pages' client-side JavaScript is not
covered by pytest — gain-core's CI image has no JS
runtime. It is driven in a browser by `info_pages_e2e`,
which needs its fixture pages generated first:

```bash
uv run python info_pages_e2e/generate_fixtures.py \
    info_pages_e2e/fixtures/grr
cd info_pages_e2e && npm ci && npx playwright test
```

No server and no network: the pages are opened over
`file://`. See `info_pages_e2e/README.md`.

### Linting and Type Checking

Run these from `core/`, as CI does — the `core` image's
WORKDIR is `/workspace/core`, and `gain` is `core/gain`.

```bash
# Ruff linting (fast, primary linter)
cd core && ruff check --fix .

# Type checking (slow)
cd core && mypy --config-file ../mypy.ini gain

# Pylint (CI runs this too — see below)
cd core && pylint --rcfile=../pylintrc gain
```

Config: `ruff.toml` (line-length: 80, target: py312),
`mypy.ini`, `pylintrc` — all at the **repo root**, hence
the explicit `--config-file` / `--rcfile`. Ruff needs no
flag: it searches upward and finds `ruff.toml` on its own.

**Suppress ruff with `# ruff: ignore[rule-name]`, not
`# noqa`.** Ruff 0.16 deprecated both spellings this repo
used to rely on — `# noqa: ARG002` comments and rule *codes*
in `ruff.toml` selectors — and reports them as
`noqa-comments` / `rule-codes-in-selectors`. The whole tree
was converted in one pass, so a new `# noqa` is now the odd
one out and CI will flag it. The rule *name* is what goes in
the brackets (`unused-method-argument`, not `ARG002`); the
old code is kept in a trailing comment beside each
`ruff.toml` entry so grepping this file for a code quoted in
an old commit or issue still lands on the right row.

Two things to know about the new spelling. Ruff parses the
literal text `# noqa` wherever it appears in a comment, so
prose *mentioning* a directive emits an "Invalid `# noqa`
directive" warning — write "the E402 directive", not the
directive itself. And ruff's own fixer drops a trailing
suppression when it reformats the statement under it to
multiple lines; `web_api/web_annotation/asgi.py` is where
that bit us.

**Two modules sit at exactly pylint's 1500-line cap**, so
*any* line added to them turns the build UNSTABLE on `C0302`
— including a comment. `web_api/web_annotation/models.py` is
one (a five-line comment here took it to 1505 and broke CI).
Ruff honours prose *after* the directive on the same line
(`# ruff: ignore[rule] -- why`), which is how a suppression
in these files keeps its reason at zero added lines. Anything
that genuinely needs the space wants the module split, or a
deliberate `# pylint: disable=too-many-lines` — there is
precedent in `genomic_resources/testing/builders.py`.

The cwd matters, and the two tools disagree about why:
`mypy gain` reads `gain` as a *path*, so it fails from the
root (`can't read file 'gain'`), while `pylint gain` reads
it as an installed *module* and works from either. Passing
`mypy.ini` explicitly is what makes the local run match CI
— without it, mypy finds no config from `core/` (there is
no `core/mypy.ini`) and silently falls back to defaults
looser than the ones CI enforces.

**CI runs three Python linters, not two.** The `Jenkinsfile`
lint stage runs **ruff + mypy + pylint** on each package
(plus eslint + stylelint for `web_ui`), and any finding from
any of them marks the build **UNSTABLE**. Running only
`ruff` + `mypy` locally is *not* enough to predict the lint
stage — always run `pylint --rcfile=../pylintrc gain` from
`core/` before committing too. A common pylint-only catch
ruff/mypy miss:
`C0103` on a module-level `UPPER_CASE` constant that is
*reassigned* (e.g. inside a `try`/`except`), which pylint then
treats as a snake_case variable — assign such constants
exactly once.

### Pre-commit Hook

```bash
cp pre-commit .git/hooks/
```

The pre-commit hook runs `ruff check` (ignoring FIX
warnings) on staged `.py` files.

### Merging a PR — do not delete the branch right away

**Merge without `--delete-branch`.** The two branch-scoped
downstream jobs (`gain-web-e2e`, `gain-core-integration`)
are triggered from the root `Jenkinsfile`'s *last* stages
with `wait: false`, and resolve the branch at their own
start time — minutes later. `gain-web-e2e` additionally
loads its pipeline *definition* from the branch (on
purpose, so a `Jenkinsfile.e2e` change is testable on the
branch that introduces it — #272), so a branch deleted at
merge time kills it before any stage exists: a ~1s red
build that ran nothing and cannot classify itself (#489).

The root `Jenkinsfile` skips the trigger when it can see
the branch is already gone, but it cannot cover a deletion
that lands after the trigger fires, while the downstream
job is still queued. Letting the branch outlive the merge
by a few minutes is what actually closes the window.

`delete_branch_on_merge` is deliberately **false** on
`iossifovlab/gain` — leave it that way. Prune merged
branches in a periodic sweep instead, reviewing the list
before deleting:

```bash
git fetch --prune
# Review first — this is the list that would be deleted.
git branch -r --merged origin/master \
    | sed 's|origin/||' \
    | grep -vE '^\s*(master|HEAD)\b'
# Then delete them.
git branch -r --merged origin/master \
    | sed 's|origin/||' \
    | grep -vE '^\s*(master|HEAD)\b' \
    | xargs -r -n1 git push origin --delete
```

### Documentation (`docs/`)

The Sphinx user docs (rendered at
<https://iossifovlab.com/gaindocs/>) live in `docs/`. The
build pulls an auto-generated module tree from `core/gain`.

```bash
# Install Sphinx toolchain
uv sync --group docs

# Build HTML + tarball
bash docs/build_docs.sh
open docs/build/html/index.html
```

The Jenkinsfile has `Build docs` (every branch) and
`Deploy docs` (master only, ansible to iossifovlab.com).
Pre-move history lives in `iossifovlab/gpf_documentation`.

#### Do NOT edit `docs/source/changes.rst` in a feature PR

**Release notes are written after a release, not before it.**
A bugfix or feature branch must leave
`docs/source/changes.rst` untouched — do not add an entry
under `unreleased`, and do not create that section. The
release notes for a version are composed once, when that
version is cut.

This is the rule even though `git log` shows plenty of
past commits that did edit it alongside their code — those
predate the convention and are not the precedent to copy.
Every unreleased-section edit on a feature branch is also a
guaranteed rebase conflict with every other branch in
flight, since they all append to the same list.

Describe the user-visible change in the PR body instead;
that is what the release notes get composed from.

### Test Infrastructure (Docker)

Some tests require external services. Start them with:

```bash
docker compose up -d
```

Services defined in `docker-compose.yaml`:
- **MinIO** (ports 9000/9001) — S3-compatible object
  storage for S3 storage tests; credentials
  `minioadmin/minioadmin`, bucket `test-bucket`
- **Apache httpd** (port 28080) — HTTP fixture server
  for `grr_http` tests; serves
  `core/tests/.test_grr/`

## Architecture

### Architecture Decision Records

`docs/adr/` records decisions that shaped this
codebase — what was chosen, why it was scoped that
way, and what it cost. **Read the relevant ADR before
changing or extending the area it covers**; it is
where the reasoning lives that is otherwise spread
across issue threads and commit messages. See
`docs/adr/README.md` for the convention, including how
ADRs divide labour with the module-header "ledger"
docstrings that some `__init__.py` files carry.

These are internal records and sit deliberately
outside `docs/source/`, which is the published GAIn
documentation site.

### Domain language

`CONTEXT.md` at the repo root records the terms this
project uses for its own domain, and the ambiguities
that have actually caused bugs — which of two things
"searching by label" means, and what "the label is not
present in this GRR" can each be. It is grown a term at
a time as ambiguities are resolved, not maintained as a
complete glossary. **Check it before naming a concept
in an issue or a docstring**; an ADR explains why a
decision was made, `CONTEXT.md` fixes what the words in
it mean.

### Docstrings describe the present, not the past

**A class or method docstring says what the code does
now. It does not narrate how it got that way.** No
"renamed from X", no "this used to be a generator", no
"the try/except went with it", no benchmark numbers from
the change that produced the current shape, no "#239
examined and rejected this". That history is real and
worth keeping — it just belongs somewhere a reader is
not forced through it to learn what a method returns.

Where each thing goes:

| Content | Home |
| --- | --- |
| What it does, what it requires, what it returns, how to call it | the docstring |
| Why the code has this shape; a rejected alternative | an ADR (`docs/adr/`) |
| A name that changed or vanished on a package's public surface | that package's `__init__.py` ledger |
| What changed in this commit and why | the commit message |
| The measurement that justified a change | the ADR, or the PR body |

The test: read the docstring as someone who has never
seen the old code. Every sentence that still earns its
place is about the code in front of them. A sentence
that only makes sense if you knew the previous version
is history — cut it, and put it in the commit message.

This is a rule for *new and edited* docstrings. Many
existing ones predate it and still carry their history;
rewriting them wholesale is not the job of an unrelated
PR, but a docstring you are already editing should come
out the far side following the rule.

Why: the history accretes. A method whose docstring
grows a paragraph per change ends up costing more to
read than the implementation, and its oldest paragraphs
quietly stop being true — the reader cannot tell which
sentences describe the code and which describe a version
that no longer exists. `git log -p` and `git blame`
never go stale and cost nothing to carry.

### Package Structure

- **`core/`** — GAIn (Genomic Annotation
  Infrastructure): annotation engine, genomic resources,
  effect annotation, task graph, gene scores/sets.
  Python package: `gain`.
- **`spliceai_annotator/`**,
  **`vep_annotator/`**,
  **`demo_annotator/`** — external annotation
  plugins (Docker-based)

### Plugin System

GAIn uses Python entry points for extensibility.

**Defined in `core/pyproject.toml`:**

1. **`gain.genomic_resources.plugins`** — genomic
   context providers (DefaultRepository, CLI,
   CLIAnnotation)
2. **`gain.genomic_resources.implementations`** —
   position/allele/NP scores, liftover chain, genome,
   gene models, fragment score (config type
   `fragment_score`, legacy `cnv_collection` also
   accepted), annotation pipeline, gene score,
   gene set collection
3. **`gain.annotation.annotators`** — all built-in
   annotator types (score, effect, gene set, liftover,
   normalize allele, fragment score (config name
   `fragment_score`, legacy `cnv_collection` also
   accepted), chrom mapping, gene score, simple
   effect, debug)

Annotator plugins in this repo register additional
annotators via their own entry points.

### GAIn Submodules (`core/gain/`)

- **`annotation/`** — annotation pipeline engine,
  annotator base classes, all built-in annotators,
  processing pipeline, annotation config parsing
- **`genomic_resources/`** — Genomic Resource Repository
  (GRR): repository hierarchy (cached, group, factory),
  resource implementations, fsspec protocol, genomic
  context system. Sub-packages:
  - `gene_models/` — gene model parsing and
    serialization
  - `genomic_position_table/` — tabular data backends
    (tabix, BigWig, VCF, in-memory)
  - `implementations/` — resource type implementations
    (scores, genome, gene models, liftover, fragment
    score, annotation pipeline)
  - `statistics/` — resource statistics (min/max)
- **`effect_annotation/`** — variant effect prediction
  (effect types, effect gene/transcript annotation)
- **`task_graph/`** — DAG-based task orchestration
- **`gene_scores/`** — gene-level score resources and
  implementations
- **`gene_sets/`** — gene set collection resources and
  implementations
- **`dask/`** — dask named cluster configuration
- **`testing/`** — test fixture helpers for study import
  (acgt, alla, foobar, t4c8 datasets)
- **`utils/`** — shared utilities (fs_utils, helpers)

### Test Structure

`core` uses a `tests/small/` vs `tests/integration/`
split:
- `tests/small/` — unit/fast tests (default for
  development and CI)
- `tests/integration/` — tests requiring external
  services or longer runtime

Key conftest patterns:
- **`grr_scheme` parametrization** — tests tagged with
  `grr_rw`, `grr_full`, `grr_http`, `grr_tabix` markers
  are automatically parametrized across GRR protocols
  (inmemory, file, s3, http). Enable S3/HTTP with
  `--enable-s3-testing` / `--enable-http-testing`.
- Architecture tests in `core/tests/` use
  `pytestarch` to enforce the package's internal
  structure.

### Test data — prefer the builders

**Where a builder exists for the resource type, build
test resources with the fluent builders in
`gain.genomic_resources.testing.builders` rather than
hand-rolling a `genomic_resource.yaml` string next to a
`setup_tabix`/`setup_directories` call.**

```python
from gain.genomic_resources.testing.builders import (
    a_grr, a_position_score,
)

res = (
    a_position_score()
    .with_score("phastCons", "float")
    .with_data("""
        chrom  pos_begin  pos_end  phastCons
        1      10         12       0.1
    """)
    .with_tabix()          # omit -> plain .txt table
    .build_resource(tmp_path)
)
```

Factories: `a_position_score`, `a_np_score`,
`an_allele_score`, `a_fragment_score`, `a_bigwig_score`,
`a_vcf_info_score`, `a_gene_score`, `a_reference_genome`,
`a_grr`. Compose a
multi-resource repo with
`a_grr().with_resource(id, builder).build_repo(tmp_path)`;
`build_resource(tmp_path)` is the single-resource
shorthand.

Every builder also carries the resource-level `meta:`
block — `with_meta(summary=…, description=…)` and
`with_labels(**labels)` — through the shared
`MetaMixin` in
`gain.genomic_resources.testing.resource_meta`, so a
test can assert on `resource.get_summary()` /
`get_description()` / `get_labels()` without
hand-rolling yaml. Label keys are passed through
verbatim (the `with_chrom_mapping` precedent) and the
mapping is deep-copied; `with_meta` accumulates across
calls, `with_labels` replaces. Omit both and no `meta:`
key is emitted at all. A NEW builder gets this by
inheriting `MetaMixin` and either appending
`self.render_meta()` to the config text it renders or
calling `self.append_meta_into(resource_dir)` after a
`setup_*` helper wrote the config for it (the
reference-genome path).

**Three factories are NOT in `builders.py`** — import
each from its own sibling module:
`a_data_frame` from
`gain.genomic_resources.testing.data_frame_builder`,
`an_ann_data` from `…testing.ann_data_builder`, and
`a_gene_models` from `…testing.gene_models_builder`.
`builders.py` is ~1800 lines against pylint's
`max-module-lines=1500`, which it carries a
`too-many-lines` suppression for — so each new builder
lives in a sibling module that imports the shared
single-realize seam one way rather than growing a module
that is already over the limit; `builders` does not
import back, and there is no re-export. They compose
into `a_grr().with_resource(...)` like any other
builder.

`a_gene_models` authors transcripts ONCE, in gain's own
1-based inclusive coordinates, and `with_format` decides
which of the seven registered interchange formats
(`default`, `refflat`, `refseq`, `ccds`, `knowngene`,
`ucscgenepred`, `gtf` — exported as
`GENE_MODELS_FORMATS`) they are written down in. The
half-open shift the UCSC-derived formats need is the
renderer's job, not the test author's, and the emitted
config always names the format the data was actually
rendered in — `setup_gene_models` writes a literal
`format: "None"` when its `fileformat` is left unset, so
the builder always states one. Knobs:
`with_transcript(tr_name, exons=…, gene=…, chrom=…,
strand=…, cds=…)` (`gene` defaults to the transcript
name, `cds` omitted means non-coding),
`with_format` and `with_no_genes()` (the empty case,
realized by `setup_empty_gene_models`; it combines with
neither of the other two and says so). Two of the seven
formats — `ccds` and `knowngene` — have a single name
column, so a gene label distinct from the transcript
name cannot survive being written in them; that is the
format, not the builder.

`a_data_frame`'s knobs are
`with_data` / `with_raw_content` (verbatim
text or bytes, for `parameters:` shapes and compressed
tables a whitespace block cannot
express), `with_format` (`csv`/`tsv`/`excel`, filename
follows), `with_file`, `with_parameters`,
`with_declared_format` (config only — how you build an
unknown or mismatched format) and
`without_file_key` / `without_format_key`. It
deliberately exposes no expected DataFrame: it parses the
authored block with pandas to realize xlsx, so handing
that frame back as an oracle would be circular on the
separator and dtype axes a `data_frame` test varies.
The next builder added should follow the same sibling-
module pattern rather than grow `builders.py`.

**That list is the whole of the coverage — the gaps are
large and structural, not an oversight to work around.**
There is no builder for `liftover_chain`,
`annotation_pipeline` or `gene_set_collection`, and no
`with_*` for
`default_annotation` or explicit
`chrom`/`pos_begin` `column_name`/`column_index`
mappings. Hand-rolled yaml is still the majority in
`core/tests` and is the correct answer for all of the
above — if you cannot find a factory for your resource
type, it very likely does not exist. Extending the
builders is welcome; contorting a fixture to avoid yaml
is not.

Why this is the default where it applies, not a style
preference:
- **The config and the data cannot drift, because the
  authored data header is the only description of the
  columns.** The emitted `table:` block names no columns
  at all (just `filename`/`format`, plus `zero_based` /
  `chrom_mapping` when asked); the declared scores
  render the `scores:` block, and tabix's
  `seq_col`/`start_col`/`end_col` are derived from the
  data header (`end_col = start_col` when there is no
  `pos_end`). A hand-written yaml plus an explicit
  `seq_col=…` states the same table twice, and a test
  whose two statements drift apart usually still passes
  — it just stops testing what it says it does.
  `with_header_mode("none"/"list")` is the one knob that
  moves the column description into the config — it
  realizes a *headerless* data file — and it still
  derives the config's `column_index:` mappings (or
  `header:` list) and the tabix index columns from that
  same authored header, so there is still only one
  declaration. `with_missing_header_mode()` deliberately
  realizes the gain#364 misconfiguration (headerless
  file, no `header_mode` key); the resource it builds
  does not open.
- **Builders are immutable** (frozen dataclasses; every
  `with_*` returns a NEW builder), so a shared base can
  be specialised per variation without leaking state.
  This is what makes "same data, two backends" a fact
  rather than a promise: derive both from one base and
  let `with_tabix()` be the only difference — see
  `core/tests/small/genomic_resources/genomic_position_table/test_overlapping_intervals.py`.
- The `setup_*` helpers in
  `gain.genomic_resources.testing`
  (`setup_directories`, `setup_tabix`, `setup_vcf`,
  `setup_genome`, `convert_to_tab_separated`, …) are the
  layer the builders delegate to. Reach for them
  directly only for a shape no builder covers, or when
  the malformed/handwritten config *is* the thing under
  test.

For study-import fixtures (pedigrees, denovo/VCF
studies) use the per-dataset **modules** under
`gain.testing` — `t4c8_import`, `acgt_import`,
`alla_import`, `foobar_import` — rather than assembling
a study by hand. `gain/testing/__init__.py` is empty, so
import the module, not the package:
`from gain.testing.t4c8_import import setup_t4c8_grr`.

### CLI Tools

**core CLIs:**
- `grr_manage` — genomic resource repository management
- `grr_browse` — GRR browser
- `annotate_tabular` / `annotate_vcf` / `annotate_doc`
  — annotation tools (`annotate_columns` is a deprecated
  alias of `annotate_tabular`)
- `annotate_variant_effects` /
  `annotate_variant_effects_vcf` — effect annotation

## Key Dependencies

- **Python 3.12**
- **DuckDB 1.5**
- **dask** — parallel computing
- **pandas 2.2**, **numpy 2.2**, **pyarrow >=18** — data
  analysis
- **pysam 0.23** — SAM/BAM file handling
- **pydantic 2.8** — data validation
- **lark 1.2** — parsing (GRR search grammar)
- **fsspec / s3fs** — filesystem abstraction + S3 access
- Dev: **ruff 0.16**, **mypy 1.15**, **pytest**,
  **pytest-xdist**, **pytestarch**


<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
