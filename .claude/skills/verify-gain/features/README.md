# verify-gain features

Each file here describes one user-facing CLI feature of `gain` and how
`verify-gain` drives it. The drive recipes and helpers are in `../SKILL.md`.

## Baseline preconditions

- The checkout has its own `.venv` from `uv sync` (core + web_api), so
  `.venv/bin/annotate_tabular` and `.venv/bin/grr_browse` exist and come from
  this checkout's source. The same holds for `annotate_vcf`, `grr_manage`
  and `binning_tool`, and `.venv/bin/python` imports `h5py`.
- `test_fixtures/mini-GRR` is initialised
  (`git submodule update --init test_fixtures/mini-GRR`); a fresh worktree has
  it empty.
- A run was launched (`scripts/launch.sh`) and Doctor (`scripts/doctor.sh
  <run id>`) passes.
- No network and no `~/.grr_definition.yaml`. Only `http-grr.md` uses
  docker: it starts its own compose project, `verify-gain-<run id>`, from a
  local `httpd:latest` image, and Cleanup removes that project.

## Driving conventions

- Call the checkout CLI as `.venv/bin/<tool>` (or through `uv run`), never the
  bare name from `PATH`: a conda env can shadow it.
- Pass the run's GRR definition, `.verify/<run id>/scratch/grr.yaml`, as
  `GRR_DEFINITION_FILE` or `-g`. For the HTTP GRR, pass
  `.verify/<run id>/scratch/grr-http.yaml` as `-g`.
- Run with `HOME` and `TMPDIR` inside `.verify/<run id>/scratch/` and write every input,
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
- A drive that changes a GRR (`grr-manage`) works on a scratch copy of
  mini-GRR, never on `test_fixtures/mini-GRR`; after every run,
  `git -C test_fixtures/mini-GRR status --porcelain` prints nothing.

## Feature index

| Feature file | CLI | GRR | Drive helper |
| --- | --- | --- | --- |
| [annotate-tabular.md](annotate-tabular.md) | `annotate_tabular` | mini-GRR, `mini_pipeline` | `scripts/drive-annotate-tabular.sh` |
| [annotate-vcf.md](annotate-vcf.md) | `annotate_vcf` | mini-GRR, `mini_pipeline` | `scripts/drive-annotate-vcf.sh` |
| [grr-manage.md](grr-manage.md) | `grr_manage`, `grr_browse` | a scratch copy of mini-GRR | `scripts/drive-grr-manage.sh` |
| [binning-tool.md](binning-tool.md) | `binning_tool` | mini-GRR, `mini_genome` and `mini_positionscore_bw` | `scripts/drive-binning-tool.sh` |
| [http-grr.md](http-grr.md) | `grr_browse`, `grr_cache_repo` | HTTP GRR over a scratch copy of mini-GRR, `mini_pipeline` | `scripts/launch-http-grr.sh`, `scripts/drive-grr-browse.sh`, `scripts/drive-grr-cache-repo.sh` |
