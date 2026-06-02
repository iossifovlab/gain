Release Notes
=============

* 2026.6.0
    * ``grr_cache_repo`` now shows live progress while caching instead
      of a wall of per-file log lines. On a terminal it draws a
      byte-based ``tqdm`` bar (human-readable units, throughput and ETA)
      with a ``files=done/total`` and, when applicable, ``failed=N``
      tally; off a terminal (e.g. a captured CI log) it instead emits
      throttled milestone ``INFO`` lines at a 0% baseline, every 10%
      crossing, and 100%. Per-file request/finished chatter is demoted
      to ``DEBUG``. Caching first classifies the full work-list — logging
      a ``caching N file(s), B bytes to download; M already cached``
      header — and then downloads only the files that are missing or
      stale, so a fully-cached re-run prints no bar. A failed download
      still advances the bar to 100% (credited with the file's size and
      counted in ``failed=N``) and is reported in the end-of-run
      summary. The ``--no-progress`` flag turns the indicator off
      entirely. ``tqdm`` is now a core runtime dependency.
    * Raised the GRR caching protocol's application-level read-buffer
      (``CHUNK_SIZE``) from 32 KiB to 1 MiB. This cuts the per-chunk
      Python read/write/md5 and progress-callback overhead roughly 32×
      on multi-gigabyte resources, with negligible extra memory and no
      change to network behavior (fsspec still owns block-level
      fetching); small files are unaffected.
    * Revised the getting-started CLI and web tutorials, refreshed their
      figures, and updated the example variant files.
    * ``grr_cache_repo`` now takes the annotation pipeline as a
      **positional argument** (``grr_cache_repo <pipeline>``) instead of
      the ``--pipeline``/``-p`` flag, adopting the same
      ``CLIAnnotationContextProvider`` mechanism as ``annotate_columns``
      and ``annotate_vcf``. **Backward-incompatible:** scripts passing
      ``--pipeline``/``-p`` must drop the flag. When a GPF instance is
      also supplied (``-i``), an explicit positional pipeline still wins
      and the instance pipeline is used only as a fallback; with no
      pipeline from any source the command logs ``no pipeline supplied;
      nothing to cache`` and exits cleanly.
    * ``annotate_tabular`` and ``annotate_vcf`` now treat ``.bgz`` as a
      first-class compression extension on par with ``.gz``, for both
      input and output. A ``.bgz`` input is read — and, when
      tabix-indexed, split by genomic region — exactly like a ``.gz``
      one, and an explicit ``.bgz`` output suffix is now preserved
      (previously the output was always rewritten to ``.gz``). When the
      output name omits a compression suffix, a compressed input's
      suffix is mirrored onto it. This also fixes ``build_output_path``
      mangling output names whose stem ended in ``g``/``z`` characters
      (e.g. ``log.gz``), a side effect of the previous ``rstrip``-based
      suffix handling. As a consequence, the default ``--work-dir`` name
      derived from a ``.bgz`` output changed (e.g. ``out.vcf.bgz`` now
      yields ``out_work`` rather than ``out.vcf_work``). A run that was
      interrupted on an older version with a ``.bgz`` output and is
      resumed after upgrading will not find its old ``.task-status``
      directory and will restart from scratch — point ``--work-dir`` at
      the previous ``<output-stem>.vcf_work`` path to continue from the
      checkpoint. (``.gz`` outputs are unaffected.)
    * Fixed silent duplication of records when an ``annotate_tabular``
      input was a compressed file carrying a ``.csi`` index rather than
      a ``.tbi`` one. The splittability check accepted either index, but
      the reader looked only for ``.tbi`` and otherwise opened the file
      whole, so every genomic-region part re-read and re-emitted the
      entire file.

* 2026.5.10
    * ``grr_cache_repo`` no longer aborts a long HTTP download at
      htslib's 300 s read cap (which killed caching of large
      resources such as the genome-wide gnomAD file): the HTTP
      filesystem now applies no overall timeout, only per-read and
      per-connect limits. A single failed file is retried with
      exponential backoff and, if it still fails, reported in a
      summary rather than discarding every other download's
      progress. Caching is resumable, so a re-run only refetches
      what failed.
    * The anonymous-quota refresh commands (``refreshdaily`` and
      ``refreshmonthly``) now reset each quota and write their
      refresh-log entry inside a single transaction, so an
      interruption mid-run rolls back cleanly instead of leaving
      quotas half-refilled. They also now reset ``SessionQuota``,
      which was previously left untouched and so became a
      permanent floor on the effective anonymous quota.
    * Added a version API endpoint.

* 2026.5.9
    * ``annotate_vcf`` and ``annotate_tabular`` now run a
      pre-flight locality check: when the pipeline uses non-local
      genomic resources (http/https/s3, queried over the network
      per variant) and the input is large, they warn (1001–5000
      rows) or abort before doing any work (more than 5000 rows).
      Local resources — file/memory schemes or anything behind a
      caching protocol — never trip the guard, and
      ``--allow-remote-resources`` disables it entirely.
    * The GRR index table now supports cascading column resize.
    * Fixed a GRR summary tooltip being truncated when a
      biosample description contained a double quote.

