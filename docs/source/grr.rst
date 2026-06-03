Genomic resources and repositories
==============================

A Genomic Resource Repository (GRR) is a collection of genomic resources (e.g.,
genomes, gene models, scores, and gene sets) stored either locally (on disk) or
remotely (over the network). GAIn uses GRRs as the backing store for resources
during annotation and analysis.

Repository discovery
--------------------
By default, GAIn looks for a configuration file named ``.grr_definition.yaml``
in your home directory to determine which GRRs are available. If the file is
not present, GAIn defaults to using the public IossifovLab GRR.

To configure which GRRs GAIn uses by default, create a file named ``.grr_definition.yaml``
in your home directory. The example below reproduces the default behavior by
pointing GAIn to the public `IossifovLab GRR <https://grr.iossifovlab.com/>`_ (a remote repository accessed via URL):

.. code:: yaml

    id: development
    type: group
    children:
    - id: GRR
      type: url
      url: https://grr.iossifovlab.com

If you replace ``.grr_definition.yaml`` with the next example, GAIn will resolve resources from your local directory-based
GRR (created in “`Getting Started in GRR <https://iossifovlab.com/gaindocs/gain_getting_started_grr.html>`_”).
This overrides the default behavior, so the public IossifovLab GRR will no longer be used unless you add it explicitly.

.. code:: yaml

    id: development
    type: group
    children:
    - id: grr_local
      type: directory
      directory: [path to my_grr]/my_grr

The configuration below defines two GRRs and searches them in order. When GAIn resolves a resource ID, it
first queries the GRR with id GRR (the public IossifovLab GRR). If the resource is not found there, GAIn then queries the GRR with id ``grr_local``.

.. code:: yaml

    id: development
    type: group
    children:
    - id: GRR
      type: url
      url: https://grr.iossifovlab.com
    - id: grr_local
      type: directory
      directory: [path to my_grr]/my_grr





Repository configuration
------------------------

A repository configuration is a YAML mapping with a required id and type,
plus additional fields depending on the repository type.

Common fields

    | **id** (string, required): Identifier for the repository.
    | **type** (string, required): directory, http, url, embedded, or group.

Type-specific fields

    | **type**: directory (local filesystem)
    | **directory**: (string, required) Path to a local directory containing resources.

    | **type**: http (remote HTTP)
    | **url** (string, required): Base URL of the remote repository.
    | **cache_dir** (string, optional): Directory used to cache downloaded resources.

    | **type**: url (remote object store, e.g., S3-style URL)
    | **url** (string, required): URL of the remote repository.
    | **cache_dir** (string, optional): Directory used to cache downloaded resources.

    | **type**: embedded (in-memory definition)
    | **content** (mapping, required): Nested dictionary that describes files and directories. Directory values are nested mappings. File values are file contents.

    | **type**: group (a collection of repositories)
    | **children** (list, required): List of repository configurations. When resolving a resource ID, repositories are searched in the order they appear in children.


Repository caching
-----------------------

When a repository is configured with a ``cache_dir`` option, GAIn caches
resources locally before using them. This matters because many genomic
resources are large (often hundreds of MB to many GB), and repeatedly downloading
or streaming them from a remote GRR can be slow and network-dependent.

With caching enabled, the first use of a resource may take longer while GAIn
downloads it into ``cache_dir``. After that, GAIn reuses the cached copy, which is
typically much faster and avoids repeated network transfers. This is especially useful for
resources you access frequently (for example, common reference genomes, gene models, or widely used scores).

The tradeoff is disk usage: cached resources can occupy substantial space,
so choose a ``cache_dir`` location with enough capacity (and keep in mind that
the cache may grow over time as you use more resources).


Repository management
-------------------------------------------------------

GAIn provides two command-line tools for working with genomic resources and repositories. Their usage is outlined below.

    | **grr_manage**: create, inspect, and maintain GRRs (manifests, stats, info pages, repair).
    | **grr_browse**: browse the resources available through a GRR definition file.



**grr_manage**

