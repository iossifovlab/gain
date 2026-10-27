# 40. `gain-core` is distributed through PyPI and bioconda

- **Status:** accepted
- **Date:** 2026-10-27
- **Issues:** [gain#1958](https://github.com/iossifovlab/gain/issues/1958) (the epic),
  [bioconda/bioconda-recipes#70746](https://github.com/bioconda/bioconda-recipes/pull/70746)
  (the first bioconda recipe, `recipes/gain-core/meta.yaml`),
  children [gain#1959](https://github.com/iossifovlab/gain/issues/1959) (the hand
  upload of 2026.10.2 to PyPI),
  [gain#1960](https://github.com/iossifovlab/gain/issues/1960) (the PyPI upload in
  `gain-release`),
  [gain#1961](https://github.com/iossifovlab/gain/issues/1961) (LICENSE in the sdist),
  [gain#1962](https://github.com/iossifovlab/gain/issues/1962) (the bioconda-recipes PR),
  [gain#1963](https://github.com/iossifovlab/gain/issues/1963) (this record)

## Context

Before this decision, `gain-core` had no public package index. A user got it
from one of these places:

- the wheel index on wheels.seqpipe.org, which only our own tools read
- the anaconda.org channel `iossifovlab`, which `gain-release` fills from the
  rattler recipe `core/conda-recipe/recipe.yaml`
- a git checkout.

Bioconda is the channel where bioinformatics users look first. Bioconda does
not take uploads. It builds every package from a recipe in
`bioconda/bioconda-recipes`, and a person on the bioconda team reviews each
new recipe. The review adds days of lag between a release and the package on
bioconda.

The recipe needs a source archive. The `gain-core` sdist carries `PKG-INFO`,
so hatch-vcs resolves the version without git. The 2026.10.2 sdist carries no
`LICENSE` file, and bioconda requires a license file.

## Decision

1. The bioconda recipe builds from the PyPI sdist of `gain-core`. It does not
   build from a GitHub tarball, and it does not build from wheels.seqpipe.org.
2. `gain-release` uploads `gain-core` and the three annotators
   (`gain-vep-annotator`, `gain-spliceai-annotator`, `gain-demo-annotator`) to
   PyPI with `uv publish`. The upload is step 0 of `Publish`. The stage
   `Pre-flight: PyPI` runs `scripts/pypi_probe.sh` before `Setup` and aborts
   when the tag version is already on PyPI. `gain-web-api` stays off PyPI.
3. The anaconda.org channel `iossifovlab` keeps `gain-core` until bioconda has
   carried two monthly releases. Then the upload to the channel stops.
4. The BiocondaBot opens the bump PR after each release. Jenkins does not open
   bioconda PRs. The recipe maintainers are `lchorbadjiev` and `iossifov`.
5. The recipe test is a copy of the entry-point walk of
   `core/conda-recipe/tests/entry_point_walk.py`, plus `pip check`. The walk
   loads every `console_scripts` entry point and every `gain.*` entry point.
6. A run dependency that is missing from the bioconda recipe is added in the
   red bump PR. Gain CI has no guard for it.

Round one covers `gain-core` only. `gain-vep-annotator` gets its own recipe
later (gain#1966). The SpliceAI annotator and the demo annotator never go to
bioconda, but they go to PyPI, because the upload loop costs nothing more for
them.

### Why the PyPI sdist

Bioconda accepts a GitHub tarball. We chose PyPI for three reasons:

- A PyPI release also gives `pip` users a public install path. The same
  upload serves both channels.
- The sdist carries `PKG-INFO`. A GitHub tarball has no git metadata, so
  hatch-vcs cannot resolve the version from it.
- wheels.seqpipe.org is our own host. A recipe on bioconda that downloads from
  our host fails when the host is down.

### Why the recipe test is inline

The first plan copied `core/conda-recipe/tests/entry_point_walk.py` into the
recipe as a test file. The bioconda mulled test container does not get the
`test: files` of a recipe, so the script file is not there when the test runs.
The walk is now inline, in `python -c`. The inline walk is a second copy of
the same logic, and the two copies can drift.

### Why the anaconda.org channel stays for two releases

`gpf-release` aborts when `gain-core` for `GAIN_PIN_VERSION` is absent from
anaconda.org. The retirement of the channel waits for gpf#1071, which lets
the probe of `gpf-release` accept `gain-core` from bioconda. Two monthly
releases on bioconda show that the BiocondaBot bump and the review lag work
before the old channel goes away.

## Consequences

- The run dependencies now have three copies: `core/pyproject.toml`,
  `core/conda-recipe/recipe.yaml` and `recipes/gain-core/meta.yaml` in
  bioconda-recipes. A new run dependency must go into all three. The walk in
  bioconda CI catches a dependency that is missing from the third copy. It
  catches it only on the bump PR, after the release is tagged.
- A file name on PyPI is permanent. A tag that reached PyPI cannot be
  replaced, so a broken release needs a new tag. `uv publish --check-url`
  makes a re-run of a partial upload complete it. A re-run fails with a hash
  mismatch when an unpinned build backend changes the bytes of a file that
  PyPI already has. Then only a new tag fixes it.
- The 2026.10.2 sdist has no `LICENSE`. The 2026.10.2 recipe takes `LICENSE`
  from the 2026.10.2 git tag as a second source. Since gain#1961, the sdist of
  each later release carries `LICENSE`, so a bump can drop the second source.
- The bioconda recipe is noarch python. Its `run_exports` uses
  `max_pin=None`, because the versions are CalVer and a CalVer major number
  says nothing about compatibility.
- The retirement of the anaconda.org channel (gain#1967) waits for the change
  to the probe of `gpf-release` (gpf#1071). Until then, `gain-release` uploads
  `gain-core` to two conda channels.
- The PyPI organization `iossifovlab` owns the projects. The move of
  `gain-core` into the organization is gain#2053. Until it is done, the
  project belongs to a personal account.
- A release now reaches bioconda days after the tag. A user who needs a
  release on the day of the tag installs it from PyPI or from the
  anaconda.org channel.