* 2026.5.8
    * Added a ``prepare_tabular`` CLI that sorts a (optionally
      gzip-compressed) tabular file by genomic coordinates and writes
      a bgzip-compressed, tabix-indexed output, so that
      ``annotate_tabular`` can parallelize annotation across genomic
      regions. It reuses the same ``--col-*`` options to derive the
      sort and tabix keys, and orders chromosomes by a reference
      genome when one is supplied (lexicographically otherwise).
    * ``annotate_tabular`` (and the deprecated ``annotate_columns``
      alias) now defaults ``--input-separator`` to a comma when the
      input filename has a ``.csv`` extension (optionally ``.gz`` or
      ``.bgz`` compressed); all other inputs still default to a tab.
      An explicit ``--input-separator``/``--in-sep`` always takes
      precedence.
    * ``annotate_tabular`` and ``annotate_vcf`` now run sequentially
      (forcing ``-j 1``) when the input cannot be split into genomic
      regions — when it has no tabix index, or ``--region-size`` is
      zero or negative — avoiding needless parallel-executor startup
      overhead for what is a single-task run.
    * ``annotate_tabular`` and ``annotate_vcf`` now run inside their
      ``work_dir``, so the ``.tbi`` index files htslib downloads for
      tabix/VCF score resources served over an http(s) GRR land in
      ``work_dir`` instead of littering the directory the tool was
      launched from. Path-bearing CLI arguments are absolutized
      first, so the change of working directory is transparent.
    * Fixed flicker and column-resize lag in the GRR browser index
      table.
    * Expanded the getting-started CLI tutorial with parallelization
      and resource-caching sections.

* 2026.5.7
    * Moved the ``grr_cache_repo`` CLI from ``gpf`` into
      ``gain``.
    * Fixed the ``--version`` label on the ``grr_manage``,
      ``grr_browse``, ``annotate_columns``, and ``annotate_vcf``
      CLIs.
    * The GRR index table now supports sorting by the ID column
      and resizing columns by dragging.
    * Silenced the spurious htslib
      ``[W::hts_idx_load3] The index file is older than the data file``
      warning emitted when reading parallel-downloaded GRR resources
      (caching protocol or DVC). htslib verbosity is now level 1
      (errors only) for any process that imports
      ``gain.genomic_resources.fsspec_protocol``.
    * Revised the getting-started CLI tutorial and refreshed the
      overview diagram.

* 2026.5.6
    * Renamed the ``annotate_columns`` CLI to
      ``annotate_tabular``. The old name is kept as a deprecated
      alias (stderr banner on the CLI, ``DeprecationWarning`` on
      ``import gain.annotation.annotate_columns``) and will be
      removed in a future release.
    * The web UI now runtime-injects the Google Analytics
      snippet from the ``GA_MEASUREMENT_ID`` container
      environment variable, so the same image runs with or
      without GA depending on the host's deploy-time config.
    * Improved the getting-started CLI documentation with
      installation prerequisites.
    * The notifications WebSocket now retries on transport-level
      errors (e.g. a 502 during handshake) after a 2 s delay,
      preventing the subscription from dying permanently.
    * Fixed empty-array table header rendering and scrollable
      grid alignment in the single-annotation report.

* 2026.5.5
    * Moved the ``to_gpf_gene_models_format`` CLI from ``gpf`` into
      ``gain``.

* 2026.5.4
    * Made ``.CONTENTS.json.gz`` and ``.CONTENTS.sqlite3.gz``
      byte-reproducible across platforms.

* 2026.5.3
    * Refactored the allele score annotator: its default mode now
      operates only on VCF alleles, and the legacy
      ``allele_aggregator`` attribute was deprecated in favor of
      ``aggregator``.
    * Fixed VCF processing where incorrect end positions caused
      spanning records to be skipped, and corrected how allele
      scores access positions.
    * Standardized canonical annotator names throughout the
      documentation and fixed attribute-selection bugs in the
      new-annotator UI.
    * Fixed a race condition when filtering annotators.
    * URL-encode lists, tuples, and dicts when stringifying
      annotation attributes.
    * Improved GRR browser page styles and table layout, and
      refactored the templates for visual cohesion.
    * Fixed broken annotation infrastructure links.
    * Updated the FTS search database when creating the contents
      file and fixed a statistics-manifest bug.
    * The single-annotation report now handles array result
      values.

* 2026.5.2
    * Added admin panel views for managing anonymous users and their
      quotas; monthly quotas are now always displayed.
    * Anonymous-user quotas are now tracked by session ID.
    * Restyled the GRR repository about and index pages.
    * Introduced a new template infrastructure for resource
      implementations.
    * Fixed BigWig score-definition validation.

* 2026.5.1
    * Imported the GAIn user documentation into the repository and
      added Build/Deploy docs CI stages.
    * Updated the quotas page UI and removed quotas from the about
      page.