.. code-block:: bash

    $ grr_manage --help
    usage: grr_manage [-h] [--version] [--verbose] [--logfile LOGFILE]
                    {list,repo-init,repo-manifest,resource-manifest,repo-stats,resource-stats,repo-info,resource-info,repo-repair,resource-repair}
                    ...

    Genomic Resource Repository Management Tool

    positional arguments:
    {list,repo-init,repo-manifest,resource-manifest,repo-stats,resource-stats,repo-info,resource-info,repo-repair,resource-repair}
                            Command to execute
        list                List a GR Repo
        repo-init           Initialize a directory to turn it into a GRR
        repo-manifest       Create/update manifests for whole GRR
        resource-manifest   Create/update manifests for a resource
        repo-stats          Build the statistics for a resource
        resource-stats      Build the statistics for a resource
        repo-info           Build the index.html for the whole GRR
        resource-info       Build the index.html for the specific resource
        repo-repair         Update/rebuild manifest and histograms whole GRR
        resource-repair     Update/rebuild manifest and histograms for a resource

    options:
    -h, --help            show this help message and exit
    --version             Prints GAIn version and exists.
    --verbose, -v, -V
    --logfile LOGFILE     File to log output to. If not set, logs to console.


**grr_browse**

.. code-block:: bash

    $ grr_browse --help
    usage: grr_browse [-h] [--version] [--verbose] [--logfile LOGFILE] [-g GRR]
                    [--bytes]

    Genomic Resource Repository Browse Tool

    options:
    -h, --help         show this help message and exit
    --version          Prints GAIn version and exists.
    --verbose, -v, -V
    --logfile LOGFILE  File to log output to. If not set, logs to console.
    --bytes            Print the resource size in bytes

    Repository/Resource:
    -g GRR, --grr GRR  path to GRR definition file.




Genomic resource configuration
------------------------

GAIn supports a large number of genomic resource types (for example, genomes, gene models, and position scores).
Each resource lives in its own folder within a GRR and includes the resource files plus a ``genomic_resource.yaml``
configuration file. In the sections below, we describe the configuration options available for each resource type.

All ``genomic_resource.yaml`` files share the same top-level structure: the first line sets the resource type (a string that
determines how GAIn interprets the resource), and an optional meta section can provide human-readable metadata via summary,
description, and labels.

.. code-block:: yaml

    type: <genomic resource type>

    # resource-specific configuration

    meta:
      summary: <(string) Short summary of the resource>
      description: <(string) Longer description of the resource>
      labels: <(dictionary) Arbitrary key/value pairs>

While describing ``genomic_resource.yaml`` configuration options,
we will first cover the resource types whose ``genomic_resource.yaml``
files are relatively simple (genome, gene models, liftover chains,
and annotation pipelines). Next, we will cover position score and allele score resources,
whose configuration files are typically more complex because the underlying data files are
large and often follow resource-specific conventions. To support these cases, we introduce
additional options for table and column matching, histogram configuration, and annotation defaults.
Finally, we cover gene scores (which are similar to position and allele scores) and gene sets,
which have their own resource-specific configuration in ``genomic_resource.yaml``.


Genomes
^^^^^^^

Genome resources use a reference assembly FASTA and (optionally) provide assembly-specific
metadata such as chromosome naming conventions and pseudoautosomal regions.

Resource-specific fields in ``genomic_resource.yaml`` for genome resources (**type**: genome) are:

    | **filename** (string): Path to the genome FASTA file, relative to the resource directory.
    | **index_file** (string, optional): Path to the FASTA ``.fai`` index, relative to the resource directory. Default: ``<filename>.fai``.
    | **chrom_prefix** (string, optional): Prefix expected in contig names (e.g., chr). Default: no prefix.
    | **PARS** (subsection, optional): Pseudoautosomal regions for the assembly.

The genome FASTA may be either a plain ``.fa`` file or a **bgzipped** FASTA
(``.fa.gz`` or ``.bgz``). GAIn selects how to read the sequence from the file
extension — a bgzipped genome is read with random access via ``pysam.FastaFile``
— so no extra configuration is required. A plain ``.fa`` genome needs only its
``.fai`` index; a bgzipped genome must be accompanied by **two** index files in
the resource directory: a ``.fai`` FASTA index and a ``.gzi`` bgzip block index.
Both are produced together by ``samtools faidx``:

.. code-block:: bash

    samtools faidx GRCh38.p14.genome.fa.gz

