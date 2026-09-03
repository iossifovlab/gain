Release Notes
=============

* unreleased
    * **Internal API.** ``AnnotatorBase`` no longer aggregates.
      ``WeightedValues``, ``Aggregator.aggregate_weighted`` and
      ``Attribute.aggregator_instance`` are **removed**;
      ``_apply_aggregators`` copies an ``AggregatedValues`` through and
      renames everything else from source to attribute name, reducing
      nothing. An attribute keeps the aggregator NAME, and anything
      needing the class -- ``Attribute.get_value_type(aggregated=True)``,
      the web API's single-allele attribute description -- resolves it
      through the new ``Aggregator.resolve_class``, which builds no
      accumulator. ``Aggregator.add(value, count)`` is unchanged and is
      still what the folding reads weigh with. An annotator that reduces
      values of its own rather than a score's record stream now folds
      them itself and answers an ``AggregatedValues``: the gene set,
      effect, simple effect and SpliceAI annotators through the new
      ``gain.annotation.annotator_base.fold_own_values``, and the gene
      score annotator through its own ``_apply_gene_aggregator``, whose
      values are a per-gene mapping rather than a list. An out-of-tree
      annotator that relied on the base folding its raw lists must do
      the same (:issue:`1133`).
    * An aggregator named on an ``allele_score`` annotator's virtual
      ``allele`` attribute reduces nothing in ``allele`` (exact-match)
      mode, where it previously folded the matched line's one-element
      list of keys -- a ``join`` there answered a bare string. ``region``
      mode has never folded it, so the two modes now agree. The default
      configuration names no aggregator on that attribute and is
      unaffected (:issue:`1133`).
    * **Behaviour change.** A ``position_score`` annotator answers a
      region no record touches per aggregator, instead of ``None`` for
      every attribute at once. A ``list`` aggregator gives ``[]``, a
      ``value_count`` gives ``{}`` and a ``bool`` gives ``False``; the
      aggregators with no empty answer to give -- ``mean``, ``max``,
      ``min``, ``median``, ``count``, ``mode``, ``concatenate``,
      ``join`` -- still give ``None``. Annotating off the end of a
      chromosome the resource does not have, or a region longer than
      the annotator's cutoff, still answers ``None`` for every
      attribute (:issue:`1131`).
    * **Behaviour change.** A ``position_score`` attribute whose score
      declares no default aggregator and which names none is refused
      when the pipeline loads, rather than annotating a region with its
      per-base expansion -- one value per base pair of a CNV. Only a
      ``bool`` score can be in that position, ``bool`` being the one
      value type with no default reduction; name an aggregator on the
      attribute to fix it. The refusal is at load, so it applies to
      every pipeline carrying such an attribute -- including one that
      only ever annotates substitutions, which never reduce anything
      and were unaffected before (:issue:`1131`).
    * **Behaviour change.** A ``position_score`` annotator raises
      ``ValueError`` on a region starting below position 1, where it
      previously annotated it -- clipping the region to the covered
      part and answering normally. The read it moved onto guards the
      span; the one it left did not. Positions are 1-based, so such a
      region is malformed, but note it is reachable from ordinary
      input: ``annotate_columns`` builds regions with ``int()`` and no
      lower bound, so a 0-based or BED-derived start column produces
      one, and there is no per-row recovery -- a single such row now
      aborts the run. Convert such input to 1-based coordinates before
      annotating (:issue:`1131`).
    * **Behaviour change.** Where a ``position_score``'s records
      overlap, a region annotation counts each covered position once,
      to the first record covering it, instead of counting every record
      at its full clipped width. Two records overlapping by five bases
      previously contributed those five positions twice. Such a
      resource is malformed -- a position score promises one value per
      position, the statistics scan refuses it, and a point lookup
      already took the first record -- so this brings region annotation
      into line with the rest rather than changing what a well-formed
      resource answers (:issue:`1131`).
    * ``PositionScore.fetch_region_weighted_values`` is **removed**. Its
      only caller was the position-score annotator, which reduces
      through ``get_scores_in_region_agg`` now.
      ``PositionScore.resolve_aggregation_queries`` is added, answering
      whether an aggregation query is answerable -- and by which
      aggregator -- without building one or reading anything
      (:issue:`1131`).
    * The **Alleles** section of a genomic score's info page is rebuilt
      around composition. The per-chromosome table gains one share
      column per allele class, taken over that chromosome's own allele
      count, each cell sorting on the fraction and carrying its exact
      count as a hover title; the separate "Allele classes" table is
      removed, its shares becoming the table's total row and its counts
      those titles. The ts/tv ratio renders as a key figure with the
      transition and transversion counts it is computed from
      (:issue:`1118`).
    * The "all chromosomes" total is pinned as a second ``<thead>`` row
      on the Alleles, Coverage and Fragments tables, so it stays visible
      while the table scrolls. No sorting behaviour changes
      (:issue:`1118`).
    * An **allele score** no longer counts covered positions, and its
      info page renders no ``Coverage`` section — the span-union scan
      never applied to the kind, and the distinct-position count that
      stood in for it answered a question the page never asked. A
      position score with unbuilt statistics still reports ``Coverage``
      as not computed (:issue:`1118`).
    * **Stored-format change.** An allele score's indel groups store an
      exact ``{length: count}`` map clamped at ``INDEL_LENGTH_CLAMP``
      (8192) plus ``count``/``sum``/``min``/``max``, in place of the
      log2 histograms ``insertion_length_histogram`` and
      ``deletion_length_histogram``. This makes the new indel statistics
      table -- alleles, min, max, mean and median per group -- exact
      rather than accurate only to bin resolution. The chart's bins are
      derived from the map at render time and are unchanged. Only the
      map is read back, so an allele score built before this reports its
      indel groups as not computed until it is rebuilt with
      ``repo-stats --force``; the statistics hash covers inputs, not the
      computing code, so a plain rebuild will not notice (ADR 0014,
      ADR 0020, :issue:`1118`).
    * The two indel length figures render side by side at half width,
      each opening the full-size image in the info page's existing modal
      (:issue:`1118`).

