Genomic Resource configuration
==============================


genomic_resource.yaml overview
------------------------

GAIn supports a large number of genomic resource types (for example, genomes, gene models, and position scores). Each resource lives in its own folder within a GRR and includes the resource files plus a genomic_resource.yaml configuration file. In the sections below, we describe the configuration options available for each resource type. 

All genomic_resource.yaml files share the same top-level structure: the first line sets the resource type (a string that determines how GAIn interprets the resource), and an optional meta section can provide human-readable metadata via summary, description, and labels.

.. code-block:: yaml

    type: <genomic resource type>

    # resource-specific configuration

    meta:
      summary: <(string) Short summary of the resource>
      description: <(string) Longer description of the resource>
      labels: <(dictionary) Arbitrary key/value pairs>

While describing genomic_resource.yaml configuration options, 
we will first cover the resource types whose genomic_resource.yaml 
files are relatively simple (genome, gene models, liftover chains, 
and annotation pipelines). Next, we will cover position score and allele score resources, 
whose configuration files are typically more complex because the underlying data files are 
large and often follow resource-specific conventions. To support these cases, we introduce 
additional options for table and column matching, histogram configuration, and annotation defaults. 
Finally, we cover gene scores (which are similar to position and allele scores) and gene sets, 
which have their own resource-specific configuration in genomic_resource.yaml.


yaml for genomes
----------------

Genome resources use a reference assembly FASTA and (optionally) provide assembly-specific 
metadata such as chromosome naming conventions and pseudoautosomal regions.

Resource-specific fields in genomic_resource.yaml for genome resources (**type**: genome) are:

    | **filename** (string): Path to the genome FASTA file, relative to the resource directory.
    | **chrom_prefix** (string, optional): Prefix expected in contig names (e.g., chr). Default: no prefix.
    | **PARS** (subsection, optional): Pseudoautosomal regions for the assembly.

The genome FASTA may be either a plain ``.fa`` file or a **bgzipped** FASTA
(``.fa.gz`` or ``.bgz``). GAIn selects how to read it from the file extension,
so no extra configuration is required. A bgzipped genome must be accompanied by
**two** index files in the resource directory: a ``.fai`` FASTA index and a
``.gzi`` bgzip block index. Both are produced together by ``samtools faidx``:

.. code-block:: bash

    samtools faidx GRCh38.p14.genome.fa.gz

which writes ``GRCh38.p14.genome.fa.gz.fai`` and ``GRCh38.p14.genome.fa.gz.gzi``
next to the FASTA. (A plain ``.fa`` genome needs only its ``.fai`` index.)

Let's revisit the example genomic_resource.yaml from the `Getting started with GRR genome section <https://iossifovlab.com/gaindocs/gain_getting_started_grr.html#genome-grch38-p14>`_.
Here, filename points to the downloaded FASTA file, contig names use the chr prefix, 
and PARS defines the pseudoautosomal regions on chromosomes X and Y.

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


yaml for gene models
---------------------

For gene model resources, the genomic_resource.yaml file has a minimal resource-specific 
section with only filename and format.

Resource-specific fields (**type**: gene_models):
    | **filename** (string): Path to the gene model file, relative to the resource directory.
    | **format** (string): Gene model format. Supported values include default, refflat, refseq, ccds, knowngene, gtf, and ucscgenepred.

In the `Getting started with GRR gene models <https://iossifovlab.com/gaindocs/gain_getting_started_grr.html#gene-models-mane-v1-4>`_ example, the gene model file is a GTF, so we set ``format: gtf``.


.. code-block:: yaml

    type: gene_models

    filename: MANE.GRCh38.v1.4.ensembl_genomic.gtf.gz
    format: gtf

    meta:
      summary: MANE gene model version 1.4

yaml for liftover chains 
-------------------------

For liftover chain resources, the genomic_resource.yaml file has a minimal resource-specific section with only filename.

Resource-specific fields (**type**: liftover_chain):
  | **filename** (string): Path to the chain file, relative to the resource directory.

.. code-block:: yaml

    type: liftover_chain
    filename: hg38-chm13v2.over.chain.gz
    meta: 
      summary: Liftover Chain hg38 to T2T


yaml for annotation pipelines
-----------------------------

For annotation pipeline resources, the genomic_resource.yaml file has a minimal resource-specific section with only filename.

Resource-specific fields (**type**: annotation_pipeline):
    | **filename** (string): Path to the pipeline YAML file, relative to the resource directory.

.. code-block:: yaml

    type: annotation_pipeline
    filename: Clinical_annotation.yaml 
    meta:
      summary: Clinical Annotation Pipeline 



yaml for position scores
------------------------

Position score resources (**type**: position_score) use a genomic_resource.yaml file with three resource-specific sections: table, scores, and (optionally) default_annotation.

**1. table**: 

The table section specifies the data file (**filename**), its **format**, and how GAIn should interpret the columns.