which writes ``GRCh38.p14.genome.fa.gz.fai`` and ``GRCh38.p14.genome.fa.gz.gzi``
next to the FASTA.

A bgzipped genome is configured exactly like a plain one — only the ``filename``
extension differs:

.. code-block:: yaml

    type: genome
    filename: GRCh38.p14.genome.fa.gz
    chrom_prefix: "chr"

    meta:
      summary: Nucleotide sequence of the GRCh38.p14 genome assembly (bgzipped)

Let's revisit the example ``genomic_resource.yaml`` from the `Getting started with GRR genome section <https://iossifovlab.com/gaindocs/gain_getting_started_grr.html#genome-grch38-p14>`_.
As before, filename points to the downloaded FASTA file and contig names use the ``chr`` prefix. We now also include
``PARS``, which defines the pseudoautosomal regions on chromosomes X and Y.

.. code-block:: yaml

    type: genome
    filename: GRCh38.p14.genome.fa
    chrom_prefix: "chr"

    PARS:
    "X":
        - "chrX:10000-2781479"
        - "chrX:155701382-156030895"
    "Y":
        - "chrY:10000-2781479"
        - "chrY:56887902-57217415"

    meta:
      summary: Nucleotide sequence of the GRCh38.p14 genome assembly


Gene models
^^^^^^^^

For gene model resources, the ``genomic_resource.yaml`` file has a minimal resource-specific
section with only filename and format.

Resource-specific fields (**type**: gene_models):
    | **filename** (string): Path to the gene model file, relative to the resource directory.
    | **format** (string): Gene model format. Supported values include default, refflat, refseq, ccds, knowngene, gtf, and ucscgenepred.

In the `Getting started with GRR gene models <https://iossifovlab.com/gaindocs/gain_getting_started_grr.html#gene-models-mane-v1-4>`_ example,
the gene model file is a GTF, so we set ``format: gtf``.


.. code-block:: yaml

    type: gene_models

    filename: MANE.GRCh38.v1.4.ensembl_genomic.gtf.gz
    format: gtf

    meta:
      summary: MANE gene model version 1.4

Liftover chains
^^^^^^^^

For liftover chain resources, the ``genomic_resource.yaml`` file has a minimal resource-specific section with only filename.

Resource-specific fields (**type**: liftover_chain):
  | **filename** (string): Path to the chain file, relative to the resource directory.

.. code-block:: yaml

    type: liftover_chain
    filename: hg38-chm13v2.over.chain.gz
    meta:
      summary: Liftover Chain hg38 to T2T


Annotation pipelines
^^^^^^^^

For annotation pipeline resources, the ``genomic_resource.yaml`` file has a minimal resource-specific section with only filename.

Resource-specific fields (**type**: annotation_pipeline):
    | **filename** (string): Path to the pipeline YAML file, relative to the resource directory.

.. code-block:: yaml

    type: annotation_pipeline
    filename: Clinical_annotation.yaml
    meta:
      summary: Clinical Annotation Pipeline



Position scores
^^^^^^^^

Position score resources (**type**: position_score) use a ``genomic_resource.yaml`` file with three resource-specific sections:
``table``, ``scores``, and (optionally) ``default_annotation``.

