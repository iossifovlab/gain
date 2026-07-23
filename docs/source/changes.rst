Release Notes
=============

* 2026.7.2
    * **Behavior change:** ``grr_manage`` repository management now
      reports any resource it cannot process and exits non-zero,
      instead of skipping it with a misleading ``WARNING`` and exiting
      ``0`` (#364). Failures are collected per resource (one broken
      resource no longer blocks repair of the healthy ones), the run
      ends with a summary naming every failed resource, and
      ``GRR <...> is consistent`` is logged only when nothing failed. A
      statistics task that fails during the task-graph run, the
      FTS-index builder and the statistics-hash writer now all fail the
      run. **A repository that was silently failing will now fail
      loudly**, including CI that gates on ``repo-repair``.
    * ``grr_manage`` now reports a configuration error (bad config,
      schema violation, missing file) as a single ``ERROR`` line
      carrying the cause, with the traceback demoted to ``DEBUG``
      (``-vv`` recovers it); other exceptions keep their traceback. A
      message-less exception is reported by its class name, an
      unrecognised management command is refused rather than falling
      through to a destructive repair, and a failed resource's
      generated ``index.html`` is left intact (#364).
    * **Behavior change:** a tabix table left on the default
      ``header_mode`` over a file with no ``#`` header line now raises a
      ``ValueError`` naming the resource, the table file and the remedy
      (``header_mode: none``), instead of a message-less ``assert`` that
      ``python -O`` removed (#364).
    * Exit statuses: a ``--dry-run`` exits with the count of resources
      needing an update, and a resource that could not even be checked
      now counts towards it (a repo with two stale and one broken
      resource exits ``3``, not ``2``); any other run exits ``1`` when
      anything failed and ``0`` otherwise. ``--dry-run`` is unchanged in
      what it writes (#364).
    * **Fixed:** ``grr_manage repo-manifest --dry-run`` reported a fully
      settled repository as inconsistent — its exit status was the
      repository size rather than the count of stale resources, so a
      settled three-resource repository exited ``3``. It now exits ``0``
      when nothing is stale (#364).
    * **Removed:** the in-resource aggregation engine (#267), a second
      aggregation path that had outlived its callers. Gone from
      ``gain.genomic_resources.genomic_scores``:
      ``PositionScore.fetch_scores_agg`` / ``.get_region_scores``,
      ``AlleleScore.fetch_scores_agg`` / ``.build_scores_agg``,
      ``PositionScoreQuery``, ``AlleleScoreQuery``, ``PositionScoreAggr``,
      ``AlleleScoreAggr`` and the ``ScoreQuery`` alias. Configure
      aggregation on the annotator instead; a caller that aggregated a
      region by hand should feed
      ``PositionScore.fetch_region_weighted_values`` to its own
      ``Aggregator``.
    * A resource may now configure ``position_aggregator: count`` /
      ``allele_aggregator: count`` and ``join()`` with an empty
      separator (#261): the resource-config schema is now derived from
      the aggregator registry, so registering an aggregator is the only
      edit needed. Resource-level aggregators remain string-only.
    * **Behavior change:** a region is now aggregated per **record**
      instead of per base pair (#260). A record covering N base pairs
      reaches the aggregator once with weight N, so a 500 kb region
      backed by 2,000 records drops from tens of milliseconds to single
      digits. ``Aggregator.add(value, count)`` now weights the value by
      ``count``. **Float position scores may shift in their last bits on
      large regions** (``mean`` only), toward the more accurate value;
      annotation output files (three significant figures) are unchanged.
    * **Behavior change:** a ``.dvc`` sidecar is now the authoritative
      md5 sum and size of the file it describes, and
      ``grr_manage --without-dvc`` (``-D``) is the only command that
      checks that claim against the bytes on disk (#251, #255, #373). In
      the default mode ``grr_manage`` never hashes a DVC-managed file
      and writes no ``.grr/*.state`` for a sidecar-derived md5; a
      sidecar whose declared size disagrees with the file is reported as
      a ``WARNING`` but still wins. ``--without-dvc`` hashes every
      materialised file, reports every file that disagrees with its
      sidecar, and exits non-zero writing no manifest for them (fix
      drift with ``dvc add`` / ``dvc commit``). Manifest membership is
      now decided by path, not extension: only the generated
      ``index.html`` and ``statistics/index.html`` are excluded and
      every other file is manifested whatever its extension. No manifest
      in any existing GRR changes.
    * **Behavior change:** ``grr_manage`` now **refuses** a resource
      with a ``dvc add <dir>`` output — a ``.dvc`` sidecar declaring a
      ``.dir`` md5 sum and/or an ``nfiles`` count — instead of
      describing it (#255), because GAIn cannot verify a ``.dir`` md5.
      Every manifest-building subcommand exits non-zero naming the file.
      Fix by DVC-managing individual files (``dvc add <file>``); no GRR
      uses ``dvc add <dir>`` today.
    * **Behavior change:** the ``--with-dvc`` (default) / ``-D``,
      ``--without-dvc`` option group is restored on the manifest, stats,
      repair and info subcommands (#251) — its removal in 2026.7.1 left
      no way to check a resource file's bytes against its published md5.
    * A malformed, unreadable or incomplete ``.dvc`` file no longer
      aborts ``grr_manage`` with a traceback (#251): it is reported as a
      warning and ignored, and is no longer written into the
      ``.MANIFEST`` as ``md5: null``.
    * **Fixed:** the ``DaskExecutor`` run loop leaked driver memory
      (#355): it polled the whole pending-future set every 50 ms with
      ``wait(FIRST_COMPLETED)``, which minted an uncancelled asyncio
      Task per pending future per poll (~220 MB/min, to 3.3 GB on a
      ``grr_manage resource-repair`` over a 5.5 GB bigWig). It now
      registers one ``add_done_callback`` per future at submit time and
      drains a queue, so waking up costs nothing per pending future.
    * **Fixed:** the ``DaskExecutor`` run loop could return zero results
      for a non-empty graph (#365): the submit worker removed a batch
      from the queue before ``Client.map()`` and recorded its futures
      only after it returned, so for the width of that call the task was
      in none of the tracked collections and the loop could declare
      itself finished and return empty. A batch now stays on the submit
      queue until ``running`` holds every future.
    * **Fixed:** rewrote the Dask run loop's termination logic around a
      single ``RunState`` six-state machine behind one lock (#367),
      replacing four separately-locked collections and a triple
      re-read; a task now occupies exactly one state from the moment it
      leaves the graph until its result is taken, closing a symmetric
      gather-side window that could silently under-deliver results.
    * **Fixed:** a crashed or OOM-killed Dask worker's task was yielded
      as ``None`` and cached as a COMPUTED ``None``, so a run of nothing
      but dead tasks reported success (#367): ``gather(errors="skip")``
      was handed a tuple, which ``distributed`` does not drop failed
      futures from, and now gathers a list. Generator teardown also
      moved into a ``finally`` so a ``keep_going=False`` failure no
      longer leaks both worker threads and leaves a long-lived
      ``web_api`` executor permanently "executing".
    * **Fixed:** an unguarded exception in either ``DaskExecutor``
      worker thread hung the run indefinitely (#372): a raise between
      claiming a batch and transitioning it out left the batch stranded
      in-flight, so ``has_outstanding()`` answered "yes" forever while
      the loop spun at its wait timeout with nothing logged. Both worker
      bodies now catch ``BaseException`` and deliver it as the per-task
      result, so the run terminates with an error the caller sees.

* 2026.7.1
    * **Behavior change:** a completed anonymous annotation job and its
      result file are no longer deleted when the user's last WebSocket
      disconnects (#216), so a captured download link no longer 404s.
      Stale anonymous jobs are reaped by age instead, by the new
      ``cleanup_anonymous_jobs`` management command
      (``ANONYMOUS_JOB_TTL_HOURS`` / ``GPFWA_ANONYMOUS_JOB_TTL_HOURS``,
      default 24), which never touches a ``WAITING`` or ``IN_PROGRESS``
      job. **Nothing in gain schedules the command** — a deployment that
      does not run it periodically will accumulate jobs without bound.
    * Closed the GRR credential-leak paths the 2026.7.0 redaction missed
      (#202): ``grr_browse`` no longer prints the raw definition, the
      definition models mask ``user`` / ``password`` in
      ``model_dump()`` / ``model_dump_json()`` as well as ``repr()``,
      and a credential embedded in a repository URL is stripped from
      logs and errors. **Behavior change:** ``get_url()`` /
      ``get_public_url()`` no longer return such a credential, and an
      invalid GRR definition now raises a plain ``ValueError`` with a
      redacted message instead of a pydantic ``ValidationError`` — code
      catching ``ValidationError`` around
      ``build_genomic_resource_repository`` must catch ``ValueError``.
    * **Behavior change:** ``GPFWA_EMAIL_USE_TLS`` is now parsed as a
      string, so setting it to ``False`` no longer *enables* STARTTLS;
      only a literal ``true`` (case-insensitive) enables TLS.
    * Fixed the 2026.7.0 ``.gitignore``-aware resource scan silently
      dropping the data files of a DVC-managed GRR (#209/#211): a
      gitignored file is now re-included when a sibling ``<name>.dvc``
      declares it an output.
    * ``grr_browse`` can now filter its listing through the GRR
      full-text search index (``-s``/``--search`` runs an SQLite FTS5
      match), restrict the listing to one resource type
      (``-t``/``--type``), and print each resource's summary
      (``--summary``).
    * Reworked the notifications WebSocket reconnection (#204): it now
      retries with exponential backoff (200 ms, doubling to a 10 s cap,
      then a 30 s cooldown) and never gives up, where it previously died
      after five attempts. It is also no longer closed by navigating to
      a page with no notification consumer, such as About (#215).
    * A pipeline whose build failed is now rebuilt on the next save:
      ``LRUPipelineCache.put_pipeline`` previously returned the existing
      (possibly failed) build future on a config-hash match, so an
      unchanged config kept surfacing a stale ``failed`` status.
    * Added ``gain.logging``, a drop-in proxy for the standard library's
      ``logging`` module that guarantees the ``TRACE`` and ``USER_INFO``
      levels are installed before any logger is created; gain's own
      modules were migrated onto it. **Behavior change:** ``-vvv`` now
      selects ``TRACE`` rather than ``DEBUG``, and the effect checkers'
      diagnostics moved to ``TRACE`` (so ``-vv`` no longer prints them).
    * The single-allele annotation response now reports
      ``preserves_domain: true`` rather than ``null`` for an attribute
      that is not aggregated, so the flag is always an explicit boolean.
    * Web UI: fixed three annotation-pipeline editor bugs — a late
      ``pipeline_status`` response overwriting the status bar with stale
      counts, a browser refresh resurrecting the stale temporary
      pipeline over the default one, and an ``NG0956`` duplicate-key
      warning on the annotatables table (rows are now tracked by their
      stable id).
    * ``gain.genomic_resources.testing`` became a package and gained a
      ``builders`` module: a fluent DSL for authoring test GRRs
      (``a_grr()``, ``a_position_score()``, ``a_np_score()``,
      ``an_allele_score()``, ``a_gene_score()``,
      ``a_reference_genome()``). Every previously importable name still
      is.
    * Documented the ``user`` and ``password`` basic-authentication keys
      of an ``http`` repository on the GRR configuration page.
    * CI: ``tests/integration`` moved out of the ``core`` build into a
      dedicated ``gain-core-integration`` downstream job that resolves
      real resources against ``grr-seqpipe`` and runs on every branch
      without failing the parent build (#222).

* 2026.7.0
    * Hardened HTTP basic-auth credential handling for GRR definitions:
      the ``user`` / ``password`` of an authed ``http`` repository are
      no longer logged when the repository is built, and are masked in
      the definition model's ``repr()`` / ``str()``. Configuring them on
      a plain ``http://`` URL to a non-local host now emits a loud
      warning (credentials would travel unencrypted); ``https://`` and
      ``localhost`` stay quiet.
    * **Behavior change:** repository definitions are now strictly
      validated — an unknown key in any repository definition is
      rejected rather than silently ignored. This guards against auth
      typos (``pasword``, ``username``) but can reject a
      previously-accepted definition carrying a stray key; remove any
      such keys to upgrade.
    * Untyped genomic resources now resolve to a dedicated ``basic``
      resource type with its own implementation (#185): a resource with
      no ``type`` renders a minimal info page and exposes every data
      file, so repository caching again covers the whole resource
      (gain#78). ``GenomicResource.get_type`` returns lower-case
      ``basic`` (was ``Basic``).
    * The GRR resource file scan now honors ``.gitignore`` files,
      accumulated across nested directories, so ignored files are
      excluded from the manifest and from caching. ``pathspec`` is now a
      runtime dependency of ``gain-core`` (#184).
    * Score aggregators now declare whether they preserve the source
      value domain via ``Aggregator.preserves_domain(value_type=…)``
      (``True`` for ``min``/``max``/``mean``/``median``/``mode``). The
      editor's aggregator-list endpoint and the single-allele response
      carry this flag per attribute, and the Web UI now hides the score
      histogram for an attribute whose aggregator does not preserve the
      domain.
    * Fixed the annotation-pipeline editor still getting stuck on
      "loading" after a WebSocket reconnect in a case the 2026.6.9 fix
      (#160) missed: a 200 from the blocking
      ``GET /api/editor/pipeline_status`` is now treated as the
      authoritative ``loaded`` signal.
    * Fixed a flaky ``FileExistsError`` when creating the task-graph log
      directory under concurrency (#186): ``ensure_log_dir`` now uses
      ``makedirs(exist_ok=True)``.
    * Added two custom logging levels, ``TRACE`` and ``USER_INFO`` (25,
      between INFO and WARNING), and the matching ``logger.trace()`` /
      ``logger.user_info()`` helpers, installed when the ``gain``
      package is imported.
    * Fixed generated pipeline documentation leaving a resource or
      histogram link unset when the resource is referenced from outside
      the managed GRR: it now falls back to the resource's public URL.
    * Enlarged the axis and note-label font on generated score-histogram
      images to a shared ``HISTOGRAM_LABELS_FONT_SIZE`` constant,
      applied to the categorical histogram's bar labels too.
    * Web UI: fixed several annotation-pipeline editor and new-annotator
      dialog layout issues (aggregators table hugging its rows while
      keeping the footer visible, scrollable tables' rounded corners,
      assorted styling).
    * Corrected the "creating an annotator plugin" documentation
      example: the sample rule read ``clinical_significance`` with
      ``.lower()`` where ``.strip()`` was intended, and its inline
      literals are now RST code.

* 2026.6.10
    * Reading a VCF score resource's header no longer logs a spurious
      htslib ``[E::idx_find_and_load] Could not retrieve index file``
      line to stderr. ``VCFGenomicPositionTable`` opens the companion
      ``*.header.vcf.gz`` (which ships no ``.tbi``) only to read its
      INFO definitions; the open is now wrapped in
      ``pysam.set_verbosity(0)`` so the index auto-probe stays quiet.
    * Added a "creating an annotator plugin" walkthrough to the
      Python-interface documentation page, with a worked example
      adapter.

* 2026.6.9
    * Fixed default-attribute selection for score annotators that
      declare a ``default_annotation``: exactly the named attributes are
      now marked as defaults (previously, when any ``default_annotation``
      was configured, none were). Gene scores with no histogram
      configuration now fall back to a default histogram for their value
      type instead of raising ``Missing histogram config``.
    * Opening a VCF score resource's header file no longer triggers a
      needless index lookup: ``open_vcf_file`` now opens the
      ``.tbi``-less ``*.header.vcf.gz`` index-less instead of always
      handing pysam a ``.tbi`` URL (which over an http GRR meant
      fetching a non-existent index).
    * Genomic resource repository definitions are now validated against
      typed pydantic schemas that reject unknown keys and check required
      fields (e.g. an ``http`` repo's ``user`` and ``password`` must be
      supplied together or not at all), so a malformed definition fails
      early with a clear error.
    * A ``public_url`` may now be set on any repository definition type,
      not only ``http``/``url`` — ``file``, ``dir`` and ``s3`` repos
      accept it too.
    * HTTP genomic resource repositories now support basic
      authentication: an ``http`` definition with ``user`` and
      ``password`` passes them to the underlying ``HTTPFileSystem`` as
      ``aiohttp`` basic-auth credentials.
    * The ``annotate_doc`` CLI now builds resource and score-histogram
      links from each resource's public URL rather than its local
      ``file://`` URL, matching the live web-help fix in 2026.6.7.
    * Renamed the stored ``Job.annotation_type`` value from ``"columns"``
      to ``"tabular"`` (#29, deferred from #25); migration 0043 rewrites
      existing rows in the ``Job`` and ``AnonymousJob`` tables and is
      reversible.
    * **Behavior change:** saving a user pipeline whose config
      references a missing or broken GRR resource now succeeds
      (HTTP 200) and reports the failure asynchronously, instead of a
      synchronous HTTP 400 (#150/#152). ``POST /api/pipelines/user`` now
      does only cheap structural YAML validation; deep,
      resource-resolving validation is deferred to the background
      pipeline loader.
    * A deferred pipeline build failure now carries a reason (#155/#156):
      a distinct ``failed`` pipeline-load status carries the formatted
      error, surfaced live (over the ``pipeline_status`` WebSocket) and
      durably (the pipeline listing), so a refresh or reconnect does not
      collapse it to ``unloaded``. The Web UI shows the reason in the
      editor and a red failed marker in the pipeline dropdown.
    * Fixed the annotation-pipeline editor getting stuck on "loading"
      after a WebSocket reconnect (#160): on connect the consumer now
      replays the current load status of the session's editor pipeline
      (and, for an authenticated user, their saved pipelines) from the
      shared cache, so a late-connecting client still converges.
    * The web API's read endpoints (single-allele annotation, the
      editor's status/attributes/YAML/aggregator handlers and the
      ``annotate_doc`` download) were converted to async, awaiting the
      GRR pipeline build and the annotate call off the event loop
      (#162–#167), keeping the ASGI event loop responsive under
      concurrent load. Request behavior, status codes and payloads are
      unchanged.
    * Web UI: the new-annotator workflow now detects duplicate output
      attribute names, showing an error and disabling Finish until the
      conflict is resolved.
    * Web UI: images on the GRR resource and gene-set-collection pages
      are now constrained to a maximum width so large figures no longer
      overflow their modal.
    * Web UI: upgraded the Angular framework to v21.
    * Added a fourth worked example to the Python-interface documentation
      page.
    * CI: the anonymous annotate rate-limit is now keyed by session
      rather than IP under the e2e settings only (#179), so Playwright
      tests no longer cross-exhaust one shared per-IP bucket and flake
      with spurious HTTP 429s; production keying is unchanged.

* 2026.6.8
    * ``annotate_vcf`` now supports CSI-indexed input: a VCF carrying a
      ``.csi`` index (rather than ``.tbi``) is split by genomic region
      using that index and the output is itself CSI-indexed. CSI lifts
      tabix's 512 Mbp coordinate limit, so VCFs on long contigs can now
      be region-parallelized.
    * Gene-set collections in ``map`` format may now be supplied
      gzip-compressed: a ``filename`` ending in ``.gz`` is read through
      ``gzip`` and the companion ``…names.txt`` is resolved against the
      de-gzipped stem.
    * Follow-up to the 2026.6.7 quote-aware CSV/TSV change (#144): the
      tabix-indexing step parsed the header with a hardcoded tab even
      for comma-separated output; the configured output separator is now
      threaded through to header parsing.
    * Fixed duplicate annotation-job names under concurrency (#138):
      ``User.generate_job_name`` was a non-atomic read-modify-write of
      ``job_counter``, so two concurrent creations could return the same
      name and collide on one ``result_path``. Allocation is now a
      single atomic ``UPDATE … RETURNING``.
    * Fixed duplicate quota rows under concurrency (#139): ``UserQuota``
      and ``AnonymousUserQuota`` used non-unique keys, so concurrent
      first-time requests each inserted a row and every ``get_quota()``
      then raised ``MultipleObjectsReturned`` (HTTP 500).
      ``UserQuota.user`` is now a ``OneToOneField`` and
      ``AnonymousUserQuota.ip`` is unique; migration 0042 de-duplicates
      pre-existing rows before adding the constraints.
    * Fixed the annotation web API returning a spurious HTTP 400
      "Pipeline … not found" under load (#140): the ``LRUPipelineCache``
      could evict, purely by recency, an entry another thread had just
      put. In-use pipelines are now pinned by a refcount under the cache
      lock and skipped by eviction and the timeout reaper.
    * Fixed running annotation jobs losing their result file when a
      WebSocket disconnected (#147): both ``delete_jobs``
      implementations now skip jobs whose status is ``WAITING`` or
      ``IN_PROGRESS``, so in-flight and queued jobs keep their files.
    * Deleting a saved pipeline now also evicts it from the annotation
      web API's pipeline cache.
    * Web UI: per-attribute (gene) aggregator selection now renders
      correctly in the new-annotator workflow, and the result value-type
      options are now finer-grained and driven by the chosen aggregator.
    * Web UI: pipeline-info loading is now driven from reactive signals
      as a single source of truth, ``pipeline_status`` requests are
      de-duplicated (``shareReplay``), and the cache is invalidated on
      YAML changes.
    * The GRR browser index table can now be sorted by any column, not
      only the ID column.
    * Revised the getting-started CLI tutorial and GRR documentation
      (local-GRR walkthrough, positions/regions handling, reannotation
      example, dropped ``--grr-directory`` and the obsolete GRR
      configuration-files page, ``t2t``/``hs1`` correction).
    * CI: the gain-web e2e pipeline now tears down its Docker Compose
      project in ``post.cleanup`` as a backstop, so a failure or abort
      before the Run-e2e stage no longer orphans the per-build ``db``
      container, volume and network.
    * **Behavior change:** ``annotate_tabular`` (and its deprecated
      ``annotate_columns`` alias) now reads and writes delimited files
      with quote-aware CSV parsing (Python's ``csv`` module) instead of
      a naive split/join. Quoted fields containing the separator are now
      respected on input, an escaped quote ``""`` decodes to a literal
      ``"``, and on output any value containing the separator or a quote
      is wrapped in quotes. A bare ``"`` in existing data is now
      significant — pre-quote data containing literal quote characters.
      Embedded newlines inside quoted fields remain unsupported.
      ``--input-separator`` / ``--output-separator`` must now be a single
      character.

* 2026.6.7
    * Fixed the VEP effect annotator segfaulting on a bgzipped reference
      genome: a bgzipped FASTA needs both a ``.fai`` faidx and a ``.gzi``
      bgzf-offset index, but only the FASTA and ``.fai`` were
      pre-fetched, so htslib tried to build the missing ``.gzi`` on the
      read-only ``/grr`` mount and died. The ``.gzi`` is now declared as
      part of a bgzipped reference genome and pre-fetched; reference-
      genome file resolution was unified into a shared helper, so an
      ``index_file`` override is now honored for VEP too.
    * Fixed in-page GRR search breaking when the repository is served
      under a sub-path (e.g. ``…/grr/``): ``.CONTENTS.sqlite3.gz`` is now
      fetched relative to the page URL instead of the origin root.
    * Fixed the histogram image on gene-score and genomic-score info
      pages being unreachable from a browser on a directory GRR: the live
      web help now builds the image link from the resource's public URL
      (the static-doc builders keep the local URL they need for relative
      links).
    * Fixed slow annotation-pipeline loading in the web API under
      concurrency: unloading a pipeline from the LRU cache held the cache
      lock while closing the (slow) pipeline; the close now happens after
      the lock is released.
    * Restructured the getting-started GRR documentation, added a
      resource version-control section, noted the ``samtools``
      prerequisite, and made further getting-started CLI edits.

* 2026.6.6
    * Fixed ``annotate_tabular`` crashing with ``UnicodeDecodeError`` on
      non-ASCII input: the tabix reader now reads the input as UTF-8 (it
      previously decoded as ASCII while writing UTF-8, so reannotating a
      run carrying a value like ``Roussy-Lévy_syndrome`` failed).
    * Default-verbosity runs no longer print ``INFO:distributed``
      messages: the ``distributed`` logger is re-silenced after the dask
      cluster is built (importing ``distributed`` had reset its level).
    * The dask cluster is now torn down gracefully in
      ``DaskExecutor.close()``, avoiding the worker-heartbeat-failure and
      "Connection … closed" log flood from the previous
      ``retire_workers`` race.
    * Task-graph executor teardown is now best-effort: a failure while
      releasing executor resources is logged and no longer crashes a run
      whose results are already written (all task-graph tools).
    * The fsspec "protocol with id … already exists" message was demoted
      from warning to debug.
    * ``grr_manage`` repo-repair now writes each resource's
      ``index.html`` and ``statistics/index.html`` only when the rendered
      content changed, so re-running repair no longer bumps mtimes and
      defeats mtime-based consumers (e.g. the ``gs://iossifovlab-grr``
      bucket-publish pipeline).
    * Web UI: the new-annotator workflow gained an aggregation
      configuration step for selecting per-attribute (gene) aggregators,
      including their parameters.
    * CI: docs-only ``gain`` master builds now also archive a
      ``gain-core`` conda package, so downstream jobs copying the last
      build's conda artifact no longer fail with zero matches.

* 2026.6.5
    * ``annotate_tabular`` and ``annotate_vcf`` now print a reannotation
      plan — an always-on stderr summary classifying each output
      attribute as ``COPIED``, ``ADDED``, ``COMPUTED`` or ``DELETED`` —
      and a new ``--dry-run``/``-n`` flag prints the plan and exits
      without annotating.
    * Reannotation reuse now works through the CLI: the framework-injected
      ``work_dir`` is excluded from ``AnnotatorInfo`` identity, so an
      unchanged annotator's result is copied instead of recomputed.
      ``annotate_tabular`` no longer emits a reused (``COPIED``) attribute
      twice.
    * Fixed ``--full-reannotation`` leaving stale values: every annotator
      of the new pipeline is now forced to recompute.
    * Fixed a reannotation crash (``TypeError: unhashable type``) when a
      pipeline contained an annotator with a dict- or list-valued
      parameter (e.g. ``chrom_mapping``'s inline ``mapping``):
      ``AnnotatorInfo`` and ``ParamsUsageMonitor`` hashing is now
      order-normalized and consistent with their equality.
    * Fixed gzipped gene-score files: a ``.tsv.gz`` score file was read
      with a comma separator; the ``.gz`` suffix is now stripped before
      the ``.tsv`` check.
    * Building gene-score histograms no longer rebuilds a ``GeneScoresDb``
      per score, removing an O(n²) rebuild and a spurious "unable to load
      histogram file" error on a first-time stats build.
    * Added a GRR definition-files page to the documentation and made
      further getting-started CLI edits.

* 2026.6.4
    * ``grr_manage`` no longer rebuilds the full-text search SQLite index
      when the repository contents are unchanged: the existing
      ``.CONTENTS.sqlite3.gz``'s stored ``contents_md5`` is compared
      against the current contents hash and the rebuild is skipped on a
      match.
    * Revised the getting-started CLI tutorial (local-GRR walk-through,
      VCF parallelization and region-annotation sections, refreshed
      example variant files); removed the obsolete standalone GRR
      YAML/repository reference pages.

* 2026.6.3
    * The annotation web API now supports a configurable default pipeline
      via ``DEFAULT_PIPELINE`` (shipping as
      ``pipeline/hg38_clinical_annotation``); the pipeline-list endpoint
      moves it to the front so the UI pre-selects it, and fails fast with
      a 500 if the configured id is unavailable. ``None`` disables the
      behavior.
    * ``annotate_tabular`` and ``annotate_vcf`` now accept a GRR resource
      id (not only a filesystem path) as ``--reannotate``, mirroring
      ``--pipeline``.
    * Removed the per-file variant limit: the annotation web API no longer
      caps the number of variants in an uploaded file, and the setting,
      its enforcement and the frontend display were removed.
    * Web UI: saved pipelines now expose a download link to their
      generated HTML documentation.
    * Fixed the single-variant annotation API failing when the pipeline
      contained an annotator with no GRR resources (e.g. a
      ``chrom_mapping`` annotator with a non-internal output attribute):
      the histogram lookup is now skipped.
    * Fixed annotation-config serialization emitting ``internal: null``
      for attributes whose ``internal`` flag is unset; the field is now
      omitted when unset, so configs round-trip cleanly.
    * Web UI: job notifications now arrive in a guaranteed order; number
      histograms can show multiple red value markers; and available
      pipelines are refetched when the login state changes.
    * Revised the getting-started CLI tutorial (reannotation walk-through,
      larger SSC example, custom-pipeline examples) and documented the
      ``gene_column`` resource in the GRR guide.

* 2026.6.2
    * When the output file is not given, ``annotate_tabular`` and
      ``annotate_vcf`` now derive the default name by inserting an
      ``.annotated`` marker before the suffix (e.g.
      ``variants.annotated.vcf.gz``) instead of ``_annotated``. An
      explicit ``-o``/``--output`` name is used verbatim.
    * ``annotate_tabular`` and ``annotate_vcf`` now delete a working
      directory they created once annotation finishes successfully (a
      pre-existing ``--work-dir`` is preserved, a failed run is kept for
      resume, and the directory is kept when the output is written inside
      it). ``--keep-work-dir`` opts out. ``annotate_vcf`` now also closes
      its pipeline at the end of a run.
    * Added an ``annotate_doc`` pipeline-documentation endpoint to the
      annotation web API: it renders a standalone HTML description of a
      saved pipeline and serves it as a downloadable ``<pipeline_id>.html``
      attachment.
    * Fixed several edge cases in the ``allele_score`` annotator's filter
      grammar: negative numeric literals are now accepted, and identifiers
      starting with a digit are tokenized correctly.
    * Web UI: the application version is now shown on the About page;
      list-valued attributes now render in the single-variant annotation
      view; and filled-in note labels are rendered more prominently.

* 2026.6.1
    * Reference genomes may now be supplied as bgzipped FASTA in
      ``pysam.FastaFile`` format, not only plain uncompressed ``.fa``. A
      genome whose ``filename`` ends in ``.gz``/``.bgz`` is opened through
      ``pysam.FastaFile`` and must ship a ``.fai`` faidx and a ``.gzi``
      bgzip index; an optional ``index_file`` can point at a non-default
      ``.fai``. Works local, cached, or over HTTP/S3 (the indexes are
      cached on first open; genome-wide statistics fetch in 1 MiB
      windows). Documented in the Genomes section of the GRR guide.
    * ``annotate_columns`` was renamed to ``annotate_tabular``;
      ``annotate_columns`` remains a deprecated alias.
    * Reworked the annotator class hierarchy so all built-in annotators
      inherit a common ``AnnotatorBase`` and share one attribute-handling
      and aggregation implementation. This fixes several bugs: the
      chrom-mapping, liftover and normalize-allele annotators now write to
      their configured output names, the gene-set annotator emits under
      the correct name, gene-score annotators support aggregation again,
      and ``annotatable``-typed attributes no longer attempt aggregation.
    * The GRR static index now embeds a content hash of the full-text
      search SQLite database, so a browser re-fetches the search index
      after the contents change instead of serving a stale cached copy.
    * Resource index links in the generated GRR pages are now relative, so
      a mirrored or relocated repository's links resolve correctly.
    * Pinned ``pysam`` to 0.24.0 (unpinning htslib/samtools/bcftools in
      the conda environment) and declared ``tqdm`` a runtime dependency of
      the ``gain-core`` conda recipe.

* 2026.6.0
    * ``grr_cache_repo`` now shows live progress while caching: a
      byte-based ``tqdm`` bar on a terminal (human-readable units,
      throughput, ETA, ``files=done/total`` and ``failed=N``), or
      throttled milestone ``INFO`` lines off a terminal. Caching first
      classifies the full work-list, then downloads only missing or stale
      files, so a fully-cached re-run prints no bar; a failed download is
      reported in the end-of-run summary. ``--no-progress`` turns it off.
      ``tqdm`` is now a core runtime dependency.
    * Raised the GRR caching protocol's read-buffer (``CHUNK_SIZE``) from
      32 KiB to 1 MiB, cutting per-chunk Python read/write/md5 overhead
      roughly 32× on multi-gigabyte resources with no change to network
      behavior.
    * Revised the getting-started CLI and web tutorials, refreshed their
      figures, and updated the example variant files.
    * **Backward-incompatible:** ``grr_cache_repo`` now takes the
      annotation pipeline as a **positional argument**
      (``grr_cache_repo <pipeline>``) instead of the ``--pipeline``/``-p``
      flag; scripts passing the flag must drop it. With ``-i`` a GPF
      instance, an explicit positional pipeline still wins; with no
      pipeline from any source the command logs ``no pipeline supplied;
      nothing to cache`` and exits cleanly.
    * ``annotate_tabular`` and ``annotate_vcf`` now treat ``.bgz`` as a
      first-class compression extension on par with ``.gz`` for both input
      and output: a ``.bgz`` input is read (and split when tabix-indexed)
      like a ``.gz`` one, an explicit ``.bgz`` output suffix is preserved,
      and this also fixes ``build_output_path`` mangling output names whose
      stem ended in ``g``/``z`` (e.g. ``log.gz``). Consequently the default
      ``--work-dir`` derived from a ``.bgz`` output changed (``out.vcf.bgz``
      now yields ``out_work``); a run interrupted on an older version and
      resumed after upgrading should point ``--work-dir`` at the previous
      ``<output-stem>.vcf_work`` path.
    * Fixed silent duplication of records when an ``annotate_tabular``
      input carried a ``.csi`` index rather than a ``.tbi`` one: the reader
      looked only for ``.tbi`` and otherwise opened the file whole, so
      every genomic-region part re-read and re-emitted the entire file.

* 2026.5.10
    * ``grr_cache_repo`` no longer aborts a long HTTP download at htslib's
      300 s read cap (which killed caching of large resources such as the
      genome-wide gnomAD file): the HTTP filesystem now applies no overall
      timeout, only per-read and per-connect limits. A failed file is
      retried with exponential backoff and, if it still fails, reported in
      a summary; caching is resumable.
    * The anonymous-quota refresh commands (``refreshdaily`` /
      ``refreshmonthly``) now reset each quota and write their refresh-log
      entry inside a single transaction, so an interruption rolls back
      cleanly. They also now reset ``SessionQuota``, previously left as a
      permanent floor on the effective anonymous quota.
    * Added a version API endpoint.

* 2026.5.9
    * ``annotate_vcf`` and ``annotate_tabular`` now run a pre-flight
      locality check: when the pipeline uses non-local genomic resources
      (http/https/s3) and the input is large, they warn (1001–5000 rows)
      or abort (more than 5000 rows). Local resources never trip the
      guard, and ``--allow-remote-resources`` disables it entirely.
    * The GRR index table now supports cascading column resize.
    * Fixed a GRR summary tooltip being truncated when a biosample
      description contained a double quote.

* 2026.5.8
    * Added a ``prepare_tabular`` CLI that sorts a (optionally
      gzip-compressed) tabular file by genomic coordinates and writes a
      bgzip-compressed, tabix-indexed output, so ``annotate_tabular`` can
      parallelize annotation across genomic regions. It reuses the
      ``--col-*`` options and orders chromosomes by a reference genome when
      one is supplied (lexicographically otherwise).
    * ``annotate_tabular`` (and the deprecated ``annotate_columns`` alias)
      now defaults ``--input-separator`` to a comma for a ``.csv`` input
      (optionally ``.gz``/``.bgz`` compressed); all other inputs still
      default to a tab. An explicit ``--input-separator``/``--in-sep``
      always wins.
    * ``annotate_tabular`` and ``annotate_vcf`` now run sequentially
      (forcing ``-j 1``) when the input cannot be split into genomic
      regions (no tabix index, or ``--region-size`` ≤ 0), avoiding needless
      parallel-executor startup overhead.
    * ``annotate_tabular`` and ``annotate_vcf`` now run inside their
      ``work_dir``, so the ``.tbi`` index files htslib downloads for
      http(s)-GRR resources land there instead of littering the launch
      directory. Path-bearing CLI arguments are absolutized first.
    * Fixed flicker and column-resize lag in the GRR browser index table.
    * Expanded the getting-started CLI tutorial with parallelization and
      resource-caching sections.

* 2026.5.7
    * Moved the ``grr_cache_repo`` CLI from ``gpf`` into ``gain``.
    * Fixed the ``--version`` label on the ``grr_manage``, ``grr_browse``,
      ``annotate_columns`` and ``annotate_vcf`` CLIs.
    * The GRR index table now supports sorting by the ID column and
      resizing columns by dragging.
    * Silenced the spurious htslib ``[W::hts_idx_load3] The index file is
      older than the data file`` warning when reading parallel-downloaded
      GRR resources: htslib verbosity is now level 1 (errors only) for any
      process importing ``gain.genomic_resources.fsspec_protocol``.
    * Revised the getting-started CLI tutorial and refreshed the overview
      diagram.

* 2026.5.6
    * Renamed the ``annotate_columns`` CLI to ``annotate_tabular``; the old
      name is kept as a deprecated alias (stderr banner,
      ``DeprecationWarning`` on ``import gain.annotation.annotate_columns``)
      and will be removed in a future release.
    * The web UI now runtime-injects the Google Analytics snippet from the
      ``GA_MEASUREMENT_ID`` container environment variable, so the same
      image runs with or without GA depending on the host's deploy-time
      config.
    * Improved the getting-started CLI documentation with installation
      prerequisites.
    * The notifications WebSocket now retries on transport-level errors
      (e.g. a 502 during handshake) after a 2 s delay, preventing the
      subscription from dying permanently.
    * Fixed empty-array table header rendering and scrollable grid
      alignment in the single-annotation report.

* 2026.5.5
    * Moved the ``to_gpf_gene_models_format`` CLI from ``gpf`` into
      ``gain``.

* 2026.5.4
    * Made ``.CONTENTS.json.gz`` and ``.CONTENTS.sqlite3.gz``
      byte-reproducible across platforms.

* 2026.5.3
    * Refactored the allele score annotator: its default mode now operates
      only on VCF alleles, and the legacy ``allele_aggregator`` attribute
      was deprecated in favor of ``aggregator``.
    * Fixed VCF processing where incorrect end positions caused spanning
      records to be skipped, and corrected how allele scores access
      positions.
    * Standardized canonical annotator names throughout the documentation
      and fixed attribute-selection bugs in the new-annotator UI.
    * Fixed a race condition when filtering annotators.
    * URL-encode lists, tuples, and dicts when stringifying annotation
      attributes.
    * Improved GRR browser page styles and table layout, and refactored the
      templates for visual cohesion.
    * Fixed broken annotation infrastructure links.
    * Updated the FTS search database when creating the contents file and
      fixed a statistics-manifest bug.
    * The single-annotation report now handles array result values.

* 2026.5.2
    * Added admin panel views for managing anonymous users and their
      quotas; monthly quotas are now always displayed.
    * Anonymous-user quotas are now tracked by session ID.
    * Restyled the GRR repository about and index pages.
    * Introduced a new template infrastructure for resource
      implementations.
    * Fixed BigWig score-definition validation.

* 2026.5.1
    * Imported the GAIn user documentation into the repository and added
      Build/Deploy docs CI stages.
    * Updated the quotas page UI and removed quotas from the about page.