Currently supported formats are tabix, vcf_info, tsv, csv, and bw. Auto-detection of the format works for the following **.

The header_mode setting controls how column names (the header) are determined:
    | **file**: Extract the header from the file (default).
    | **list**: Use the explicit header provided via header.
    | **none**: No header is used; columns can only be referenced by index.

The **header** field is used only when header_mode is set to list. Example:

.. code-block:: yaml

    header_mode: list
    header: ["chrom", "start", "end", "score_value"]


The user must tell GAIn which columns correspond to chrom (chromosome), pos_begin (start position), and pos_end (end position). This can be done by column index or by column name.

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

If the resource file includes a header, columns can be specified by name. In the next example, positionscore2.bedGraph.gz has columns named chr and pos:

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
    The first line must be a header with the column names chrom and file_chrom. 
    Values in file_chrom are what appear in the resource file, and values in chrom are what 
    they will be mapped to. For example:

.. code-block:: yaml

    chrom           file_chrom
    Chromosome_1     1
    Chromosome_22    22

An example of using chrom_mapping 
(useful when the resource uses a chr prefix but the genome does not) is shown below:

.. code-block:: yaml

    table:
    ...
      chrom_mapping:
        add_prefix: "chr"


**2. scores**: 

The table section configures how the data file is read. 
The scores section specifies which score columns to extract, how to name them in the GRR, 
and what data type they should have. For example, the minimal configuration below extracts a 
float score from column index 2 and stores it under the id my_positionscore1:

.. code-block:: yaml

    scores:
    - id: my_positionscore1
      type: float
      index: 2

Alternatively, score columns can be specified by name. In the next example, the score column in the file is named positionscore2, and the extracted score is stored under the id my_positionscore2:

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
and control how the score distribution is displayed. Histogram options are covered 
in a dedicated section***. The example below shows a custom histogram within a complete scores entry. 
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


**3. default_annotation**: 

Annotation pipelines can choose which scores from a resource to use. If a pipeline does not explicitly specify scores for this resource, GAIn falls back to the resource's default_annotation list. If default_annotation is not provided, all scores in the resource are used by default. An example is shown below ***:

.. code-block:: yaml

    default_annotation:
    - source: my_positionscore2
      name: my_positionscore2

Putting all the pieces together, the following is a complete genomic_resource.yaml example for a position score resource. The optional meta field is omitted for conciseness.

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






yaml for allele scores
-----------------------

Genomic_resource.yaml files for allele score resources are almost exactly the same 
as for position score resources, with three differences:

1. **type**: allele_score

2. **allele_score_mode** must be specified. Options are: []

   | substitutions: single nucleotide substitutions (for example, C>T)
   | allele: covers all allele types (for example, insertions and deletions in addition to substitutions)

3. In the table section, the user must also specify which columns contain the **reference** and **alternative** alleles using reference and alternative.

The scores, default_annotation, and meta sections are the same as for position scores. The example below shows the beginning of a valid genomic_resource.yaml for an allele score resource:


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

histogram configuration
-------------------------

Histograms provide a quick visual summary of how a score is distributed across 
the genome or across observed variants. Seeing the distribution is often as important 
as seeing individual values, because it helps interpret what “large” or “small” values 
typically look like for a given score and whether the score has outliers, heavy tails, 
or distinct modes.

For each score, the HTML summary page shows a default histogram whenever it is possible 
to compute one from the underlying data. [] Histogram configuration is optional. If a score 
includes a histogram block under scores, GAIn uses it to override the default display and 
control how the distribution is visualized.

Histogram behavior is controlled by the type field, which selects the histogram implementation. 
GAIn supports three histogram types: number for numeric scores, categorical for string or 
discrete category scores, and null to explicitly disable histogram computation/display when a 
histogram is not meaningful. The value of type must be exactly one of number, categorical, or null.

Some options are shared across number and categorical histogram types. For example, y_log_scale 
controls whether the y-axis is displayed on a log scale (default: False), which can be 
helpful when counts vary widely across bins or categories. x_log_scale controls whether 
the x-axis is displayed on a log scale (default: False). When x_log_scale is set to 
True, x_min_log defines the minimum x-axis value used for the logarithmic scale. 
The example below shows a minimal histogram configuration that overrides the default 
by enabling log-scale display on the y-axis for a numeric score. Other options depend 
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
  | **view_range**: the visible range on the x-axis using min and max values, which is useful for bounded scores (for example, 0–1) or for focusing on the region of interest without being dominated by extreme outliers. Default is showing all values[].

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
or review-status labels). Categorical histograms are supported for scores of type str and int. 
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
categorical histogram rendering with displayed_values_count and label_rotation. The second uses 
plot_function, which overrides the default categorical histogram rendering.

Example 1: built-in categorical histogram options (top 5 values + label rotation)

.. code-block:: yaml

  histogram:
    type: categorical
    displayed_values_count: 5
    label_rotation: 90

Example 2: custom categorical histogram rendering using plot_function