* 2026.8.5
    * The deprecated ``np_score`` spellings are removed -- the resource
      ``type:`` and the ``np_score`` / ``np_score_annotator`` annotator
      names. Write ``allele_score`` / ``allele_score_annotator``
      instead. The resource ``type:`` is not a plain rename:
      ``np_score`` read in substitutions mode, so a resource that relied
      on that default must also declare ``allele_score_mode:
      substitutions``. The ``np_score`` test-data builders are retired
      with the type (:issue:`918`, :issue:`919`, :issue:`920`).
    * The statistics scan assigns each record to exactly one region --
      the one its ``pos_begin`` falls in -- so no statistic varies with
      ``--region-size`` (:issue:`816`).
    * ``fetch_region_segments`` is the unclipped region read on the
      scores; ``fetch_region_segment_scores`` keeps its clipped meaning
      but is deprecated (:issue:`826`, :issue:`827`).
    * The GTF parser reads ``CDS`` records into the coding interval,
      repairing transcripts GENCODE flags incomplete; picked up when a
      resource is rebuilt (:issue:`909`).
    * The gene-models parsers read tables as text and refuse malformed
      records by name instead of producing ``nan`` transcripts
      (:issue:`907`, :issue:`929`, :issue:`931`, :issue:`963`,
      :issue:`973`).
    * Filename suffixes are matched case-insensitively wherever
      behaviour hangs on one (:issue:`348`, :issue:`975`).
    * The single-allele annotation response links each resource on its
      child repository's declared ``public_url`` (:issue:`838`).
    * Anonymous-quota rows store what a user has consumed, so a raised
      limit applies immediately (:issue:`750`).
    * The score filter language gains ``>=``, ``<=``, ``!=``, ``not``
      and grouping, and filtering becomes a capability of the score
      itself -- ``compile_filter`` plus ``score_filter=`` on the fetch
      methods (:issue:`805`, :issue:`806`, :issue:`820`).
    * ``AlleleScore`` gains a bulk ref/alt column-array read
      (:issue:`780`).
    * The resource info pages document coverage, allele and fragment
      statistics: covered positions, segment and fragment counts,
      length histograms, allele classes, the substitution matrix with
      ts/tv, and indel/complex grids -- in sortable tables, in natural
      chromosome order (:issue:`772`, :issue:`777`, :issue:`779`,
      :issue:`794`, :issue:`988`, :issue:`997`).
    * Repository publishes go through a write-temp-verify-move seam,
      a file's recorded state carries the store's change token (the
      S3 ETag), and downloads reuse the streamed md5 -- a steady-state
      sweep of an unchanged s3 repository takes 5x fewer round trips
      (:issue:`865`, :issue:`880`, :issue:`881`, :issue:`933`,
      :issue:`936`, :issue:`948`).
    * Gene-models format inference reports a per-format rejection
      ledger and breaks the headerless ``refseq``/``ccds`` tie, and
      the GTF parser accepts FlyBase-flavour files (:issue:`853`,
      :issue:`856`, :issue:`869`).
    * The test-data builders gain ``a_gene_models`` (:issue:`315`).
    * ``/api/pipelines/validate`` memoises its verdicts, and the
      annotation worker count is configurable through
      ``GPFWA_ANNOTATION_MAX_WORKERS`` (:issue:`833`, :issue:`837`).
    * Secrets stay out of logs: the backend's daphne access log is
      discarded and the GRR download path redacts url credentials
      (:issue:`620`, :issue:`795`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-08-12T13%3A59%3A14
      Z..2026-08-31T11%3A05%3A07Z>`__.

* 2026.8.4
    * An annotation config's ``value_transform`` is compiled by a
      restricted evaluator instead of a bare ``eval()``; an expression
      outside the allowlist is refused when the pipeline is built
      (:issue:`764`, :issue:`767`).
    * The web API's authentication endpoints are rate-limited, per
      client IP and per submitted address (:issue:`694`).
    * ``grr_manage``'s ``resource-*`` commands write only inside the
      selected resources; the new ``repo-index`` command publishes the
      repository globals from the manifests on disk (:issue:`760`).
    * The repository index is published gzipped only; readers still fall
      back to an uncompressed copy (:issue:`758`).
    * ``fetch_region_values`` is renamed
      ``fetch_region_segment_scores``; the old name is a deprecated
      forwarder (:issue:`729`, :issue:`734`).
    * ``PositionScore`` gains a logical ``get_`` read plane, reading
      the score as a function from position to values (:issue:`727`).
    * ``SearchResources`` reports the group children a search had to
      skip in an ``incomplete`` list (:issue:`686`).
    * Live single-use codes and url credentials are kept out of the
      logs, and anonymous config text cannot forge log records
      (:issue:`629`, :issue:`655`, :issue:`701`, :issue:`754`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-08-07T12%3A26%3A09
      Z..2026-08-12T13%3A59%3A14Z>`__.

* 2026.8.3
    * A 10x ``ann_data`` resource is read whole by default; filtering a
      multiome down to its genes is opt-in configuration. Deployed 10x
      resources take a one-time ``grr_manage -f`` reprocess
      (:issue:`716`).
    * Oversized categorical histograms get truncated sidecars the
      info pages read; ``grr_manage repo-fix-histograms`` migrates a
      repository built before them (:issue:`718`, :issue:`719`,
      :issue:`724`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-08-06T12%3A05%3A32
      Z..2026-08-07T12%3A26%3A09Z>`__.

* 2026.8.2
    * GAIn reads both 10x ``ann_data`` formats itself -- the
      matrix-market triple and the CellRanger ``.h5`` -- and scanpy
      is retired as a dependency. The statistics build no longer
      opens the count matrix, so repairing a large 10x resource
      completes in seconds (:issue:`708`, :issue:`712`).
    * ``GPFWA_NUM_PROXIES`` accepts only an ASCII decimal string, and
      the frontend container's access log writes real log lines
      (:issue:`695`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-08-05T12%3A44%3A07
      Z..2026-08-06T12%3A05%3A32Z>`__.

* 2026.8.1
    * The anonymous pipeline-validation endpoint runs on a bounded pool
      and sheds requests with ``503`` + ``Retry-After`` instead of
      queueing without limit (:issue:`659`, :issue:`676`).
    * The web API derives the client IP in one place, under the new
      ``GPFWA_NUM_PROXIES`` setting (:issue:`667`).
    * A search over a group repository skips a child that cannot
      answer it instead of dying on it (:issue:`680`).
    * Each score kind states its record-ordering rule on both
      statistics scan paths (:issue:`591`, :issue:`592`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-08-04T11%3A46%3A09
      Z..2026-08-05T12%3A44%3A07Z>`__.

* 2026.8.0
    * Record-ordering validation moved off the score read path and into
      the statistics scan; a violation refuses the resource with
      ``MalformedResourceError`` naming the locus and the rule
      (:issue:`587`, :issue:`588`, :issue:`589`, :issue:`590`).
    * A tabix table whose index and configuration name different columns
      is refused at open (:issue:`553`).
    * An unknown score id is refused up front, whatever the region
      holds.
    * A caching repository carries the id of the repository it wraps,
      and ``repository_id`` can select a top-level repository
      (:issue:`447`).
    * ``build_fsspec_protocol`` refuses a writable request over http,
      and a string-valued ``read_only`` resolves to a boolean
      (:issue:`528`).
    * ``draw_score_histograms`` isolates a per-resource failure and
      skips scoreless resources (:issue:`337`, :issue:`537`).
    * New ``ann_data`` resource type: an AnnData single-cell matrix
      served from the GRR, with statistics and an info page
      (:issue:`608`).
    * The resource query language moved into the GRR itself:
      ``search_resources`` takes it, ``grr_manage list`` /
      ``grr_browse`` gain ``-q/--query`` (plus ``-s`` and ``-t``),
      and ``/api/resources/search`` accepts it as ``query=``
      (:issue:`441`, :issue:`442`, :issue:`443`).
    * The GRR index page gains a hierarchical browse view
      (:issue:`560`).
    * The ``cnv_collection`` configuration vocabulary is deprecated
      in favor of ``fragment_score``, warning once per offender until
      2027.1.0 (:issue:`538`).
    * A ``table:`` block's ``index_filename`` is honoured everywhere,
      including the VCF backend and cache prefetch (:issue:`595`,
      :issue:`596`).
    * ``prepare_tabular`` publishes its output and index atomically
      (:issue:`618`).
    * Hardening against untrusted GRR content and anonymous requests:
      control characters in names are refused, browse pages
      autoescape, the query parser bounds its input, and
      ``grr_manage`` refuses a ``dvc add <dir>`` output up front
      (:issue:`284`, :issue:`542`, :issue:`558`, :issue:`635`,
      :issue:`642`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-07-30T12%3A39%3A05
      Z..2026-08-04T11%3A46%3A09Z>`__.

* 2026.7.7
    * ``build_fragment_score_from_resource`` returns a fresh score per
      call instead of a process-wide shared instance.
    * The per-kind score reads are renamed after their kind
      (``fetch_position_scores``, ``fetch_allele_scores``,
      ``fetch_fragment_scores``), ``fetch_fragment_scores`` returns
      score-value dicts instead of ``Fragment`` objects, and
      ``fetch_region`` is removed in favor of ``fetch_region_values``.
      No aliases.
    * ``load_pipeline_from_grr`` is removed; use
      ``load_pipeline_from_file_or_resource`` (:issue:`508`).
    * The bulk statistics scan covers allele, fragment, int- and
      str-valued scores, bit-identical to the per-record path and
      several times faster (:issue:`406`, :issue:`421`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-07-29T12%3A26%3A13
      Z..2026-07-30T12%3A39%3A05Z>`__.

* 2026.7.6
    * A GRR cache must live on a local filesystem; off one, the per-file
      download lock silently degraded to a no-op (:issue:`473`).
    * A repository ``id`` must be a single safe path segment, and ids
      must be unique across the definition tree (:issue:`445`,
      :issue:`460`, :issue:`461`).
    * The ``CnvCollection`` Python surface is renamed
      ``FragmentScore``, and the ``fragment_score`` configuration
      vocabulary is accepted alongside ``cnv_collection``
      (:issue:`470`, :issue:`471`).
    * The resource test-data builders gain ``with_meta`` /
      ``with_labels`` (:issue:`319`).
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-07-28T12%3A55%3A33
      Z..2026-07-29T12%3A26%3A13Z>`__.

* 2026.7.5
    * The per-line score objects are gone from the ``GenomicScore`` API
      -- values are read straight off records (``fetch_records``,
      ``get_score_from_record``); ``chrom`` is required in the region
      reads, and a whole-table read is ``get_all_records()``.
    * A resource score declares a single ``aggregator:`` key; the per-
      kind aggregator keys are removed.
    * BigWig fetch buffering is removed (``direct_fetch_size`` becomes
      ``fetch_size``), and a bigWig score config is validated against
      what the format can honour.
    * ``GenomicScore.aggregate_region`` folds a region into one value
      per requested score, and the typed score factories return
      concrete types (:issue:`438`).
    * The per-record score read path is 17-34% faster, per backend.
    * **Fixed:** `the bug issues closed in this release
      <https://github.com/iossifovlab/gain/issues?q=is%3Aissue+label%3Ab
      ug+is%3Aclosed+reason%3Acompleted+closed%3A2026-07-24T12%3A55%3A06
      Z..2026-07-28T12%3A55%3A33Z>`__.

* 2026.7.4
    * **Fixed:** a ``data_frame`` resource with ``format: excel`` — the
      format announced in 2026.7.3 — could not be read by a stock
      install. pandas opens a workbook through ``openpyxl``, which was
      not a dependency of ``gain-core``, so the read raised an
      ``ImportError`` asking for the missing package.
      ``openpyxl >=3.1`` is now declared everywhere ``gain-core``
      declares its runtime dependencies — the Python distribution, the
      conda environment and the conda package recipe — so an Excel
      ``data_frame`` opens out of the box (#423).

* 2026.7.3
    * **Behavior change:** ``grr_manage --dry-run`` now writes nothing at
      all — it no longer records a ``.grr/<file>.state`` for each file it
      hashes, so it leaves the repository byte-identical; repeated dry
      runs re-hash instead of seeding the state cache (#257).
    * **Behavior change:** ``grr_manage repo-init`` no longer offers
      ``--dry-run`` / ``--force``; both were accepted and ignored, so
      ``repo-init -n`` initialised the repository for real (#415).
    * The statistics scan is now bulk-vectorized for float
      ``position_score`` resources over tabix and bigWig — column arrays
      instead of a ``Record`` per row, and numpy accumulation instead of
      a call per value — making the histogram and min/max passes ~3–4x
      faster on tabix and ~9x on bigWig, bit-identical to the per-record
      path (#385).
    * Every other resource keeps the per-record scan: allele and NP
      scores, VCF-backed tables, non-float or categorical scores, CNV
      collections and whole-table (``--region-size 0``) runs (#385).
    * ``GenomicScore`` exposes the bulk column-array region read
      (``supports_region_value_arrays`` /
      ``fetch_region_value_arrays``, yielding parsed ``float64`` arrays
      per score); a backend declares it through
      ``GenomicPositionTable.supports_value_arrays`` (#398, #409).
    * Turning a raw cell into a score value now lives on
      ``GenomicScoreDef.parse_value`` / ``parse_array``, instead of
      being implemented once per record and once per column (#405).
    * **Fixed:** a literal ``nan`` value token — reachable when
      ``na_values`` is configured without the default ``nan`` sentinel —
      poisoned a score's recorded min/max and nullified its histogram
      (#385).
    * The SpliceAI annotator can run under ONNX Runtime:
      ``SPLICEAI_BACKEND=onnx`` selects it per process, TensorFlow stays
      the default, and an unrecognised value raises (#297).
    * The five SpliceAI ensemble models now ship as ``.onnx`` artifacts
      derived from the committed ``.h5`` weights and pinned to them by
      an equivalence test; ``onnxruntime`` became a runtime dependency
      of ``gain-spliceai-annotator`` (#296).
    * ONNX is not currently the faster backend: measured ~1.08x slower
      than TensorFlow at the default window and ~1.26x at the widest
      (#400).
    * **Fixed:** a Dask batch aborted mid-wiring could deliver its error
      on top of a task's real result, so a run yielded more results than
      the graph had tasks; delivery is now exactly-once per task (#381).
    * **Fixed:** a string-valued gene score with a ``categorical``
      histogram was silently recorded as a ``NullHistogram``, because
      the values were coerced with ``int()`` (#352).
    * **Fixed:** a ``data_frame`` resource read its file relative to the
      process's working directory rather than through the resource, so
      it loaded only by accident; it also now accepts ``format: excel``
      alongside ``csv``/``tsv``, and its info page lists the column
      description one row per column.
    * ``draw_score_histograms`` pointed at a non-score resource now
      fails with a ``TypeError`` naming the resource and its type,
      instead of an empty ``AssertionError`` (#337).
    * CI: the gain-web e2e job reports ``NOT_BUILT`` rather than failing
      when its branch moves past the commit it was handed (#414), and
      its per-agent lock is no longer collapsed onto one global resource
      (#291).
    * CI: the SpliceAI integration tier resolves ``GRR_ROOT`` itself and
      runs against the node-local real GRR (#321).

* 2026.7.2
    * **Behavior change:** ``grr_manage`` now reports every resource it
      cannot process and exits non-zero, instead of skipping it with a
      misleading ``WARNING`` and exiting ``0``; failures are collected
      per resource and summarised, and a failing statistics task,
      FTS-index build or stats-hash write now fails the run (#364).
    * ``grr_manage`` now reports a configuration error as a single
      ``ERROR`` line (traceback demoted to ``DEBUG``), refuses an
      unrecognised management command, reports a message-less exception
      by its class name, and raises a clear ``ValueError`` for a
      headerless tabix table on the default ``header_mode`` (#364).
    * A ``--dry-run`` exits with the count of resources needing an
      update — now counting a resource it could not check; other runs
      exit ``1`` on any failure and ``0`` otherwise (#364).
    * **Fixed:** ``grr_manage repo-manifest --dry-run`` reported a
      settled repository as inconsistent (its exit status was the
      repository size, not the stale count); it now exits ``0`` (#364).
    * **Removed:** the in-resource aggregation engine — the
      ``fetch_scores_agg`` / ``*Query`` / ``*Aggr`` APIs on
      ``PositionScore`` / ``AlleleScore``; aggregate on the annotator
      instead (#267).
    * Resources may now configure ``position_aggregator: count`` /
      ``allele_aggregator: count`` and an empty-separator ``join()``;
      the config schema is derived from the aggregator registry (#261).
    * **Behavior change:** a region is now aggregated per record rather
      than per base pair, so ``mean`` float scores may shift in their
      last bits on large regions (annotation output unchanged) (#260).
    * **Behavior change:** a ``.dvc`` sidecar is now the authoritative
      md5 and size, and ``grr_manage --without-dvc`` (``-D``) is the
      verifier that checks it against the bytes on disk; the default
      mode never hashes a DVC-managed file (#251, #255, #373).
    * Manifest membership is now decided by path, not extension: only
      the generated ``index.html`` and ``statistics/index.html`` are
      excluded, and every other file is manifested (#251, #255, #373).
    * **Behavior change:** ``grr_manage`` now refuses a ``dvc add
      <dir>`` output (a ``.dvc`` sidecar declaring a ``.dir`` md5 or
      ``nfiles``), which GAIn cannot verify (#255).
    * **Behavior change:** the ``--with-dvc`` / ``--without-dvc`` option
      group is restored on the manifest, stats, repair and info
      subcommands (removed in 2026.7.1) (#251).
    * A malformed or incomplete ``.dvc`` file is now reported as a
      warning and ignored rather than aborting ``grr_manage`` (#251).
    * **Fixed:** the ``DaskExecutor`` run loop leaked driver memory by
      minting an uncancelled asyncio Task per pending future on every
      50 ms poll; it now registers one ``add_done_callback`` per future
      (#355).
    * **Fixed:** the Dask run loop could return zero results for a
      non-empty graph via a submit-side window where a task was tracked
      nowhere; a batch now stays queued until its futures are recorded
      (#365).
    * **Fixed:** rewrote Dask run-loop termination around a single
      ``RunState`` state machine, closing a gather-side window that
      could silently under-deliver results (#367).
    * **Fixed:** a crashed or OOM-killed Dask worker's task was yielded
      as ``None`` and cached as a real result, so an all-dead run
      reported success; failed futures are now gathered as errors and
      teardown moved into a ``finally`` so a failure no longer leaks
      worker threads (#367).
    * **Fixed:** an unguarded exception in a ``DaskExecutor`` worker
      thread hung the run forever; both workers now deliver the
      exception as the task result so the run terminates (#372).
    * **Behavior change:** the SpliceAI annotator now refuses a deletion
      longer than the model half-window (``ref_len - 1 > distance``),
      whose batch annotation diverged from the sequential path (#320).
    * **Behavior change:** ``build_annotation_pipeline`` now defaults
      ``work_dir`` to an absolute per-process temp dir instead of the
      cwd-relative ``./work`` (which ``EPERM``'d under a non-root
      runtime in a root-owned cwd) (#331).
    * **Fixed:** tabix reads of overlapping-interval tables returned
      wrong ``get_records_in_region`` results when the ``LineBuffer``
      was warm; ordering is now judged by ``pos_begin`` (#250).
    * **Fixed:** an HTTP GRR download could silently truncate and fail
      only at the md5 check; it now verifies byte count first and raises
      a retryable ``TruncatedDownloadError`` (#292).
    * **Fixed:** a scalar ``na_values`` sentinel was matched by
      substring, silently NA-ing real scores (and raising on bigWig);
      scalars are now matched type-aware by equality (#268).
    * **Fixed:** a VCF ``Number=A`` INFO field on a record with no ALT
      allele (``ALT=.``) now yields a null score instead of leaking the
      raw pysam tuple (#256).
    * **Fixed:** a ``gene_score`` with a null-histogram config crashed
      construction and a runtime histogram failure was dropped; the
      config is accepted and the failure recorded as a ``NullHistogram``
      (#305).
    * **Fixed:** the ``genes`` / ``worst_effect_genes`` columns were
      ``PYTHONHASHSEED``-ordered (deduped via ``set()``); they now
      dedupe order-preservingly and are byte-reproducible (#327).
    * **Fixed:** a tabix table reopened over changed data could answer
      from a stale buffer, and a failed ``close()`` left a live handle;
      ``open()`` now clears the buffer and ``close()`` closes the handle
      first (#362).
    * **Fixed:** the GRR manifest scanner now honors ``.gitignore``
      files between the GRR root and the resource, not only within it
      (#369).
    * **Fixed:** a bigWig query in an unscored gap between buffered
      intervals could return a bogus score; the buffer search now
      returns the insertion point (#259).
    * bigWig region fetch is now sized by record count (adaptive window)
      rather than fixed base-pair chunks; ``direct_fetch_size`` /
      ``buffer_fetch_size`` now mean records per call and
      ``use_buffered_threshold`` is accepted by the schema (#259).
    * ``LineBuffer.prune`` now evicts every dead record instead of
      stopping at the first survivor, bounding buffer growth on dense
      overlapping tabix scans (#287).
    * ``build_genomic_position_table`` now warns when a tabix/in-memory
      table omits ``zero_based`` (#379), or when ``zero_based`` is set on
      a VCF/bigWig table that ignores it (#378).

* 2026.7.1
    * **Behavior change:** a completed anonymous annotation job and its
      result file now survive the user's last WebSocket disconnect;
      stale anonymous jobs are reaped by age via the new
      ``cleanup_anonymous_jobs`` command (nothing schedules it) (#216).
    * Closed the GRR credential-leak paths the 2026.7.0 redaction missed
      — ``grr_browse``, the definition models' ``model_dump*`` and
      credentials embedded in repository URLs; ``get_url()`` /
      ``get_public_url()`` no longer return them and an invalid
      definition raises a redacted ``ValueError`` (#202).
    * **Behavior change:** ``GPFWA_EMAIL_USE_TLS`` is parsed as a
      string, so only a literal ``true`` enables STARTTLS.
    * **Fixed:** the 2026.7.0 ``.gitignore`` scan dropped a DVC-managed
      GRR's data files; a gitignored file is re-included when a sibling
      ``<name>.dvc`` declares it an output (#209/#211).
    * ``grr_browse`` can now filter through the full-text search index
      (``-s``), restrict to one type (``-t``), and print summaries
      (``--summary``).
    * Reworked the notifications WebSocket reconnection to retry with
      exponential backoff and never give up (was dead after five
      attempts), and to survive navigating to a consumer-less page like
      About (#204, #215).
    * **Fixed:** a pipeline whose build failed is now rebuilt on the
      next save instead of the cache returning the failed future.
    * Added ``gain.logging``, a drop-in ``logging`` proxy guaranteeing
      ``TRACE`` / ``USER_INFO`` are installed early; **behavior change:**
      ``-vvv`` now selects ``TRACE`` (effect-checker diagnostics moved
      there too).
    * The single-allele annotation response reports
      ``preserves_domain: true`` rather than ``null`` for a
      non-aggregated attribute.
    * Web UI: fixed three annotation-pipeline editor bugs (stale status
      bar from a late ``pipeline_status``, a refresh resurrecting the
      temporary pipeline, and an ``NG0956`` duplicate-key warning).
    * ``gain.genomic_resources.testing`` became a package with a
      ``builders`` module — a fluent DSL for authoring test GRRs.
    * Documented the ``user`` / ``password`` basic-auth keys of an
      ``http`` repository.
    * CI: ``tests/integration`` moved to a dedicated
      ``gain-core-integration`` downstream job resolving real resources
      against ``grr-seqpipe`` (#222).

* 2026.7.0
    * Hardened HTTP basic-auth credential handling: ``user`` /
      ``password`` are no longer logged and are masked in the definition
      model's ``repr()`` / ``str()``; credentials on a plain ``http://``
      URL to a non-local host now warn.
    * **Behavior change:** repository definitions are strictly validated
      — an unknown key in any definition is rejected (guards auth typos;
      remove stray keys to upgrade).
    * Untyped genomic resources now resolve to a dedicated ``basic``
      resource type that renders an info page and exposes every data
      file, so caching covers the whole resource (#185).
    * The GRR resource file scan now honors ``.gitignore`` files;
      ``pathspec`` is a new ``gain-core`` runtime dependency (#184).
    * Score aggregators now declare
      ``Aggregator.preserves_domain(value_type=…)``, and the Web UI
      hides the score histogram for an attribute whose aggregator does
      not preserve the domain.
    * **Fixed:** the pipeline editor could still hang on "loading" after
      a WebSocket reconnect in a case the 2026.6.9 fix missed; a 200
      from ``GET /api/editor/pipeline_status`` is now the authoritative
      ``loaded`` signal (#160).
    * **Fixed:** a flaky ``FileExistsError`` creating the task-graph log
      directory under concurrency; ``ensure_log_dir`` uses
      ``makedirs(exist_ok=True)`` (#186).
    * Added the ``TRACE`` and ``USER_INFO`` logging levels and the
      ``logger.trace()`` / ``logger.user_info()`` helpers.
    * **Fixed:** generated pipeline docs left a resource/histogram link
      unset for a resource outside the managed GRR; it now falls back to
      the public URL.
    * Enlarged the axis and note-label font on generated histogram
      images.
    * Web UI: fixed several editor and new-annotator dialog layout
      issues.
    * Corrected the "creating an annotator plugin" documentation example
      (``.strip()`` vs ``.lower()``).

* 2026.6.10
    * **Fixed:** reading a VCF score header no longer logs a spurious
      htslib ``Could not retrieve index file`` line; the header open is
      wrapped in ``pysam.set_verbosity(0)``.
    * Added a "creating an annotator plugin" documentation walkthrough.

* 2026.6.9
    * **Fixed:** default-attribute selection for score annotators with a
      ``default_annotation`` (previously none were marked default); gene
      scores with no histogram config fall back to a default histogram.
    * **Fixed:** opening a VCF score header no longer forces a needless
      index lookup over an http GRR.
    * GRR repository definitions are validated against typed pydantic
      schemas rejecting unknown keys and checking required fields.
    * A ``public_url`` may now be set on any repository type, not only
      ``http``/``url``.
    * HTTP GRRs now support basic authentication (``user`` /
      ``password`` passed as ``aiohttp`` credentials).
    * The ``annotate_doc`` CLI now builds links from each resource's
      public URL.
    * Renamed the stored ``Job.annotation_type`` value ``"columns"`` →
      ``"tabular"`` (migration 0043, reversible) (#29).
    * **Behavior change:** saving a user pipeline that references a
      broken GRR resource now succeeds (HTTP 200) and reports the
      failure asynchronously, instead of a synchronous HTTP 400
      (#150/#152).
    * A deferred pipeline build failure now carries a reason via a
      distinct ``failed`` status, surfaced live and durably (#155/#156).
    * **Fixed:** the pipeline editor getting stuck on "loading" after a
      WebSocket reconnect; the consumer replays load status on connect
      (#160).
    * The web API's read endpoints were converted to async, keeping the
      ASGI event loop responsive under load (#162–#167).
    * Web UI: the new-annotator workflow detects duplicate output
      attribute names.
    * Web UI: GRR resource and gene-set-collection page images are
      constrained to a maximum width.
    * Web UI: upgraded Angular to v21.
    * Added a fourth Python-interface documentation example.
    * CI: the anonymous annotate rate-limit is keyed by session under
      the e2e settings so Playwright tests don't cross-exhaust one
      per-IP bucket (#179).

* 2026.6.8
    * ``annotate_vcf`` now supports CSI-indexed input, lifting tabix's
      512 Mbp limit so long contigs can be region-parallelized.
    * Gene-set collections in ``map`` format may now be supplied
      gzip-compressed.
    * **Fixed:** the tabix-indexing step parsed the header with a
      hardcoded tab even for CSV output (#144).
    * **Fixed:** duplicate annotation-job names under concurrency;
      allocation is now a single atomic ``UPDATE … RETURNING`` (#138).
    * **Fixed:** duplicate quota rows under concurrency;
      ``UserQuota.user`` is now one-to-one and ``AnonymousUserQuota.ip``
      unique, with migration 0042 de-duplicating (#139).
    * **Fixed:** a spurious HTTP 400 "Pipeline … not found" under load;
      in-use pipelines are pinned by refcount so eviction can't drop one
      mid-resolve (#140).
    * **Fixed:** running jobs losing their result file on a WebSocket
      disconnect; ``delete_jobs`` skips ``WAITING`` / ``IN_PROGRESS``
      jobs (#147).
    * **Fixed:** deleting a saved pipeline now also evicts it from the
      web API cache.
    * Web UI: per-attribute (gene) aggregator selection renders
      correctly, with finer value-type options driven by the aggregator.
    * Web UI: pipeline-info loading is driven from reactive signals with
      de-duplicated status requests.
    * The GRR browser index table can now be sorted by any column.
    * Revised the getting-started CLI tutorial and GRR documentation.
    * CI: the gain-web e2e pipeline tears down its Docker Compose project
      in ``post.cleanup`` as a backstop.
    * **Behavior change:** ``annotate_tabular`` now reads/writes
      delimited files with quote-aware CSV parsing; a bare ``"`` in
      existing data is now significant, and the separator must be a
      single character.

* 2026.6.7
    * **Fixed:** the VEP effect annotator segfaulting on a bgzipped
      reference genome; the ``.gzi`` index is now declared and
      pre-fetched, and an ``index_file`` override is honored for VEP.
    * **Fixed:** in-page GRR search under a sub-path; the search index is
      fetched relative to the page URL.
    * **Fixed:** the histogram image unreachable from a browser on a
      directory GRR; the live web help builds it from the public URL.
    * **Fixed:** slow pipeline loading under concurrency; the LRU cache
      no longer closes a pipeline while holding the lock.
    * Restructured the getting-started GRR documentation.

* 2026.6.6
    * **Fixed:** ``annotate_tabular`` crashing with
      ``UnicodeDecodeError`` on non-ASCII input; the input is now read as
      UTF-8.
    * **Fixed:** default-verbosity runs printing ``INFO:distributed``
      messages; the logger is re-silenced after the dask cluster builds.
    * **Fixed:** the dask cluster is now torn down gracefully in
      ``DaskExecutor.close()``, ending the previous heartbeat-failure log
      flood.
    * Task-graph executor teardown is now best-effort — a teardown
      failure no longer crashes a run whose results are written.
    * Demoted the fsspec "protocol … already exists" message to debug.
    * **Fixed:** ``grr_manage`` repo-repair rewriting unchanged
      ``index.html`` pages and bumping their mtime, defeating
      mtime-based consumers.
    * Web UI: the new-annotator workflow gained a per-attribute (gene)
      aggregation step.
    * CI: docs-only master builds now archive a ``gain-core`` conda
      package.

* 2026.6.5
    * ``annotate_tabular`` / ``annotate_vcf`` now print a reannotation
      plan (``COPIED`` / ``ADDED`` / ``COMPUTED`` / ``DELETED``), with a
      new ``--dry-run``/``-n`` flag.
    * Reannotation reuse now works through the CLI: an unchanged
      annotator's result is copied rather than recomputed.
    * **Fixed:** ``--full-reannotation`` leaving stale values; every
      annotator is forced to recompute.
    * **Fixed:** a reannotation crash (``TypeError: unhashable type``)
      for annotators with a dict/list-valued parameter.
    * **Fixed:** gzipped gene-score files read with the wrong separator;
      the ``.gz`` suffix is stripped before the ``.tsv`` check.
    * **Fixed:** building gene-score histograms rebuilt a
      ``GeneScoresDb`` per score (O(n²) plus a spurious error).
    * Added a GRR definition-files documentation page.

* 2026.6.4
    * ``grr_manage`` skips rebuilding the full-text search index when the
      repository contents are unchanged (compares the stored
      ``contents_md5``).
    * Revised the getting-started CLI tutorial; removed the obsolete GRR
      YAML/repository reference pages.

* 2026.6.3
    * The annotation web API supports a configurable ``DEFAULT_PIPELINE``
      (shipping as ``pipeline/hg38_clinical_annotation``), pre-selected
      in the UI.
    * ``annotate_tabular`` / ``annotate_vcf`` accept a GRR resource id as
      ``--reannotate``, not only a filesystem path.
    * Removed the per-file variant limit (setting, enforcement and UI
      display).
    * Web UI: saved pipelines expose a download link to their generated
      HTML documentation.
    * **Fixed:** the single-variant API failing when a pipeline annotator
      has no GRR resources; the histogram lookup is skipped.
    * **Fixed:** annotation-config serialization emitting
      ``internal: null``; the field is omitted when unset.
    * Web UI: job notifications arrive in order, number histograms show
      multiple red markers, and pipelines refetch on login change.
    * Revised the getting-started CLI tutorial; documented the
      ``gene_column`` resource.

* 2026.6.2
    * When no output file is given, ``annotate_tabular`` /
      ``annotate_vcf`` insert an ``.annotated`` marker (was
      ``_annotated``).
    * ``annotate_tabular`` / ``annotate_vcf`` now delete a work directory
      they created on a clean run (``--keep-work-dir`` opts out);
      ``annotate_vcf`` also closes its pipeline.
    * Added an ``annotate_doc`` endpoint serving a saved pipeline's
      documentation as a downloadable HTML attachment.
    * **Fixed:** ``allele_score`` filter-grammar edge cases (negative
      literals, digit-leading identifiers).
    * Web UI: the app version shows on the About page, list-valued
      attributes render in the single-variant view, and note labels are
      more prominent.

* 2026.6.1
    * Reference genomes may now be supplied as bgzipped FASTA (a ``.fai``
      faidx and ``.gzi`` index required; optional ``index_file``
      override), working local, cached or over HTTP/S3.
    * ``annotate_columns`` was renamed to ``annotate_tabular`` (old name
      kept as a deprecated alias).
    * Reworked the annotator class hierarchy onto a common
      ``AnnotatorBase``, fixing output-name and aggregation bugs across
      the chrom-mapping, liftover, normalize-allele, gene-set and
      gene-score annotators.
    * The GRR static index embeds a content hash of the full-text search
      database, so browsers re-fetch it after contents change.
    * Resource index links in generated GRR pages are now relative.
    * Pinned ``pysam`` to 0.24.0 and declared ``tqdm`` a ``gain-core``
      runtime dependency.

* 2026.6.0
    * ``grr_cache_repo`` now shows live progress (a ``tqdm`` byte bar on
      a terminal, milestone lines otherwise) and downloads only
      missing/stale files; ``--no-progress`` disables it.
    * Raised the GRR caching read-buffer (``CHUNK_SIZE``) from 32 KiB to
      1 MiB, cutting per-chunk overhead ~32× on large resources.
    * Revised the getting-started CLI and web tutorials.
    * **Backward-incompatible:** ``grr_cache_repo`` now takes the
      pipeline as a positional argument instead of ``--pipeline``/``-p``.
    * ``annotate_tabular`` / ``annotate_vcf`` now treat ``.bgz`` as a
      first-class compression extension for input and output (changing
      the default ``--work-dir`` name derived from a ``.bgz`` output).
    * **Fixed:** silent record duplication when an ``annotate_tabular``
      input carried a ``.csi`` index rather than ``.tbi``.

* 2026.5.10
    * **Fixed:** ``grr_cache_repo`` aborting a long HTTP download at
      htslib's 300 s read cap; the HTTP filesystem now applies only
      per-read/per-connect limits, with backoff retries and resumable
      caching.
    * The anonymous-quota refresh commands now reset each quota in a
      single transaction and also reset ``SessionQuota``.
    * Added a version API endpoint.

* 2026.5.9
    * ``annotate_vcf`` / ``annotate_tabular`` now run a pre-flight
      locality check, warning (1001–5000 rows) or aborting (>5000) when
      the pipeline uses non-local resources;
      ``--allow-remote-resources`` disables it.
    * The GRR index table supports cascading column resize.
    * **Fixed:** a GRR summary tooltip truncated on a double quote.

* 2026.5.8
    * Added a ``prepare_tabular`` CLI that sorts a tabular file by
      genomic coordinates and writes a bgzip-compressed, tabix-indexed
      output for region-parallel annotation.
    * ``annotate_tabular`` defaults ``--input-separator`` to a comma for
      a ``.csv`` input; other inputs still default to a tab.
    * ``annotate_tabular`` / ``annotate_vcf`` run sequentially (``-j 1``)
      when the input cannot be split into regions.
    * ``annotate_tabular`` / ``annotate_vcf`` now run inside their
      ``work_dir`` so downloaded ``.tbi`` indexes don't litter the launch
      directory.
    * **Fixed:** flicker and column-resize lag in the GRR browser index
      table.
    * Expanded the getting-started CLI tutorial with parallelization and
      resource-caching sections.

* 2026.5.7
    * Moved the ``grr_cache_repo`` CLI from ``gpf`` into ``gain``.
    * **Fixed:** the ``--version`` label on the ``grr_manage``,
      ``grr_browse``, ``annotate_columns`` and ``annotate_vcf`` CLIs.
    * The GRR index table supports sorting by the ID column and
      drag-resizing columns.
    * Silenced the spurious htslib "index file is older than the data
      file" warning for parallel-downloaded GRR resources.
    * Revised the getting-started CLI tutorial and overview diagram.

* 2026.5.6
    * Renamed the ``annotate_columns`` CLI to ``annotate_tabular`` (old
      name kept as a deprecated alias).
    * The web UI runtime-injects the Google Analytics snippet from
      ``GA_MEASUREMENT_ID``.
    * Improved the getting-started CLI documentation with installation
      prerequisites.
    * The notifications WebSocket now retries on transport-level errors
      after a 2 s delay.
    * **Fixed:** empty-array table header rendering and scrollable grid
      alignment in the single-annotation report.

* 2026.5.5
    * Moved the ``to_gpf_gene_models_format`` CLI from ``gpf`` into
      ``gain``.

* 2026.5.4
    * Made ``.CONTENTS.json.gz`` and ``.CONTENTS.sqlite3.gz``
      byte-reproducible across platforms.

* 2026.5.3
    * Refactored the allele score annotator to operate only on VCF
      alleles by default; deprecated ``allele_aggregator`` in favor of
      ``aggregator``.
    * **Fixed:** VCF spanning records skipped due to wrong end positions,
      and allele-score position access.
    * Standardized canonical annotator names in the docs and fixed
      new-annotator UI attribute-selection bugs.
    * **Fixed:** a race condition when filtering annotators.
    * URL-encode list/tuple/dict annotation attributes.
    * Improved GRR browser page styles and table layout.
    * **Fixed:** broken annotation infrastructure links.
    * Update the FTS database when creating the contents file; fixed a
      statistics-manifest bug.
    * The single-annotation report now handles array result values.

* 2026.5.2
    * Added admin views for anonymous users and their quotas; monthly
      quotas are always displayed.
    * Anonymous-user quotas are tracked by session ID.
    * Restyled the GRR about and index pages.
    * New template infrastructure for resource implementations.
    * **Fixed:** BigWig score-definition validation.

* 2026.5.1
    * Imported the GAIn user documentation into the repository and added
      Build/Deploy docs CI stages.
    * Updated the quotas page UI and removed quotas from the about page.
