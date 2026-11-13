# 40. `gain-core` is distributed through PyPI and bioconda

- **Status:** accepted
- **Date:** 2026-10-27
- **Amended:** 2026-11-13 by [gain#1967](https://github.com/iossifovlab/gain/issues/1967).
  `iossifovlab` keeps `gain-core` for every release. See [Amendment — gain#1967](#amendment--gain1967-gain-core-stays-on-iossifovlab).
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
   `Pre-flight: PyPI` runs `scripts/pypi_probe.sh` before `Setup`. The stage
   aborts when the tag version is on PyPI and also on the wheel index. When
   the tag version is on PyPI only, the run resumes a partial publish. A
   `DRY_RUN` never aborts. `gain-web-api` stays off PyPI.
3. The anaconda.org channel `iossifovlab` keeps `gain-core` until bioconda has
   carried two monthly releases and gpf#1071 has landed. Only when both
   conditions hold does the upload to the channel stop.
   *Reversed: the channel keeps `gain-core` for every release. See [Amendment — gain#1967](#amendment--gain1967-gain-core-stays-on-iossifovlab).*
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

*The channel does not go away. See [Amendment — gain#1967](#amendment--gain1967-gain-core-stays-on-iossifovlab).*

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
  *The retirement is cancelled. See [Amendment — gain#1967](#amendment--gain1967-gain-core-stays-on-iossifovlab).*
- The projects are to be owned by the PyPI organization `iossifovlab`. The
  move of `gain-core` into the organization is gain#2053. Until gain#2053 is
  done, `gain-core` belongs to a personal account.
- A release now reaches bioconda days after the tag. A user who needs a
  release on the day of the tag installs it from PyPI or from the
  anaconda.org channel.

## Amendment — gain#1967: gain-core stays on iossifovlab

*Added 2026-11-13. Everything above is the original record and is left as written.*

The anaconda.org channel `iossifovlab` keeps `gain-core` for every release.
`gain-release` does not change: it already uploads `gain-core` to the channel
for every release. This amendment reverses decision 3 of this record. It also
records the reversal of decision 5 of the epic
[gain#1958](https://github.com/iossifovlab/gain/issues/1958), on 2026-11-13.

We keep the channel for three reasons:

1. A gpf release pins `gain-core ==GAIN_PIN_VERSION` in its conda recipes.
   The gpf release often comes on the day of the gain release.
2. Bioconda lags a gain release by days. On 2026-11-13, PyPI and `iossifovlab`
   had 2026.11.1, and bioconda did not. The BiocondaBot opened no bump for
   2026.11.0. A maintainer made that bump by hand in
   [bioconda/bioconda-recipes#71386](https://github.com/bioconda/bioconda-recipes/pull/71386).
3. With strict channel priority, the first channel in the list that has
   `gain-core` hides the others. When bioconda is first and lags, a gpf
   release cannot be installed.

The BiocondaBot opened
[bioconda/bioconda-recipes#71549](https://github.com/bioconda/bioconda-recipes/pull/71549)
for 2026.11.1 on 2026-11-12. On 2026-11-13 that PR was still open. The bot now
opens bumps, but bioconda still lags.

The gpf side, the probe of `gpf-release` and the install order, is
[iossifovlab/gpf#1181](https://github.com/iossifovlab/gpf/issues/1181).