**table**
"""""""""""""""

The ``table`` section specifies the data file (**filename**), its **format**, and how GAIn should interpret the columns.

Currently supported formats are ``tabix``, ``vcf_info``, ``tsv``, ``csv``, and ``bw``. Auto-detection of the format works for the following:

The header_mode setting controls how column names (the header) are determined:
    | **file**: Extract the header from the file (default).
    | **list**: Use the explicit header provided via header.
    | **none**: No header is used; columns can only be referenced by index.

The **header** field is used only when ``header_mode`` is set to list. Example:

.. code-block:: yaml

    header_mode: list
    header: ["chrom", "start", "end", "score_value"]


The user must tell GAIn which columns correspond to ``chrom`` (chromosome), ``pos_begin`` (start position), and ``pos_end`` (end position).
This can be done by column index or by column name.

If the resource file has no header, columns must be specified by index. For example:

.. code-block:: yaml

    table:
      filename: positionscore1.bedGraph.gz
      format: tabix
      header_mode: none
      chrom:
        index: 0
      pos_begin:
        index: 1
      pos_end:
        index: 2

If the resource file includes a header, columns can be specified by name. In the next example, ``positionscore2.bedGraph.gz`` has
columns named ``chr`` and ``pos``:

.. code-block:: yaml

    table:
      filename: positionscore2.bedGraph.gz
      format: tabix
      header_mode: file
      chrom:
        name: chr
      pos_begin:
        name: pos
      pos_end:
        name: pos

The table section also supports **chrom_mapping**, which can be used to reconcile chromosome
naming differences between the resource file and the reference genome. This is useful, for example,
when the resource uses contig names like chr1 but the genome uses only numbers.

Three options are available under chrom_mapping:

    | **add_prefix**: Takes a string value and adds it as a prefix.
    | **del_prefix**: Takes a string value and removes it from the start of each chromosome name.
    | **zero_based**: Controls the coordinate convention used when reading the score. Set to true for BED-style coordinates (0-based, half-open). Leave it as the default (false) to use GAIn's internal format (1-based, closed intervals).
    | **filename**: Takes a filepath (relative to the genomic resource directory).
    The file must contain two whitespace-delimited columns.
    The first line must be a header with the column names ``chrom`` and ``file_chrom``.
    Values in ``file_chrom`` are what appear in the resource file, and values in ``chrom`` are what
    they will be mapped to. For example:

.. code-block:: yaml

    chrom           file_chrom
    Chromosome_1     1
    Chromosome_22    22

An example of using ``chrom_mapping``
(useful when the resource uses a ``chr`` prefix but the genome does not) is shown below:

.. code-block:: yaml

    table:
    ...
      chrom_mapping:
        add_prefix: "chr"


**scores**
"""""""""""""""

The ``table`` section configures how the data file is read.
The ``scores`` section specifies which score columns to extract, how to name them in the GRR,
and what data type they should have. For example, the minimal configuration below extracts a
float score from column index 2 and stores it under the id ``my_positionscore1``:

.. code-block:: yaml

    scores:
    - id: my_positionscore1
      type: float
      index: 2

Alternatively, score columns can be specified by name. In the next example, the score column in the file is named ``positionscore2``,
and the extracted score is stored under the id ``my_positionscore2``:

.. code-block:: yaml

    scores:
    - id: my_positionscore2
      type: float
      name: positionscore2

Optionally, the user may also add human-readable descriptions.
These fields are used on the HTML summary page for the resource. For example:

.. code-block:: yaml

    desc: "conservation score"
    large_values_desc: "more conserved"
    small_values_desc: "less conserved"

The HTML summary page displays a default histogram for each score.
Optionally, the user may provide a histogram configuration to override the default
and control how the score distribution is displayed. Histogram configuration options are covered `here <https://iossifovlab.com/gaindocs/grr.html#histogram-configuration>`_. The example below shows a custom histogram within a complete scores entry.
If the resource includes multiple scores, add additional entries under scores with different id values.

.. code-block:: yaml

    scores:
    - id: my_positionscore2
      type: float
      name: positionscore2

      desc: "conservation score"
      large_values_desc: "more conserved"
      small_values_desc: "less conserved"

      histogram:
        type: number
        number_of_bins: 100
        view_range:
          min: 0.0
          max: 1.0
        y_log_scale: True