.. code-block:: yaml

  histogram:
    type: categorical
    plot_function: "customplot1.py:my_own_plot"

For GAIn to render the second histogram using a custom plotting function, place a Python module such as customplot1.py that contains the function my_own_plot in the resource directory. The custom function must render and write a plot to the provided output stream (outfile) so it can be embedded in the HTML summary output. A simple example that sorts categories by their counts, keeps the top 20, and renders a basic bar chart (with optional log-scaled y-axis) to the provided output stream is []:

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


auto generated score definition
------------------------

VCF files provide enough information to allow automatic generation of score definitions.
These definitions can be overriden manually if necessary, either partially or fully.

Example VCF file:

.. code:: bash

    ##fileformat=VCFv4.1
    ##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
    #CHROM POS ID REF ALT QUAL FILTER  INFO
    chr1   5   .  A   T   .    .       A=1

Score ``A`` will get auto generated score definition as if created by configuration like this:
    
.. code:: yaml

    scores:
    - id: A
      type: int
      column_name: A
      desc: Score A

Some fields cannot be automatically generated. Use overriding to add more fields or change existing auto generated fields.
Define manually which score definitions should be overriden by first specifying the score id,
then add new fields (like ``histogram``) or override existing auto generated (like ``type``):

.. code:: yaml

    scores:
    - id: A
      type: float
      histogram:
        type: categorical
        value_order: ["alpha", "beta"]

The resulting score definition with updated ``type`` and added ``histogram`` will be equivalent to the following configuration:

.. code:: yaml

    scores:
    - id: A
      type: float
      column_name: A
      desc: Score A
      histogram:
        type: categorical
        value_order: ["alpha", "beta"]

How VCF types correspond to our types

  =========  ======
  VCF        GAIn
  =========  ======
  Integer    int
  Float      float
  String     str
  Flag       bool
  =========  ======


how to generate tabix files
---------------------------

Note - in order to use tabix, the score file must already be compressed using ``bgzip``.

.. code:: bash

  $ tabix --help

  Version: 1.22.1
  Usage:   tabix [OPTIONS] [FILE] [REGION [...]]

  Indexing Options:
    -0, --zero-based           coordinates are zero-based
    -b, --begin INT            column number for region start [4]
    -c, --comment CHAR         skip comment lines starting with CHAR [null]
    -C, --csi                  generate CSI index for VCF (default is TBI)
    -e, --end INT              column number for region end (if no end, set INT to -b) [5]
    -f, --force                overwrite existing index without asking
    -m, --min-shift INT        set minimal interval size for CSI indices to 2^INT [14]
    -p, --preset STR           gff, bed, sam, vcf, gaf
    -s, --sequence INT         column number for sequence names (suppressed by -p) [1]
    -S, --skip-lines INT       skip first INT lines [0]

  Querying and other options:
    -h, --print-header         print also the header lines
    -H, --only-header          print only the header lines
    -l, --list-chroms          list chromosome names
    -r, --reheader FILE        replace the header with the content of FILE
    -R, --regions FILE         restrict to regions listed in the file
    -T, --targets FILE         similar to -R but streams rather than index-jumps
    -D                         do not download the index file
        --cache INT            set cache size to INT megabytes (0 disables) [10]
        --separate-regions     separate the output by corresponding regions
        --verbosity INT        set verbosity [3]
    -@, --threads INT          number of additional threads to use [0]


.. code:: bash

  $ bgzip --help

  Version: 1.22.1
  Usage:   bgzip [OPTIONS] [FILE] ...
  Options:
    -b, --offset INT           decompress at virtual file pointer (0-based uncompressed offset)
    -c, --stdout               write on standard output, keep original files unchanged
    -d, --decompress           decompress
    -f, --force                overwrite files without asking
    -g, --rebgzip              use an index file to bgzip a file
    -h, --help                 give this help
    -i, --index                compress and create BGZF index
    -I, --index-name FILE      name of BGZF index file [file.gz.gzi]
    -k, --keep                 don't delete input files during operation
    -l, --compress-level INT   Compression level to use when compressing; 0 to 9, or -1 for default [-1]
    -o, --output FILE          write to file, keep original files unchanged
    -r, --reindex              (re)index compressed file
    -s, --size INT             decompress INT bytes (uncompressed size)
    -t, --test                 test integrity of compressed file
        --binary               Don't align blocks with text lines
    -@, --threads INT          number of compression threads to use [1]

**Example usage of ``tabix``**

For a VCF-format score:

.. code:: bash

    $ tabix -p vcf score.vcf.gz

For a 1-based TSV score with a single position column:

.. code:: bash

    $ tabix -s 1 -b 2 score.tsv.gz

For a 1-based TSV score with start and stop position columns:

.. code:: bash

    $ tabix -s 1 -b 2 -e 3 score.tsv.gz

For a 0-based TSV score with start and stop position columns:

.. code:: bash

    $ tabix -0 -s 1 -b 2 -e 3 score.tsv.gz
