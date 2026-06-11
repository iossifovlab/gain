Release Notes
=============

* 2026.6.6
    * Fixed ``annotate_tabular`` crashing with ``UnicodeDecodeError``
      when the bgzip/tabix input contained non-ASCII characters. The
      tabix reader opened the input without an encoding, so pysam
      decoded lines as ASCII; since the tool writes its own output as
      UTF-8, reannotating a previous run that carried a non-ASCII value
      (e.g. a ClinVar ``clinical_disease_name`` such as
      ``Roussy-Lévy_syndrome``) failed. The input is now read as UTF-8.
    * Default-verbosity runs no longer print ``INFO:distributed``
      messages from dask. ``VerbosityConfiguration`` silenced the
      ``distributed`` logger at startup, but importing ``distributed``
      (done lazily when the cluster starts) reset its level back to
      INFO; it is now re-silenced after the cluster is built.
    * The dask cluster is now torn down gracefully in
      ``DaskExecutor.close()``. The previous
      ``retire_workers(close_workers=True)`` before ``shutdown()`` raced
      the scheduler teardown and flooded the log with worker heartbeat
      failures and "Connection ... closed" lines.
    * Task graph executor teardown is now best-effort: a failure while
      releasing executor resources (e.g. a dask worker shutdown timeout
      when workers hold large resources) is logged and no longer crashes
      a run whose results are already written. This applies to every
      task-graph tool (``annotate_tabular``, ``annotate_vcf``, the
      effect-annotation tools, ...).
    * The fsspec "protocol with id ... already exists" message was
      demoted from warning to debug.
    * ``grr_manage`` repo-repair now writes each resource's
      ``index.html`` and ``statistics/index.html`` only when the
      rendered content actually changed. Re-running repair on an
      unchanged repository previously rewrote every page and bumped its
      mtime, which defeated mtime-based GRR consumers (notably the
      ``gs://iossifovlab-grr`` bucket-publish pipeline, which then
      re-uploaded hundreds of unchanged pages).
    * Web UI: the new-annotator workflow gained an aggregation
      configuration step for selecting per-attribute (gene) aggregators,
      including fetching the available aggregators and supporting their
      parameters.
    * CI: docs-only ``gain`` master builds now also archive a
      ``gain-core`` conda package, so downstream jobs that copy the last
      successful build's conda artifact no longer fail with zero matches.

* 2026.6.5
    * ``annotate_tabular`` and ``annotate_vcf`` now print a
      reannotation plan. A reannotation run emits an always-on stderr
      summary classifying each output attribute as ``COPIED`` (reused
      unchanged), ``ADDED``, ``COMPUTED`` (recomputed) or ``DELETED``,
      and the new ``--dry-run``/``-n`` flag prints the plan and exits
      without annotating. The shared "load previous pipeline + wrap in
      a ``ReannotationPipeline``" construction and the plan-printing
      helper were factored into a single implementation used by both
      CLIs.
    * Reannotation reuse now works through the CLI. The
      framework-injected ``work_dir`` is excluded from
      ``AnnotatorInfo`` identity, so an unchanged annotator's old and
      new infos compare equal and its result is copied instead of
      silently recomputed. As part of this, ``annotate_tabular`` no
      longer emits a reused (``COPIED``) attribute twice — the
      leftover input column is dropped on reannotation runs only;
      plain append-columns behavior is unchanged.
    * Fixed ``--full-reannotation`` leaving stale values: every
      annotator of the new pipeline is now forced to recompute, so no
      output column is deleted-but-not-recomputed.
    * Fixed a reannotation crash (``TypeError: unhashable type``) when
      a pipeline contained an annotator with a dict- or list-valued
      parameter — the canonical trigger being the built-in
      ``chrom_mapping`` annotator's inline ``mapping``. ``AnnotatorInfo``
      and ``ParamsUsageMonitor`` hashing is now order-normalized and
      consistent with their order-insensitive equality.
    * Fixed gzipped gene-score files: a ``.tsv.gz`` score file was read
      with a comma separator (so the whole row became one column),
      which raised ``KeyError`` on the score id during ``grr_manage``
      repo-repair. The ``.gz`` suffix is now stripped before the
      ``.tsv`` extension check.
    * Building gene-score histograms no longer rebuilds a
      ``GeneScoresDb`` per score. The small/large value descriptions
      are read directly from the score definition, removing both the
      O(n²) rebuild and the spurious "unable to load histogram file"
      error and traceback emitted on a first-time stats build (e.g.
      ``grr_manage`` repo-repair) for scores whose histograms had not
      been written yet.
    * Added a GRR definition-files page to the documentation describing
      the structure and use of ``.grr_definition.yaml`` files, and
      made further edits to the getting-started CLI tutorial.

* 2026.6.4
    * ``grr_manage`` no longer rebuilds the full-text search SQLite
      index when the repository contents are unchanged: before
      regenerating it, the existing ``.CONTENTS.sqlite3.gz`` is opened
      and its stored ``contents_md5`` compared against the current
      contents hash, and the rebuild is skipped on a match. This
      complements the content-hash embedding added in 2026.6.1.
    * Revised the getting-started CLI tutorial — added a local-GRR
      walk-through and VCF parallelization and region-annotation
      sections, and shrank and refreshed the example variant files
      (the 50k and SSC inputs). The obsolete standalone GRR
      YAML/repository reference pages were removed.