**default_annotation**
"""""""""""""""

Annotation pipelines can choose which scores from a resource to use. If a pipeline does not explicitly specify scores for this resource,
GAIn falls back to the resource's ``default_annotation`` list. If ``default_annotation`` is not provided, all scores in the resource are
used by default. An example is shown below ***:

.. code-block:: yaml

    default_annotation:
    - source: my_positionscore2
      name: my_positionscore2

Putting all the pieces together, the following is a complete ``genomic_resource.yaml`` example for a position score resource.
The optional ``meta`` field is omitted for conciseness.

.. code-block:: yaml

    type: position_score                         # resource type

    table:                                       # how to read the input table
      filename: positionscore2.bedGraph.gz       # input file (relative path)
      format: tabix                              # file format
      header_mode: file                          # read header from file
      chrom:                                     # chromosome column
        name: chr                                # column name
      pos_begin:                                 # start position column
        name: pos                                # column name
      pos_end:                                   # end position column
        name: pos                                # column name

    scores:                                      # how to extract data columns as scores
      - id: my_positionscore2                    # score id stored in GRR
        type: float                              # data type of the score values
        name: positionscore2                     # column name containing the score

        desc: "a description"                    # shown on the HTML summary page
        large_values_desc: "more"                # meaning of larger values (HTML)
        small_values_desc: "less"                # meaning of smaller values (HTML)

        histogram:                               # optional histogram override (HTML)
          type: number                           # numeric histogram
          number_of_bins: 100                    # bin count used in the histogram
          view_range:                            # visible range shown on the x-axis
            min: 0.0                             # minimum visible range in the histogram
            max: 1.0                             # maximum visible range in the histogram
          y_log_scale: True                      # use log scale on the y-axis

    default_annotation:                          # default scores used for annotation
      - source: my_positionscore2                # score id to annotate from
        name: my_positionscore2                  # name of the annotation field






Allele scores
^^^^^^^^

`genomic_resource.yaml` files for allele score resources are almost exactly the same
as for position score resources, with three differences:

1. **type**: allele_score

2. **allele_score_mode** must be specified. Options are:

   | substitutions: single nucleotide substitutions (for example, C>T)
   | allele: covers all allele types (for example, insertions and deletions in addition to substitutions)

3. In the ``table`` section, the user must also specify which columns contain the **reference** and **alternative** alleles using reference and alternative.

The scores, ``default_annotation``, and ``meta`` sections are the same as for position scores. The example below shows the beginning of
a valid ``genomic_resource.yaml`` for an allele score resource:


.. code-block:: yaml

    type: allele_score
    allele_score_mode: substitutions

    table:
      filename: AlphaMissense_hg38_modified.tsv.gz
      format: tabix

      chrom:
        name: CHROM
      pos_begin:
        name: POS
      pos_end:
        name: POS
      reference:
        name: REF
      alternative:
        name: ALT

    ... (scores, default_annotation, and meta sections follow) ...


CNV collections
^^^^^^^^^^^^^^^

``genomic_resource.yaml`` files for CNV collection resources are the same as for
position score resources, except that the resource type is set to ``cnv_collection``.

CNV collections are coordinate-based, like position scores: they are queried by chromosome and interval and do not model allele changes.
Annotation consists of reporting overlapping CNVs and the selected associated fields (for example, CNV class and frequency).

The example below shows a valid ``genomic_resource.yaml`` for a CNV collection resource (``my_CNVcollection.txt``),
which uses ``chrom``, ``pos_begin`` and ``pos_end`` as column names for chromosome, beginning
position and end position, respectively. It and also has a column called ``deletion_duplication``
which describes the event type recorded.

.. code-block:: yaml

    type: cnv_collection
    table:
      filename: my_CNVcollection.txt

    scores:
    - id: CNV type
      name: deletion_duplication
      type: str
      desc: duplication or deletion

    meta:
      summary: CNV collection resource


Gene scores
^^^^^^^^^^^

Gene scores are gene-level annotations, such as constraint metrics, expression summaries, or intolerance scores.
``genomic_resource.yaml`` files for gene score resources are similar to position score resources,
except that the resource type is set to ``gene_score`` and there is no ``table`` section. The underlying data file
is a table whose gene identifier column must be named ``gene``.


In the example ``genomic_resource.yaml`` file below, data file ``gene_scores.tsv`` contains a required column named ``gene``,
plus two score columns named ``constraint`` and ``intolerance``. The ``scores`` section defines which columns are exposed as scores,
and ``default_annotation`` works the same way as for position scores.

The HTML summary page displays a default histogram for each score. Optionally, the user may provide a
histogram configuration to override the default and control how the score distribution is displayed, as shown for the ``constraint_score`` in this example.
Histogram configuration options are covered `here <https://iossifovlab.com/gaindocs/grr.html#histogram-configuration>`_.

.. code-block:: yaml

    type: gene_score

    filename: gene_scores.tsv

    scores:
    - id: intolerance_score
      desc: Probability of Loss-of-Function Intolerance

    - id: constraint_score
      desc: Gene conservation score
      histogram:
        type: number
        number_of_bins: 126
        view_range:
          min: 0
          max: 1
        x_min_log: 0.00001
        x_log_scale: false
        y_log_scale: true

    default_annotation:
    - source: constraint_score
      name: constraint_score

    meta:
      summary: Gene score resource

Gene set collections
^^^^^^^^^^^^

A ``gene_set_collection`` defines relationships between genes and gene sets.
These relationships can be provided either directly as gene sets (``gmt`` format) or
as gene-set mappings (``map`` format). In both cases, the underlying structure is the same:
a many-to-many association between genes and sets.

In ``gmt`` format, each line of the file directly defines a gene set and its member genes.
In this format, each row corresponds to a single gene set. The first column defines
the set identifier, the second column typically provides a description, and the remaining
columns list the genes belonging to that set. No additional processing is required to
construct the gene sets.

``example.gmt``, an example ``gmt`` data file:

.. code-block:: text

    PATHWAY_A   Description of pathway A    GENE1    GENE2    GENE3
    PATHWAY_B   Description of pathway B    GENE2    GENE4

Example ``genomic_resource.yaml`` file for a ``gmt`` gene set collection resource:

.. code-block:: yaml

    type: gene_set_collection
    id: example_gmt
    format: gmt
    filename: example.gmt

    meta:
      summary: Minimal GMT example

In ``map`` format, each row defines a relationship between a gene and a gene set.
The first column contains the gene identifier, and the second column contains the set
identifier. Gene sets are formed by grouping all rows with the same set identifier.
A companion file may optionally be provided to associate each set identifier with a
human-readable description.

``example-map.txt``, an example ``map`` file:

.. code-block:: text

    GENE1   SET_A
    GENE2   SET_A
    GENE3   SET_A
    GENE2   SET_B
    GENE4   SET_B

Optional companion file: ``example-mapnames.txt``

.. code-block:: text

    SET_A   Pathway A description
    SET_B   Pathway B description

Example ``genomic_resource.yaml`` file for a ``map`` gene set collection resource:

.. code-block:: yaml

    type: gene_set_collection
    id: example_map
    format: map
    filename: example-map.txt

    histograms:
      genes_per_gene_set:
        type: number
        y_log_scale: true

      gene_sets_per_gene:
        type: number
        y_log_scale: true

    meta:
      summary: Example MAP-based gene set collection

For both ``gmt`` and ``map`` resources, the optional ``histograms`` section can be used to summarize the structure of the
collection. For example, ``genes_per_gene_set`` describes the distribution of gene set sizes,
while ``gene_sets_per_gene`` describes how many sets each gene belongs to.


Histogram configuration
-------------------------

Histograms provide a quick visual summary of how a score is distributed across
the genome or across observed variants. Seeing the distribution is often as important
as seeing individual values, because it helps interpret what “large” or “small” values
typically look like for a given score and whether the score has outliers, heavy tails,
or distinct modes.

For each score, the HTML summary page shows a default histogram whenever it is possible
to compute one from the underlying data. Histogram configuration is optional. If a score
includes a histogram block under scores, GAIn uses it to override the default display and
control how the distribution is visualized.

Histogram behavior is controlled by the ``type`` field, which selects the histogram implementation.
GAIn supports three histogram types: ``number`` for numeric scores, ``categorical`` for string or
discrete category scores, and ``null`` to explicitly disable histogram computation/display when a
histogram is not meaningful. The value of type must be exactly one of number, categorical, or null.

Some options are shared across number and categorical histogram types. For example, ``y_log_scale``
controls whether the ``y-axis`` is displayed on a log scale (default: ``False``), which can be
helpful when counts vary widely across bins or categories. ``x_log_scale`` controls whether
the ``x-axis`` is displayed on a log scale (default: ``False``). When ``x_log_scale`` is set to
``True``, ``x_min_log`` defines the minimum ``x-axis`` value used for the logarithmic scale.
The example below shows a minimal histogram configuration that overrides the default
by enabling log-scale display on the ``y-axis`` for a numeric score. Other options depend
on the selected type and are described in the sections below.


.. code-block:: yaml

  scores:
    - id: myscore
      column_name: RS
      type: int
      desc: a genomic score

      histogram:
        type: number
        y_log_scale: True


**Number histograms**

Number histograms are used for numeric scores, including continuous-valued scores and
integer-valued scores. They are supported for scores of type int and float. By default,
the histogram is calculated with 100 bins and uses linear scaling on both axes. They summarize
the distribution by grouping values into bins along the x-axis and showing the number of observations
per bin.

A number histogram configuration supports two options.

  | **number_of_bins**: number of bins used to partition the score values (default: []).
  | **view_range**: the visible range on the x-axis using min and max values, which is useful for bounded scores (for example, 0-1) or for focusing on the region of interest without being dominated by extreme outliers. Default is showing all values.

The example below shows a number histogram configuration with an explicit bin count and visible range.

.. code-block:: yaml

  histogram:
    type: number
    number_of_bins: 10
    view_range:
      min: 0.0
      max: 1.0


**Categorical histograms**

Here, each value represents a discrete label (e.g., ClinVar clinical significance categories
or review-status labels). Categorical histograms are supported for scores of type ``str`` and ``int``.
This histogram type shows the distribution of unique values in the score and is supported only
for scores with fewer than 100 unique values. They summarize the distribution by counting how many
observations fall into each unique value and displaying those counts.

A categorical histogram configuration supports five options.

  | **displayed_values_count**: the number of unique values that will be displayed in the histogram (default: 20). The remaining values are grouped into the Other category.
  | **displayed_values_percent**: the percentage of total mass of unique values that will be displayed. The remaining values are grouped into the Other category. Only one of displayed_values_count and displayed_values_percent can be set.
  | **label_rotation**: rotation angle for x-axis category labels in degrees (default: []).
  | **value_order**: the order in which the unique values are displayed in the histogram.
  | **plot_function**: optional custom plotting function used instead of the default categorical histogram rendering. This is useful when the default plot and the available options are not sufficient, for example to reorder, filter, or relabel categories. The value should be provided as <python module>:<python function>, where the Python module path is relative to the resource directory. When plot_function is set, GAIn uses the custom function to render the histogram and ignores built-in categorical histogram options such as displayed_values_count, displayed_values_percent, and label_rotation.

The examples below show two common categorical histogram setups. The first uses the built-in
categorical histogram rendering with ``displayed_values_count`` and ``label_rotation``. The second uses
``plot_function``, which overrides the default categorical histogram rendering.

Example 1: built-in categorical histogram options (top 5 values + label rotation)

.. code-block:: yaml

  histogram:
    type: categorical
    displayed_values_count: 5
    label_rotation: 90

Example 2: custom categorical histogram rendering using ``plot_function``

.. code-block:: yaml

  histogram:
    type: categorical
    plot_function: "customplot1.py:my_own_plot"

For GAIn to render the second histogram using a custom plotting function, place a Python module such as ``customplot1.py`` that contains
the function ``my_own_plot`` in the resource directory. The custom function must render and write a plot to the provided output stream
(outfile) so it can be embedded in the HTML summary output. A simple example that sorts categories by their counts, keeps the top 20,
and renders a basic bar chart (with optional log-scaled ``y-axis``) to the provided output stream is:

.. code-block:: yaml

  from typing import IO
  from dae.genomic_resources.histogram import CategoricalHistogram
  import matplotlib.pyplot as plt

  def my_own_plot(outfile: IO, histogram: CategoricalHistogram, xlabel: str, *_args, **_kw) -> None:
      items = sorted(histogram.raw_values.items(), key=lambda x: -x[1])[:20]
      labels, counts = zip(*items) if items else ([], [])
      plt.figure()
      plt.bar(labels, counts, log=histogram.config.y_log_scale)
      plt.xlabel(xlabel); plt.ylabel("count")
      plt.savefig(outfile); plt.clf()

**Null histograms**

Null histograms are used when calculating a histogram is not possible or does not make sense for a score.
In this case, the HTML summary page will not display a histogram for the score, and instead records the reason why histogram display is disabled.

A null histogram configuration supports one required field.

  | **reason**: a short explanation of why the histogram is disabled.

Example:


.. code-block:: yaml

  histogram:
    type: null
    reason: "Histogram is not available for this score."


VCF score auto-detection
------------------------

VCF files already describe many score-like fields in their headers. In particular,
each ##INFO line provides an ID, a type, and a human-readable description. GAIn uses this
metadata to automatically create score definitions for ``INFO`` fields, which you can then reference
in configuration just like manually defined scores.

Create the following file and save it as ``example.vcf``, which contains a single ``INFO`` field A:

.. code:: bash

    ##fileformat=VCFv4.1
    ##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
    #CHROM POS ID REF ALT QUAL FILTER  INFO
    chr1   5   .  A   T   .    .       A=1


Create the following ``genomic_resource.yaml`` for this score which omits an explicit scores section.

.. code:: yaml

    type: position_score

    table:
      filename: example.vcf
      format: vcf_info

When you run ``grr_manage resource-repair``, the scores and their descriptions will be automatically generated from the ``INFO``
field in the vcf file.


The configuration above is equivalent to spelling out the generated score definition explicitly:

.. code:: yaml

    type: position_score

    table:
      filename: example.vcf
      format: vcf_info

    scores:
    - id: A
      type: int
      column_name: A
      desc: Score A


Some fields cannot be automatically generated. To customize a generated definition, add a ``scores:`` entry with the same
id and include only the fields you want to change or extend (for example, overriding type or adding a histogram block):

.. code:: yaml

    scores:
    - id: A
      type: float
      histogram:
        type: categorical
        value_order: ["alpha", "beta"]



GAIn derives each score's type directly from the VCF ``INFO`` field type:
``Integer`` maps to ``int``, ``Float`` to ``float``, String to ``str``, and ``Flag`` to ``bool``.





Tabix indexing
---------------------------

Many GAIn resource types are backed by on-disk tables, typically tab-delimited genomic files
(for example TSV/BED-like tables, bedGraph, or VCF-derived tables). These files can be large, but GAIn
still needs to look up the records that overlap a given genomic interval during annotation (for example,
chr1:100000-101000). Scanning the full file for every query would be too slow, so GAIn supports
Tabix-indexed tables for fast random access by genomic region. (Some resource formats such as ``bigWig``
are already indexed and do not use Tabix.)

When you set ``format: tabix`` under a resource's table section, you are telling GAIn that the data
file is bgzip-compressed, coordinate-sorted, and accompanied by a Tabix index (``.tbi`` or ``.csi``). With
that index in place, GAIn can jump directly to the relevant file blocks, and your table: mapping
tells it how to interpret each row (which columns provide ``chrom``, ``pos_begin``, and ``pos_end``, plus any
header handling you specify).

The main pitfall is coordinate conventions: BED-style files are typically 0-based,
half-open, while many TSV tables and VCF positions are 1-based. Keep the tabix indexing flags
(for example -0) consistent with the file, and set ``zero_based`` accordingly in the resource YAML
to avoid subtle off-by-one overlaps.


**Common options:**

    * **-p, --preset**: preset parser for common formats (e.g., vcf, bed, gff), which sets the expected coordinate columns automatically.
    * **-s, --sequence**: 1-based column index for the chromosome/contig (sequence name) column.
    * **-b, --begin**: 1-based column index for the start (begin) coordinate column.
    * **-e, --end**: 1-based column index for the end (stop) coordinate column. If the file has no end column, set ``-e`` to the same value as ``-b`` (single-position intervals).
    * **-0, --zero-based**: interpret coordinates as 0-based (BED-style) instead of 1-based.
    * **-C, --csi**: generate a CSI index instead of the default TBI index (useful for very large coordinates/contigs).
    * **-f, --force**: overwrite an existing index file.


For a full list of options run ``tabix --help``. The examples below show how to produce Tabix indexes for
common file layouts.


**example usage of tabix**

For a VCF-format score (-p vcf: use the VCF preset):

.. code:: bash

    $ tabix -p vcf score.vcf.gz

For a 1-based TSV score with a single position column (-s: chrom column, -b: pos column, -e: same as -b):

.. code:: bash

    $ tabix -s 1 -b 2 -e 2 score.tsv.gz


For a 1-based TSV score with start and stop position columns (-s: chrom, -b: start, -e: end):

.. code:: bash

    $ tabix -s 1 -b 2 -e 3 score.tsv.gz


For a 0-based TSV score with start and stop position columns (-0: 0-based coordinates, plus -s/-b/-e as above):

.. code:: bash

    $ tabix -0 -s 1 -b 2 -e 3 score.tsv.gz
