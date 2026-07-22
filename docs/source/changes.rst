Release Notes
=============

* 2026.7.2
    * **Removed:** the in-resource aggregation engine (#267). Aggregation
      belongs to the annotators — the annotator fetches raw values from the
      resource and applies the configured aggregator to them — but an earlier
      design also aggregated *inside* the score resources, and that second
      engine outlived its callers. It had none left, in GAIn or in GPF. The
      following public names are gone from
      ``gain.genomic_resources.genomic_scores``:

      * ``PositionScore.fetch_scores_agg``
      * ``PositionScore.get_region_scores``
      * ``AlleleScore.fetch_scores_agg`` (already deprecated)
      * ``AlleleScore.build_scores_agg`` (already deprecated)
      * the query and aggregate-holder types that only that engine used:
        ``PositionScoreQuery``, ``AlleleScoreQuery`` (already deprecated),
        ``PositionScoreAggr``, ``AlleleScoreAggr``, and the ``ScoreQuery``
        union alias

      Configure aggregation on the annotation pipeline attribute, or as the
      resource's ``position_aggregator`` / ``allele_aggregator`` default —
      both are unchanged, and the aggregators themselves are untouched. A
      caller that fetched-and-aggregated a region by hand should use
      ``PositionScore.fetch_region_weighted_values``, which pairs each record
      with the number of queried bases it covers, and feed those pairs to an
      ``Aggregator`` it owns.
    * A resource may now configure ``position_aggregator: count`` /
      ``allele_aggregator: count`` (#261). ``count`` has always been a
      registered aggregator — buildable, accepted in a pipeline
      configuration and documented — but the resource-config schema was a
      second, hand-written list of names that had drifted, and it rejected
      ``count`` outright. The schema is now derived from the aggregator
      registry, so registering an aggregator is the only edit needed for it
      to be configurable everywhere. ``join()`` with an empty separator is
      accepted too, matching the definition parser. A resource-level
      aggregator remains **string-only**: the
      ``{aggregator_type: ..., parameters: [...]}`` dict form is a
      pipeline-configuration spelling.
    * **Behavior change:** a region is now aggregated per **record**, not
      per base pair (#260). A position-score record that covers N base
      pairs of the annotated region used to be turned into N identical
      copies of its value and aggregated one copy at a time; it now
      reaches the aggregator once, carrying N as its weight, which the
      aggregator applies in closed form. Aggregating a region therefore
      costs one step per record instead of one per base — a 500 kb region
      backed by 2,000 records drops from tens of milliseconds to single
      digits, and no longer allocates one list element per base.
      ``Aggregator.add(value, count)`` has always taken a ``count``; it
      previously added it to a bookkeeping counter and otherwise ignored
      it, so a caller that passed one got a correct ``total_count`` and a
      silently wrong mean. It now weights the value.

      **Float position scores may move in their last bits on large
      regions**, toward the more accurate value. ``mean`` is the only
      aggregator affected, because it is the only one that accumulates
      floats: summing a value N times is not the same operation as
      multiplying it by N, and the weighted form rounds once per record
      rather than once per base pair. The error of the old form grew with
      the region — over a 500 kb region a true ``0.51`` came out as
      ``0.5099999999965371`` (~6.8e-12 relative); it is now ``0.51``.
      ``min``, ``max``, ``count``, ``median``, ``mode``, ``value_count``,
      ``bool``, ``list``, ``join`` and ``concatenate`` are bit-identical
      to before. Annotation **output files are unchanged**, since values
      are written to three significant figures; a consumer that reads an
      annotation at full float precision (writing parquet, say) will see
      the difference.
    * **Behavior change:** a ``.dvc`` sidecar is no longer accepted as the
      md5 sum of a resource file whose bytes are on disk (#251, #255). The
      rule ``grr_manage`` now follows is:

      * whenever an md5 sum has to be **derived** for a file that is
        **materialised** (its bytes are on disk), it is computed from those
        bytes. The file's ``.dvc`` sidecar is not consulted;
      * a file that is **not materialised** (only a ``.dvc`` sidecar is
        checked out, as in the pointer-only clone the ``grr`` pipeline
        builds from) has no bytes to hash, so its sidecar remains the sole
        source of its md5 sum and size, and its manifest entry is never
        dropped.

      A sidecar cannot be confirmed without reading the bytes it claims to
      describe, so it is only trusted where there is no alternative.
      Previously, editing a DVC-managed file in place without ``dvc add`` /
      ``dvc commit`` and running ``resource-repair`` reported the resource
      as up to date and left the manifest certifying the *old* md5 — even
      when the edit preserved the file's size, which no later ``--force``
      run could recover from.

    * **Upgrading an existing GRR — please read.** The rule above governs how
      an md5 sum is *derived*. It does not retroactively re-verify md5 sums
      that are **already recorded** in a resource file state
      (``<resource>/.grr/<file>.state``). Such a state stays authoritative for
      as long as its size and timestamp match the file, whatever wrote it.

      A ``ResourceFileState`` does not record how its md5 sum was derived, and
      GAIn deliberately does not distinguish: a DVC-declared md5 sum and a
      content-derived one are treated as **equivalent**, since ``dvc add``
      computes the md5 sum from the very bytes it stores. States written by an
      earlier GAIn carry sidecar-derived md5 sums and are kept — they are not
      invalidated, and their files are not rehashed on upgrade.

      The accepted consequence: if a DVC-managed file was edited in place
      *before* the upgrade and an earlier GAIn already recorded that edit's
      size and timestamp alongside the sidecar's md5 sum, the manifest keeps
      certifying the stale md5 sum, and ``repo-repair`` will not re-detect it.
      To force content verification of a GRR — the recommended one-off step
      when upgrading a GRR that an earlier GAIn managed — run::

          grr_manage repo-repair --without-dvc

      which ignores recorded state and hashes every materialised file from its
      content. (Deleting the ``.grr`` directories has the same effect.)

      "Materialised" means the file exists, not "the repository scan yielded
      it" (#255). The two are not the same, and the difference used to be a
      hole in the rule: everything the scan skipped but that was nonetheless
      on disk was classified as a pointer and handed its sidecar's md5 sum
      unverified, in every mode. A file the scan does not yield but that
      exists on disk is now left out of the manifest rather than certified
      from a sidecar whose claim nobody checked. Two kinds of resource data
      fell through the hole:

      * a **DVC-managed ``*html`` file** is resource data, not one of the
        info pages GAIn generates for a resource, and the scan's blanket
        ``*html`` exclusion no longer applies to it. It is manifested with an
        md5 sum derived from its content. ``*html`` files that are not
        DVC-managed are still excluded, as before;
      * a **``dvc add <dir>`` output** — see the next entry: it is now
        refused, not described.

      The cost is that the **first** manifest build of a fully-materialised
      GRR hashes each DVC-managed file once, where it previously read the
      md5 sum out of the sidecar. Afterwards the recorded file state's
      size-and-timestamp fast path applies as before, so repeated
      ``repo-repair`` runs do not rehash unchanged files, and the
      pointer-only clone hashes nothing at all.

      Every ``.dvc`` sidecar in the GRRs is a per-file ``dvc add <file>``
      output, and the manifest of such a resource is byte-for-byte unchanged
      — whether its data is materialised or not.
    * **Behavior change:** ``grr_manage`` now **refuses** a resource that has
      a ``dvc add <dir>`` output — a ``.dvc`` sidecar declaring a directory
      (a ``.dir`` md5 sum and/or an ``nfiles`` count) — instead of describing
      it (#255). Every subcommand that builds or checks a manifest
      (``repo-manifest``, ``resource-manifest``, ``repo-stats``,
      ``resource-stats``, ``repo-repair``, ``resource-repair``,
      ``repo-info``, ``resource-info``) fails with a non-zero exit and an
      error naming the resource and the offending ``.dvc`` file. The refusal
      applies whether or not the directory's data is materialised.

      GAIn cannot verify a ``.dir`` md5 sum: it hashes a DVC *cache object*
      listing the directory's files, not any file GAIn can read from the
      resource. Accepting it into the manifest — which is what used to
      happen — was a false clean bill of health: a file inside such a
      directory could be tampered with and the resource was still reported up
      to date, ``--without-dvc`` included. Silently skipping the directory
      would be no better, since its data would then be neither manifested nor
      verified. Fix such a resource by DVC-managing its individual files
      (``dvc add <file>``). No GRR uses ``dvc add <dir>``: all 344 ``.dvc``
      sidecars in ``iossifovlab/grr`` are per-file outputs, so nothing in
      production is refused today.
    * **Behavior change:** the ``--with-dvc`` (default) /
      ``-D``, ``--without-dvc`` option group is restored on
      ``repo-manifest``, ``resource-manifest``, ``repo-stats``,
      ``resource-stats``, ``repo-repair``, ``resource-repair``,
      ``repo-info`` and ``resource-info`` (#251). It was removed in
      2026.7.1, which left no way to ask ``grr_manage`` to verify a
      resource file's bytes against its recorded md5 sum.
      ``--without-dvc`` is that audit mode: it ignores the recorded
      resource file state and computes from its content the md5 sum of every
      materialised resource file. It does **not** discard ``.dvc`` sidecars
      for files that are not materialised — those have no content to hash, so
      dropping them would delete their entries from the manifest. Each file
      is read exactly once per command.
    * A malformed, unreadable or incomplete ``.dvc`` file no longer aborts
      ``grr_manage`` with a traceback (#251). It is reported as a warning and
      ignored, exactly as the repository scan already ignored it — both now
      interpret a sidecar through the same parser, so they cannot classify
      one differently. A sidecar that declares no usable md5 sum and size is
      ignored rather than written into the ``.MANIFEST`` as ``md5: null``.

* 2026.7.1
    * **Behavior change:** a completed anonymous annotation job and its
      result file are no longer deleted when the user's last WebSocket
      disconnects (#216), so a captured download link no longer 404s
      after a socket drop. Stale anonymous jobs are reaped by age
      instead, by the new ``cleanup_anonymous_jobs`` management command
      (``ANONYMOUS_JOB_TTL_HOURS``, environment variable
      ``GPFWA_ANONYMOUS_JOB_TTL_HOURS``, default 24; overridden per run
      by ``--older-than-hours``), which never touches a ``WAITING`` or
      ``IN_PROGRESS`` job. **Nothing in gain schedules the command** — a
      deployment that does not run it periodically will accumulate
      anonymous jobs and result files without bound.
    * Closed the GRR credential-leak paths the 2026.7.0 redaction missed
      (#202): ``grr_browse`` no longer prints the raw definition, the
      definition models mask ``user`` / ``password`` in ``model_dump()``
      and ``model_dump_json()`` as well as ``repr()``, and a credential
      embedded in a repository URL (``scheme://user:pass@host``) is
      stripped from log lines, fetch failures and scheme-mismatch errors.
    * **Behavior change:** ``get_url()`` and ``get_public_url()`` no
      longer return a credential embedded in a repository URL (#202).
      The credential-bearing URL is kept privately and still drives the
      fetch, so authentication is unaffected.
    * **Behavior change:** an invalid GRR definition now raises a plain
      ``ValueError`` with a redacted message, instead of a pydantic
      ``ValidationError`` that embedded the plaintext password (#202).
      Code catching ``ValidationError`` around
      ``build_genomic_resource_repository`` must catch ``ValueError``.
    * **Behavior change:** ``GPFWA_EMAIL_USE_TLS`` is now parsed as a
      string rather than tested for truthiness, so setting it to
      ``False`` no longer *enables* STARTTLS. Only a literal ``true``
      (case-insensitive) enables TLS; a deployment that relied on the old
      truthiness to keep TLS on must set the variable to ``true``.
    * Fixed the 2026.7.0 ``.gitignore``-aware resource scan silently
      dropping the data files of a DVC-managed GRR (#209/#211):
      ``dvc add`` gitignores exactly those files, so they vanished from
      the resource manifest and from caching. A gitignored file is now
      re-included when a sibling ``<name>.dvc`` declares it an output.
    * ``grr_browse`` can now filter its listing through the GRR full-text
      search index: ``-s``/``--search`` runs an SQLite FTS5 match against
      the repository's ``.CONTENTS.sqlite3.gz`` metadata database,
      ``-t``/``--type`` restricts the listing to one resource type, and
      ``--summary`` prints each resource's summary beneath its line.
    * Reworked the notifications WebSocket reconnection (#204). It now
      retries with exponential backoff (200 ms, doubling to a 10 s cap,
      then a 30 s cooldown) and never gives up, where it previously died
      permanently after five attempts. A graceful server close, an
      abnormal drop delivered as a bare ``Event``, and a sign-in or
      sign-out no longer leave notifications dead or churn the socket.
      This supersedes the flat 2 s transport-error retry of 2026.5.6.
    * Fixed the notifications WebSocket being closed by navigating to a
      page with no notification consumer, such as About (#215). For an
      anonymous user the backend read that close as "left the site" and
      deleted the completed jobs (the backend half is #216, above). The
      socket is now held open across route changes.
    * A pipeline whose build failed is now rebuilt on the next save,
      instead of re-reporting the cached failure indefinitely:
      ``LRUPipelineCache.put_pipeline`` returned the existing — possibly
      failed — build future whenever the config hash matched, so an
      unchanged config kept surfacing the ``failed`` load status added in
      2026.6.9 even after a transient GRR problem had cleared.
    * Added ``gain.logging``, a drop-in proxy for the standard library's
      ``logging`` module. ``from gain import logging`` guarantees the
      ``TRACE`` and ``USER_INFO`` levels added in 2026.7.0 are installed
      before any logger is created, so ``logger.trace()`` /
      ``logger.user_info()`` are always available and type-check. gain's
      own modules were migrated onto it.
    * **Behavior change:** ``-vvv`` now selects the ``TRACE`` level
      rather than ``DEBUG``, and the effect checkers' diagnostics moved
      from ``DEBUG`` to ``TRACE``. ``-v`` (INFO) and ``-vv`` (DEBUG) are
      unchanged, but a run at ``-vv`` no longer prints the effect-checker
      trace — pass ``-vvv`` for it.
    * The single-allele annotation response now reports
      ``preserves_domain: true``, rather than ``null``, for an attribute
      that is not aggregated at all, so the flag added in 2026.7.0 is
      always an explicit boolean. The Web UI already treated ``null`` as
      domain-preserving, so the rendered histogram is unchanged.
    * Web UI: fixed three annotation-pipeline editor bugs — a late
      ``pipeline_status`` response no longer overwrites the status bar
      with stale annotator and attribute counts, a browser refresh no
      longer resurrects the stale temporary pipeline over the default
      one, and the annotatables table tracks its rows by their stable id,
      so a history refresh no longer trips Angular's ``NG0956``
      duplicate-key warning.
    * ``gain.genomic_resources.testing`` became a package and gained a
      ``builders`` module: a fluent DSL for authoring test GRRs —
      ``a_grr()``, ``a_position_score()``, ``a_np_score()``,
      ``an_allele_score()``, ``a_gene_score()`` and
      ``a_reference_genome()``. Every name previously importable from
      ``gain.genomic_resources.testing`` still is.
    * Documented the ``user`` and ``password`` basic-authentication keys
      of an ``http`` repository on the GRR configuration page.
    * CI: ``tests/integration`` moved out of the ``core`` build into a
      dedicated ``gain-core-integration`` downstream job, which resolves
      real resources against the ``grr-seqpipe`` GRR and runs on every
      branch without failing the parent build (#222).

* 2026.7.0
    * Hardened HTTP basic-auth credential handling for GRR definitions.
      The ``user`` / ``password`` of an authed ``http`` repository are no
      longer written to the logs when a repository is built, and are
      masked in the definition model's ``repr()`` / ``str()``.
      Configuring basic-auth credentials on a plain ``http://`` URL to a
      non-local host now emits a loud warning (the credentials would
      travel unencrypted); the request still works, and ``https://`` and
      ``localhost`` stay quiet.
    * **Behavior change:** repository definitions are now strictly
      validated — an unknown key in *any* repository definition (in
      ``~/.grr_definition.yaml`` or a group's ``children``) is rejected
      rather than silently ignored. This guards against auth typos (e.g.
      ``pasword`` or ``username``) but can reject a previously-accepted
      deployed definition that carried a stray key; remove any such keys
      to upgrade.
    * Untyped genomic resources now resolve to a dedicated ``basic``
      resource type with its own implementation (#185). A resource whose
      config carries no ``type`` previously had no implementation at all;
      it now renders a minimal info page and exposes every data file, so
      repository caching again covers the whole resource (gain#78).
      ``GenomicResource.get_type`` returns the lower-case ``basic`` (was
      ``Basic``) so the entry-point lookup resolves.
    * The GRR resource file scan now honors ``.gitignore`` files,
      accumulated across nested directories and each matched relative to
      its own ``.gitignore`` root, so ignored files are excluded from the
      resource manifest and from caching. ``pathspec`` is now a runtime
      dependency of ``gain-core`` (#184).
    * Score aggregators now declare whether they preserve the source
      value domain: a new ``Aggregator.preserves_domain(value_type=…)``
      returns ``True`` for ``min``/``max``/``mean``/``median``/``mode``
      and ``False`` otherwise. The editor's aggregator-list endpoint and
      the single-allele annotation response carry this flag per
      attribute.
    * Web UI: the single-annotation report now hides the score histogram
      for an attribute whose chosen aggregator does not preserve the
      score domain, since the resource's own histogram no longer
      describes the aggregated value.
    * Fixed the annotation-pipeline editor still getting stuck on
      "loading" after a WebSocket reconnect in a case the 2026.6.9 fix
      (#160) missed. A successful (200) response from the blocking
      ``GET /api/editor/pipeline_status`` means the GRR build finished,
      so it is now treated as the authoritative ``loaded`` signal and the
      editor converges regardless of WebSocket attach order or churn.
    * Fixed a flaky ``FileExistsError`` when creating the task-graph log
      directory under concurrency (#186): ``ensure_log_dir`` now uses the
      atomic, idempotent ``makedirs(exist_ok=True)`` instead of a
      check-then-create with an ineffective ``exists_ok`` kwarg.
    * Added two custom logging levels, ``TRACE`` and ``USER_INFO``, and
      the matching ``logger.trace()`` / ``logger.user_info()`` helper
      methods, installed when the ``gain`` package is imported.
      ``USER_INFO`` (25, between INFO and WARNING) is for messages aimed
      at end users; ``TRACE`` is for fine-grained diagnostic output.
    * Fixed generated pipeline documentation leaving a resource or
      histogram link unset when the resource is referenced from outside
      the managed GRR: it now falls back to the resource's public URL
      (``get_public_url`` / ``get_histogram_image_public_url``).
    * Enlarged the axis and note-label font on generated score-histogram
      images to a shared ``HISTOGRAM_LABELS_FONT_SIZE`` constant, and
      applied it to the categorical histogram's bar labels too.
    * Web UI: fixed several annotation-pipeline editor and new-annotator
      dialog layout issues — the aggregators table now hugs its rows
      while keeping the footer visible, scrollable tables render their
      rounded corners correctly, and assorted editor styling was cleaned
      up.
    * Corrected the "creating an annotator plugin" documentation example:
      the sample rule read ``clinical_significance`` with ``.lower()``
      where ``.strip()`` was intended, and its inline literals are now
      formatted as RST code.

* 2026.6.10
    * Reading a VCF score resource's header no longer logs a spurious
      htslib ``[E::idx_find_and_load] Could not retrieve index file``
      line to stderr. ``VCFGenomicPositionTable`` opens the resource's
      companion ``*.header.vcf.gz`` purely to read its INFO definitions,
      and such header-only files correctly ship no ``.tbi``; htslib
      auto-probed for the missing index on open and logged the
      (harmless) error, which was noisy during ``grr_manage``
      resource-repair and other resource operations. The header open is
      now wrapped in ``pysam.set_verbosity(0)`` (restored afterwards) so
      the probe stays quiet.
    * Added a "creating an annotator plugin" walkthrough to the
      Python-interface documentation page, with a worked example
      adapter.

* 2026.6.9
    * Fixed default-attribute selection for score annotators that
      declare a ``default_annotation``. A genomic-score annotator now
      marks exactly the attributes named in the resource's
      ``default_annotation`` as defaults; previously, when any
      ``default_annotation`` was configured, *no* attribute was flagged
      as a default. Gene scores with no histogram configuration now fall
      back to a default histogram for their value type instead of
      raising ``Missing histogram config``.
    * Opening a VCF score resource's header file no longer triggers a
      needless index lookup. ``VCFGenomicPositionTable`` opens the
      companion ``*.header.vcf.gz`` only to read its INFO definitions,
      and that header file ships no ``.tbi``; ``open_vcf_file`` now
      checks whether the index actually exists and opens the file
      index-less when it does not, instead of always handing pysam a
      ``.tbi`` URL (which over an http GRR meant fetching a
      non-existent index).
    * Genomic resource repository definitions are now validated against
      typed schemas. Each repository entry in a ``.grr_definition.yaml``
      is parsed through a per-type pydantic model that rejects unknown
      keys and checks required fields (e.g. an ``http`` repo's ``user``
      and ``password`` must be supplied together or not at all), so a
      malformed definition fails early with a clear error instead of
      being silently mis-read.
    * A ``public_url`` may now be set on any repository definition type,
      not only ``http``/``url`` — ``file``, ``dir`` and ``s3`` repos
      accept it too, so a directory- or S3-backed GRR can advertise the
      public address its generated pages and histogram images link to.
    * HTTP genomic resource repositories now support basic
      authentication: an ``http`` repository definition with ``user``
      and ``password`` set passes them to the underlying
      ``HTTPFileSystem`` as ``aiohttp`` basic-auth credentials, so a GRR
      served behind HTTP basic auth can be read.
    * The ``annotate_doc`` CLI now builds resource and score-histogram
      links from each resource's public URL (``get_public_url`` /
      ``get_histogram_image_public_url``) rather than its local
      ``file://`` URL, so the generated pipeline documentation is
      reachable from a browser — matching the live web-help fix in
      2026.6.7.
    * Renamed the stored ``Job.annotation_type`` value from ``"columns"``
      to ``"tabular"`` (#29, deferred from #25). The annotate-tabular
      endpoint now writes ``"tabular"`` and the job-detail endpoint
      branches on ``"tabular"``. Migration 0043 rewrites existing rows in
      both the ``Job`` and ``AnonymousJob`` tables (``columns`` →
      ``tabular``) and is reversible.
    * **Behavior change:** saving a user pipeline whose config
      references a missing or broken GRR resource now succeeds
      (HTTP 200) and reports the failure asynchronously, instead of
      returning a synchronous HTTP 400 (#150/#152).
      ``POST /api/pipelines/user`` built the pipeline against the GRR
      inline on daphne's single sync request thread, so a multi-second
      build serialized every other API request behind it (an
      intermittent gain-web-e2e timeout). The save endpoint now does
      only cheap structural YAML validation; deep, resource-resolving
      validation is deferred to the background pipeline loader, and an
      unbuildable pipeline surfaces as a load failure on the
      pipeline-status channel rather than a 500.
    * A deferred pipeline build failure now carries a reason (#155/#156).
      Because resource validation is deferred (above), a bad config
      previously surfaced only as a bare ``unloaded`` status —
      indistinguishable from a delete and carrying no explanation. A
      distinct ``failed`` pipeline-load status now carries the formatted
      error, surfaced both live (over the ``pipeline_status`` WebSocket)
      and durably (the pipeline listing reports ``failed`` + reason for a
      cached-but-failed build, so a refresh or reconnect does not
      collapse it back to ``unloaded``). The Web UI shows the reason in
      the editor and a red failed marker with a tooltip in the pipeline
      dropdown.
    * Fixed the annotation-pipeline editor getting stuck on "loading"
      after a WebSocket reconnect (#160). The editor's loaded state was
      driven only by a one-shot ``pipeline_status`` "loaded"
      notification; if that frame fired while the browser's WebSocket
      was between reconnects it was lost permanently over the no-replay
      in-memory channel layer. On connect the consumer now replays the
      current load status of the session's editor pipeline (and, for an
      authenticated user, their saved pipelines) from the shared
      pipeline cache, so a client that connects late still converges on
      the real status.
    * The web API's read endpoints — single-allele annotation, the
      pipeline editor's status/attributes/YAML/aggregator handlers and
      the ``annotate_doc`` download — were converted to async, awaiting
      the GRR pipeline build and the annotate call off the event loop
      (#162–#167). This keeps the ASGI event loop (and the WebSocket
      notifications it drives) responsive while a cold pipeline builds
      under concurrent load, removing the head-of-line blocking behind
      an intermittent gain-web-e2e flake. Request behavior, status codes
      and payloads are unchanged.
    * Web UI: the new-annotator workflow now detects duplicate output
      attribute names — selecting an attribute whose name collides with
      an existing one shows an "Attribute with this name already exists"
      error and disables Finish until the conflict is resolved by
      renaming or deleting the duplicate; original attribute names are
      preserved through the flow.
    * Web UI: images on the GRR resource and gene-set-collection pages
      are now constrained to a maximum width so large figures no longer
      overflow their modal.
    * Web UI: upgraded the Angular framework to v21.
    * Added a fourth worked example to the Python-interface
      documentation page.
    * CI: the anonymous annotate rate-limit is now keyed by session
      rather than IP under the e2e settings only (#179), so Playwright
      tests — each running in a fresh browser session — no longer
      cross-exhaust one shared per-IP bucket and flake with spurious
      HTTP 429s; production keying (IP for anonymous, user id for
      authenticated) is unchanged.

* 2026.6.8
    * ``annotate_vcf`` now supports CSI-indexed input. When the input
      VCF carries a ``.csi`` index (rather than a ``.tbi``), it is split
      by genomic region using that index and the annotated output is
      itself CSI-indexed; ``.tbi`` inputs are unchanged. CSI lifts
      tabix's 512 Mbp coordinate limit, so VCFs on long contigs can now
      be region-parallelized.
    * Gene-set collections in ``map`` format may now be supplied
      gzip-compressed: a ``filename`` ending in ``.gz`` is read through
      ``gzip``, and the companion ``…names.txt`` is resolved against the
      de-gzipped stem so a gzipped map still finds its names file.
    * Follow-up to the 2026.6.7 quote-aware CSV/TSV change (#144): the
      tabix-indexing step parsed the header with a hardcoded tab even
      for comma-separated output, so column resolution failed on a
      CSV-delimited run. The configured output separator is now threaded
      through to header parsing.
    * Fixed duplicate annotation-job names under concurrency (#138).
      ``User.generate_job_name`` was a non-atomic read-modify-write of
      ``job_counter``, so two concurrent job creations by the same user
      could read the same counter and return the same name — and since
      the name derives the job's data/config/result paths, the two jobs
      collided on one ``result_path`` (lost/overwritten results). The
      allocation is now a single ``UPDATE … RETURNING`` that increments
      and reads back atomically (with the catch-up floor folded in as a
      correlated subquery), closing the read-back race under READ
      COMMITTED on multi-process Postgres.
    * Fixed duplicate quota rows under concurrency (#139). ``UserQuota``
      and ``AnonymousUserQuota`` used non-unique key fields, so
      concurrent first-time requests each passed the ``get_or_create``
      check and inserted a row; the duplicates then made every
      ``get_quota()`` raise ``MultipleObjectsReturned`` and return
      HTTP 500 on ``/api/jobs/annotate_vcf``. ``UserQuota.user`` is now a
      ``OneToOneField`` and ``AnonymousUserQuota.ip`` is unique, the
      creates run in a transaction with an ``IntegrityError`` fallback to
      the existing row, and migration 0042 de-duplicates pre-existing
      rows (keeping the newest per user/ip) before adding the
      constraints.
    * Fixed the annotation web API returning a spurious HTTP 400
      "Pipeline … not found" under load (#140). The ``LRUPipelineCache``
      could evict, purely by recency, an entry another thread had just
      put and was still resolving. In-use pipelines are now pinned by a
      refcount under the cache lock; eviction skips pinned entries (and
      may briefly exceed capacity rather than evict an in-flight
      pipeline), the timeout reaper likewise defers pinned entries, and
      ``get_pipeline`` retries the load-and-cache sequence on a cache
      miss so a genuinely-missing pipeline still 4xx's.
    * Fixed running annotation jobs losing their result file when a
      WebSocket disconnected (#147). On an anonymous user's last
      disconnect, ``delete_jobs`` removed every job's files regardless of
      state, so a job still executing had its ``result-<name>.vcf``
      unlinked mid-run and ``on_success`` then failed with ``[Errno 2]``
      (an intermittent gain-web-e2e flake). Both ``delete_jobs``
      implementations now skip jobs whose status is ``WAITING`` or
      ``IN_PROGRESS``, so in-flight and queued jobs keep their files
      until they complete.
    * Deleting a saved pipeline now also evicts it from the annotation
      web API's pipeline cache — the delete endpoint was removing the
      pipeline from the user's store but leaving the stale entry cached.
    * Web UI: per-attribute (gene) aggregator selection now renders
      correctly in the new-annotator workflow, and the result value-type
      options are now finer-grained and driven by the chosen aggregator
      (an aggregator can influence the attribute's value type). The
      new-annotator grid-table styling was refreshed and its Material
      theme overrides extracted into a separate stylesheet.
    * Web UI: pipeline-info loading is now driven from reactive signals
      as a single source of truth, ``pipeline_status`` requests are
      de-duplicated (``shareReplay``), and the cache is invalidated on
      YAML changes — removing race conditions behind several flaky tests.
    * The GRR browser index table can now be sorted by any column, not
      only the ID column.
    * Revised the getting-started CLI tutorial and GRR documentation:
      reworked the local-GRR walkthrough, simplified the positions and
      regions handling in the examples, refreshed the reannotation
      pipeline example, dropped the ``--grr-directory`` option and the
      obsolete GRR configuration-files page, and corrected a
      ``t2t``/``hs1`` reference in the Python-interface page.
    * CI: the gain-web e2e pipeline now tears down its Docker Compose
      project in ``post.cleanup`` as a backstop, so a failure or abort
      before the Run-e2e stage no longer orphans the per-build ``db``
      container, volume and network (mirrors iossifovlab/gpf#953).
    * **Behavior change:** ``annotate_tabular`` (and its deprecated
      ``annotate_columns`` alias) now reads and writes delimited files
      with quote-aware CSV parsing (Python's ``csv`` module) instead of a
      naive split/join on the separator. Quoted fields containing the
      separator are now respected on input (e.g. a CSV cell
      ``"Smith, John"`` is one column, not two), an escaped quote ``""``
      decodes to a literal ``"``, and on output any value containing the
      separator or a quote is wrapped in quotes (with embedded quotes
      doubled). Plain rows with no special characters are emitted
      unchanged. This quoting is applied uniformly to all inputs, TSV and
      CSV alike, so a bare ``"`` in existing data is now significant and
      may change how a file is parsed -- pre-quote your data if it
      contains literal quote characters. Embedded newlines inside quoted
      fields remain unsupported (incompatible with the line-based tabix
      path). The ``--input-separator`` / ``--output-separator`` must now
      be a single character; a multi-character value is rejected early
      with a clear error.

* 2026.6.7
    * Fixed the VEP effect annotator segfaulting on a bgzipped
      reference genome. A bgzipped FASTA needs both a ``.fai`` faidx
      and a ``.gzi`` bgzf-offset index for htslib random access, but
      only the FASTA and ``.fai`` were pre-fetched into the GRR cache;
      htslib then tried to build the missing ``.gzi`` in place and died
      on the read-only ``/grr`` mount. The ``.gzi`` is now declared as
      part of a bgzipped reference genome and pre-fetched alongside the
      FASTA. Reference-genome file resolution (the FASTA, its ``.fai``
      honoring the optional ``index_file`` config key, and ``.gzi``
      when bgzipped) was unified into a single helper shared by the
      resource implementation and the VEP annotator, so an
      ``index_file`` override is now honored for VEP too.
    * Fixed in-page GRR search breaking when the repository is served
      under a sub-path (e.g. ``…/grr/``): the browse page fetched
      ``.CONTENTS.sqlite3.gz`` from the origin root instead of relative
      to the page. The fetch is now resolved against the page URL,
      which is sub-path-safe and unchanged for root-served
      repositories.
    * Fixed the histogram image on gene-score and genomic-score info
      pages being unreachable from a browser on a directory GRR: it was
      embedded via the resource's local ``file://`` URL. The live web
      help now builds the image link from the resource's public URL
      (honoring a directory GRR's configured ``public_url``); the
      static-doc builders (``annotate_doc``, pipeline documentation)
      keep the local URL they need for relative links.
    * Fixed slow annotation-pipeline loading in the web API under
      concurrency: unloading a pipeline from the LRU cache held the
      cache lock while closing the (potentially slow) pipeline, which
      blocked other threads' loads. The close now happens after the
      lock is released. Detailed lock acquire/release logging was added
      at ``DEBUG`` to diagnose such contention.
    * Restructured the getting-started GRR documentation, added a
      resource version-control section to the Genomic Resources and
      Repositories page, noted the ``samtools`` prerequisite, and made
      further edits to the getting-started CLI tutorial.

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
