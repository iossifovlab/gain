# The pip images keep the PyPI pyBigWig wheel, which has no libcurl

GAIn does not build `pyBigWig` from its sdist (`pip install --no-binary
pybigwig`) in the images and venvs it installs with pip or uv. Those get
the PyPI manylinux wheel, which is compiled `-DNOCURL` and exports
`pyBigWig.remote == 0`: libBigWig's `urlOpen` classifies `http(s)://`
only `#ifndef NOCURL`, so on that build every url is taken for a local
path and `fopen` fails. Only two things in the stack have a curl-enabled
pyBigWig — the conda package (bioconda links it against conda-forge
libcurl) and the `python:3.14-slim` CI cell, where no cp314 wheel exists
and the sdist is compiled against the `libcurl4-openssl-dev` the CI
Dockerfiles install.

## Why this is out of scope

The question (gain#1394, filed from the diagnosis in gain#1392) was
whether `web_api/Dockerfile.production` and the annotator images should
compile pyBigWig from source so that a bigWig on a remote GRR opens over
https everywhere. It was closed as resolved by gain#1425, which examined
the same build split and settled it the other way.

**No deployment hands pyBigWig a url.** The only path that does is
`FsspecReadOnlyProtocol.open_bigwig_file` on an *uncached* `http`, `https`
or `s3` GRR. Every production definition is either a local directory or
a cached remote:

- `gain-infra` renders the gainweb GRR definition from
  `gainweb_grr_mounts`: each child is `type: directory` over a bind
  mount (`/grr`, `/grr_encode`), the `file` scheme, so pyBigWig gets a
  path. The two definitions the production image bakes
  (`web_api/scripts/grr-definition{,-dir}.yaml`) have the same shape.
- GPF's SFARI production definition
  (`data-sfari-production/.grr_docker_definition.yaml`) is `type: http`
  with a `cache_dir`. `CachingProtocol.open_bigwig_file` downloads the
  file through fsspec and opens it through the local protocol; the url
  never reaches libBigWig.

So a curl-enabled build would change nothing any deployment does today,
at the price of `build-essential` + `libcurl4-openssl-dev` in the
production builder stage and `libcurl4` in its runtime stage.

**The failure is no longer opaque.** Before gain#1425 the uncached path
on the wheel died with `RuntimeError: Received an error during file
opening!` and nothing said why. `open_bigwig_file` now refuses a remote
open when `pyBigWig.remote == 0` with an `OSError` that names the cause
and both remedies — give the repository a `cache_dir`, or install a
curl-enabled pyBigWig such as the bioconda build.
`test_bigwig_remote_build_guard.py` pins that under both builds, and
`docs/source/binning.rst` tells a user reading a remote bigWig directly
which of the two to pick. That is the durable record this issue asked
for; a compiled wheel would only have hidden the split again.

**The choice stays the operator's.** Whether to cache a remote GRR (a
full download of each bigWig, ~60 GB for the eight scores in the binning
example, whatever the window) or to install from conda is a deployment
decision. Compiling pyBigWig into the pip images would make it for them,
and would have to be kept in step with every future interpreter the
images move to.

## What is still in scope

**The CI images keep the headers.** `libcurl4-openssl-dev` stays in
`core/Dockerfile`, `web_api/Dockerfile` and the annotator Dockerfiles: on
an interpreter with no prebuilt wheel (3.14 today) uv builds the sdist,
and that build must find curl. The 3.14 cell is also the only CI arm
that exercises the `remote == 1` branch of the diagnostics helper
(gain#1392), which is why both builds run in the nightly matrix.

**Revisiting if the ground moves.** A deployment that wants an uncached
remote GRR on a pip install — or pyBigWig publishing a curl-enabled
wheel — reopens the build question; the refusal message and
`test_bigwig_remote_build_guard.py` are where a change would start.

## Prior requests

- iossifovlab/gain#1394 -- "the PyPI pyBigWig wheel is a NOCURL build
  (remote == 0), so every python:3.12/3.13-slim image opens no bigWig over
  https — confirm production reads bigWigs from a local GRR, or build
  pyBigWig from the sdist everywhere"
