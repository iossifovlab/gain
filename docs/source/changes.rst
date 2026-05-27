Release Notes
=============

* 2026.5.7
    * Silenced the spurious htslib
      ``[W::hts_idx_load3] The index file is older than the data file``
      warning emitted when reading parallel-downloaded GRR resources
      (caching protocol or DVC). htslib verbosity is now level 1
      (errors only) for any process that imports
      ``gain.genomic_resources.fsspec_protocol``.

* 2026.5.6
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
