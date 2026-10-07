# verify-gain features

Each file here describes one user-facing CLI feature of `gain` and how
`verify-gain` drives it. The drive recipes and helpers are in `../SKILL.md`.

## Baseline preconditions

- The checkout has its own `.venv` from `uv sync` (core + web_api), so
  `.venv/bin/annotate_tabular` and `.venv/bin/grr_browse` exist and come from
  this checkout's source.
- `test_fixtures/mini-GRR` is initialised
  (`git submodule update --init test_fixtures/mini-GRR`); a fresh worktree has
  it empty.
- A run was launched (`scripts/launch.sh`) and Doctor (`scripts/doctor.sh
  <run id>`) passes.
- No docker, no network, no `~/.grr_definition.yaml`.

## Driving conventions

- Call the checkout CLI as `.venv/bin/<tool>` (or through `uv run`), never the
  bare name from `PATH`: a conda env can shadow it.
- Pass the run's GRR definition, `.verify/<run id>/scratch/grr.yaml`, as
  `GRR_DEFINITION_FILE` or `-g`.
- Run with `HOME` inside `.verify/<run id>/scratch/` and write every input,
  output and work directory there too.
- Use `-j 1` so a run is deterministic and a failure is one readable
  traceback.

## Proof rules

- A drive proves a feature only with its evidence kept in
  `.verify/<run id>/evidence/<feature>/`: the command, stdout, stderr, the
  exit code and the output file.
- A mutation is read back a second time by a separate command over the kept
  output, and the read-back compares exact expected values.
- Pick inputs whose expected values differ from each other and from the
  default (`0`, empty, `NA`), so a broken drive cannot pass by accident.
- The run writes only inside `.verify/<run id>/`; after Cleanup,
  `git status --porcelain` shows no run output.

## Feature index

| Feature file | CLI | GRR | Drive helper |
| --- | --- | --- | --- |
| [annotate-tabular.md](annotate-tabular.md) | `annotate_tabular` | mini-GRR, `mini_pipeline` | `scripts/drive-annotate-tabular.sh` |