* 2026.6.3
    * The annotation web API now supports a configurable default
      pipeline via the ``DEFAULT_PIPELINE`` setting (shipping as
      ``pipeline/hg38_clinical_annotation``). The pipeline-list
      endpoint moves the default to the front of the list so the UI
      pre-selects it; if the configured id is not among the available
      pipelines the endpoint fails fast with a 500 and an explanatory
      ``reason``. Setting ``DEFAULT_PIPELINE`` to ``None`` disables the
      behavior.
    * ``annotate_tabular`` and ``annotate_vcf`` now accept a GRR
      resource id (not only a filesystem path) as the ``--reannotate``
      argument, mirroring how ``--pipeline`` already works. A
      ``--reannotate`` value that is not an existing file is now treated
      as a sentinel (e.g. a GRR pipeline resource) rather than being
      absolutized or added to the task graph as an input file.
    * Removed the per-file variant limit. The annotation web API no
      longer caps the number of variants in an uploaded file, and the
      associated setting, its server-side enforcement, and the
      frontend display of the limit were all removed.
    * Web UI: building on the ``annotate_doc`` endpoint added in
      2026.6.2, saved pipelines now expose a download link to their
      generated HTML documentation.
    * Fixed the single-variant annotation API failing when the
      pipeline contained an annotator with no GRR resources (for
      example a ``chrom_mapping`` annotator with a non-internal output
      attribute): the histogram lookup is now skipped when the
      annotator has no resource ids instead of raising.
    * Fixed annotation-config serialization emitting ``internal: null``
      for attributes whose ``internal`` flag is unset; the field is now
      omitted when unset, so saved and re-loaded pipeline configs
      round-trip cleanly.
    * Web UI: job notifications now arrive in a guaranteed order; number
      histograms can show multiple red value markers; and the available
      pipelines are refetched when the login state changes (sign-in or
      sign-out), so user-private pipelines appear and disappear
      correctly.
    * Revised the getting-started CLI tutorial — added a reannotation
      walk-through, a larger SSC example, and custom-pipeline examples —
      and documented the ``gene_column`` resource in the GRR guide.

* 2026.6.2
    * When the output file is not given explicitly, ``annotate_tabular``
      and ``annotate_vcf`` now derive the default output name by
      inserting a ``.annotated`` marker before the suffix instead of
      ``_annotated`` (e.g. ``variants.vcf.gz`` now yields
      ``variants.annotated.vcf.gz`` rather than
      ``variants_annotated.vcf.gz``). An explicit ``-o``/``--output``
      name is used verbatim and is unaffected.
    * ``annotate_tabular`` and ``annotate_vcf`` now delete the working
      directory they created once the annotation finishes successfully,
      instead of leaving the intermediate parts and task-status files
      behind. Removal happens only for a directory the tool itself
      created (a pre-existing ``--work-dir`` is preserved), only on a
      clean ``run`` (a failed or partial run keeps the directory so it
      can be resumed), and never when the output is written inside the
      working directory. The new ``--keep-work-dir`` flag opts out and
      keeps the directory in all cases; ``--keep-parts`` continues to do
      so as well. As part of this, ``annotate_vcf`` now closes its
      annotation pipeline at the end of a run, matching
      ``annotate_tabular`` (previously it leaked the pipeline's open
      resources).
    * Added an ``annotate_doc`` pipeline-documentation endpoint to the
      annotation web API: it renders a standalone HTML description of a
      saved pipeline — its annotators, resources and score histograms —
      and serves it as a downloadable ``<pipeline_id>.html`` attachment.
    * Fixed several edge cases in the ``allele_score`` annotator's filter
      grammar: negative numeric literals are now accepted, and
      identifiers that start with a digit are tokenized correctly rather
      than being misread as malformed numbers.
    * Web UI: the application version is now shown on the About page;
      list-valued attributes now render correctly in the single-variant
      annotation view (previously they were dropped); and filled-in note
      labels are rendered more prominently.

* 2026.6.1
    * Reference genomes may now be supplied as bgzipped FASTA in
      ``pysam.FastaFile`` format, not only as a plain uncompressed
      ``.fa``. A genome resource whose ``filename`` ends in ``.gz`` or
      ``.bgz`` is opened through ``pysam.FastaFile`` for random access;
      it must be accompanied by a ``.fai`` faidx index and a ``.gzi``
      bgzip index (both produced by ``samtools faidx`` on a
      ``bgzip``-compressed FASTA), and an optional ``index_file`` config
      field can point at a non-default ``.fai`` path. Bgzipped genomes
      work whether the repository is local, cached, or accessed directly
      over HTTP/S3, including on an S3-backed cache; the ``.fai`` and
      ``.gzi`` indexes are cached on first open, and genome-wide
      statistics fetch in 1 MiB windows to keep remote reads efficient.
      This is documented in the Genomes section of the GRR guide.
    * ``annotate_columns`` was renamed to ``annotate_tabular`` to better
      reflect that it annotates arbitrary tab-/whitespace-delimited
      tabular files. ``annotate_columns`` remains as a deprecated alias,
      so existing scripts keep working.
    * Reworked the annotator class hierarchy so that all built-in
      annotators inherit a common ``AnnotatorBase`` and share a single
      attribute-handling and aggregation implementation. Along with the
      refactor this fixes several attribute bugs: the chromosome-mapping,
      liftover and normalize-allele annotators now write to their
      configured output attribute names (renaming an output in the
      pipeline config now takes effect), the gene-set annotator emits its
      attribute under the correct name, gene-score annotators support
      aggregation again, and ``annotatable``-typed attributes no longer
      attempt (meaningless) aggregation.
    * The GRR static index now embeds a content hash of the full-text
      search SQLite database, so a browser re-fetches the search index
      after the repository's contents change instead of serving a stale
      cached copy.
    * Resource index links in the generated GRR pages are now relative,
      so a mirrored or relocated repository's links resolve correctly.
    * Pinned ``pysam`` to 0.24.0 (and unpinned htslib/samtools/bcftools
      in the conda environment), and declared ``tqdm`` as a runtime
      dependency of the ``gain-core`` conda recipe.

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
