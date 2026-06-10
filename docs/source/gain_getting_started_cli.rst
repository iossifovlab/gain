
Getting started on CLI
======================

Prerequisites
-------------

This guide assumes that you are working on a recent Linux or macOS X machine.

.. warning::

    GAIn is not currently supported on Windows, but it can be run through Windows Subsystem for Linux (WSL) if you have WSL configured.

GAIn is distributed as a Conda package and can be installed with ``conda install``. For faster installation, we recommend using the ``libmamba solver`` with Conda or using Mamba directly. If you do not already have Conda or Mamba installed, or if you are unfamiliar with these package managers, we recommend installing Mamba through the Miniforge distribution, available at: `https://github.com/conda-forge/miniforge <https://github.com/conda-forge/miniforge>`_.


Installation
------------

We assume that you have a working ``mamba`` installation. If you do not have ``mamba`` but have a working ``conda`` installation, replace ``mamba`` with ``conda`` in the commands below. If you have neither, install Mamba through Miniforge as described above.

Start by creating an empty Conda environment named ``gain_cli``:

.. code-block:: bash

    mamba create -n gain_cli

To use this environment, activate it using the following command:

.. code-block:: bash

    mamba activate gain_cli

Then install the ``gain_core`` conda package:

.. code-block:: bash

    mamba install -c conda-forge -c bioconda -c iossifovlab gain-core

This command installs GAIn and all of its dependencies. A simple test to confirm that GAIn is installed correctly is to run:

.. code-block:: bash

    grr_browse --version

The result should look similar to this:

.. code-block:: bash

    GAIn version: 2026.5.5


Note that the version number may be different depending on when you install GAIn, but the command should run without error and print a version number.



Browse available resources
-----------------------------------------


GAIn is installed with access to the default IossifovLab GRR. You can confirm which GRRs are available to you and browse the resources hosted on them by running:


.. code-block:: bash

    grr_browse

This shows that you have access to the IossifovLab GRR server and lists all the resources available from that server.

.. code-block:: bash

    No GRR definition found, using the DEFAULT_DEFINITION
    id: main-GRR
    type: http
    url: https://grr.iossifovlab.com

    gene_score           0      139 11.12 MB     main-GRR gene_properties/gene_scores/GTEx_V11_RNAexpression
    gene_score           0        9 11.84 MB     main-GRR gene_properties/gene_scores/Iossifov_Wigler_PNAS_2015
    gene_score           0       19 2.27 MB      main-GRR gene_properties/gene_scores/LGD
    gene_score           0        9 13.2 MB      main-GRR gene_properties/gene_scores/LOEUF
    gene_score           0       10 1.48 MB      main-GRR gene_properties/gene_scores/RVIS
    gene_score           0        9 202.88 KB    main-GRR gene_properties/gene_scores/SFARI_gene_score_2024_Q1
    ...


This output contains several pieces of information. The first line shows that GAIn is using the default GRR definition, which points to the IossifovLab GRR server. The next three lines show the default configuration. This section is useful for confirming that GAIn is connected to the expected GRR server. In this example, the GRR server is ``https://grr.iossifovlab.com``. The following lines list the resources available on that server, including their type, size, and resource ID. For example, ``gene_properties/gene_scores/GTEx_V11_RNAexpression`` is the resource ID for the GTEx V11 RNA expression gene score resource. Resource IDs are used to refer to resources in annotation pipelines.


Quick annotation test
---------------------

After installation, GAIn can immediately run a small annotation test using the default IossifovLab GRR. This is a useful way to confirm that the command-line tools are working and can access the public resources.

In this example, we annotate a small comma-separated text file containing three variants. The test uses resources directly from the public GRR, so it is convenient for checking the setup but not intended for large annotation jobs.

Download the example input CSV file (:download:`small_input.csv <files/small_input.csv>`), whose content is shown below. The file contains three variant annotatables, each described by the columns ``chrom``, ``pos``, ``ref``, and ``alt``, which specify the chromosome, genomic position, reference allele, and alternate allele:

.. csv-table::
    :file: files/small_input.csv
    :header-rows: 1


To annotate the file, run:

.. code-block:: bash
    
    annotate_tabular small_input.csv pipeline/hg38_clinical_annotation

This command annotates ``small_input.csv`` using the predefined ``pipeline/hg38_clinical_annotation`` pipeline, which is hosted in the default GRR.

GAIn writes the annotated output to a new file whose name is derived from the input file. For example, the command above produces (:download:`small_input.annotated.csv <files/small_input.annotated.csv>`), with the following content:

.. csv-table::
    :file: files/small_input.annotated.csv
    :header-rows: 1


The output contains the original variant columns followed by the annotation attributes produced by ``pipeline/hg38_clinical_annotation``. See the `pipeline summary page <https://grr.iossifovlab.com/pipeline/hg38_clinical_annotation/index.html>`_ in the main GRR for a description of the attributes produced by this pipeline.


Custom annotation pipelines
---------------------------

In the quick annotation test, we used a predefined pipeline from the default GRR. GAIn also allows users to define their own annotation pipelines as YAML files. A custom pipeline is useful when you want to select genomic resources from one or more GRRs that fit a specific project or research question. 

In this example, we will annotate the same three variants from ``small_input.csv``, but this time using a custom pipeline stored locally as ``custom_pipeline.yaml``.

Download the example custom annotation pipeline file (:download:`custom_pipeline.yaml <files/custom_pipeline.yaml>`), whose content is shown below. 


.. literalinclude:: files/custom_pipeline.yaml
    :language: yaml


This pipeline has an optional preamble section, which records metadata about the pipeline and specifies that the input variants use the ``hg38/genomes/GRCh38-hg38`` reference genome. The annotators section lists the annotation steps that GAIn will run from top to bottom. This pipeline first uses the ``MANE 1.5`` gene model to identify affected genes and predict the worst effect of each variant. It then adds a conservation score from ``phyloP7way``. Finally, it normalizes each allele and looks up selected ``ClinVar`` attributes: ``CLNSIG``, which describes clinical significance, and ``CLNDN``, which reports associated disease names. 

.. note:: 

    When building custom annotation pipelines, users can either write the pipeline directly using GAIn's YAML structure or use the pipeline authoring tool in the GAIn web interface, which simplifies pipeline creation by guiding users through annotator and resource selection.

To review the attributes produced by the custom pipeline, run the following command. 

.. code-block:: bash

    annotate_doc custom_pipeline.yaml > doc.html



You can open the generated HTML summary (doc.html) in your local folder. To annotate the input file with this custom pipeline, run:

.. code-block:: bash

    annotate_tabular small_input.csv custom_pipeline.yaml -o small_input_custom.annotated.csv

This command applies the local ``custom_pipeline.yaml`` file to the variants in ``small_input.csv``. To avoid overwriting the output from the previous section, we write the result to (:download:`small_input_custom.annotated.csv <files/small_input_custom.annotated.csv>`), whose content is shown below.

.. csv-table::
    :file: files/small_input_custom.annotated.csv
    :header-rows: 1


This approach is convenient for small tests and for developing custom pipelines. However, when annotation uses resources directly from the public GRR, it is practical only for small inputs. For larger inputs, input files should be sorted by genomic coordinates for more efficient processing. Users can also configure local resource caching and parallel execution, as described in the next sections.


Caching resources
-----------------

By default, GAIn can access genomic resources directly from a remote GRR. This works well for small examples, but large annotation jobs may require repeated access to many large resources over the network. To make these jobs faster and more reliable, GAIn supports local resource caching.

When caching is enabled, GAIn downloads a required resource into a local cache directory the first time the resource is used. After that, GAIn uses the local copy for annotation and reuses it in future jobs without downloading it again.

So far, GAIn has been using the default GRR definition, which corresponds to the configuration shown by the first lines of ``grr_browse``. To enable caching, create a GRR definition file (``~/.grr_definition.yaml``), with the same default GRR configuration plus a ``cache_dir`` entry. For example: 

.. code-block:: yaml

    id: "main-GRR"
    type: "url"
    url: "https://grr.iossifovlab.com"
    cache_dir: "<path_to_cache>/remote_grr_cache"

After this configuration, GAIn downloads each required resource to the specified cache directory before using it for annotation. Because genomic resources can be large, the cache directory should have sufficient disk space and write permission for the user. If ``<path_to_cache>`` does not have enough available space, use another cache directory with sufficient storage. The approximate space requirements for the resources used in this guide are described below.

This is especially important for large annotation pipelines. For example, a comprehensive clinical pipeline such as ``pipeline/hg38_clinical_annotation`` may require many large resources. These resources total approximately 40 GB and may take substantial time to download, depending on network speed and storage performance. Once cached, however, they can be reused directly from the local cache, making future annotation jobs much faster.

GAIn can automatically download required resources during annotation. For large pipelines, however, it is often better to pre-download them before starting the annotation job. GAIn provides a dedicated tool for this purpose:

.. code-block:: bash

    grr_cache_repo pipeline/hg38_clinical_annotation

This command downloads the resources required by the pipeline in one step, so that the actual annotation job does not need to pause while resources are being retrieved.

Custom pipelines can also reduce the amount of data that must be cached. A broad clinical pipeline may require more than 40 GB of resources, whereas a focused custom pipeline may require only the resources needed for a specific analysis. For example, the custom pipeline shown above requires approximately 8 GB of resources. Custom pipelines therefore help control annotation content while reducing storage requirements and setup time. You can cache the resources for the custom pipeline used above with:

.. code-block:: bash

    grr_cache_repo custom_pipeline.yaml

After the necessary resources have been cached, users can run large annotation jobs without waiting for GAIn to download each resource during the annotation process. To test this workflow, download the example input file (:download:`50k_variants.tsv.gz <files/50k_variants.tsv.gz>`), which contains 50,000 variants randomly selected from approximately 1.4 million variants observed by whole-exome sequencing in the SSC project.


Depending on which pipeline you cached above, you can now run the annotation normally:

.. code-block:: bash

    annotate_tabular 50k_variants.tsv.gz pipeline/hg38_clinical_annotation

or

.. code-block:: bash

    annotate_tabular 50k_variants.tsv.gz custom_pipeline.yaml

Without caching, annotating a file of this size through remote resource access can take a very long time. With the required resources already cached, GAIn uses the local copies for annotation, making the same large-scale job much faster and less dependent on network performance. For example, in our test on a recent Mac laptop using cached resources, annotating 50,000 variants with ``pipeline/hg38_clinical_annotation`` took approximately 4 minutes. The input file used in this test was pre-sorted by chromosome and position, which allows GAIn to access genomic resources more efficiently. Unsorted input files can be annotated, but they will run significantly more slowly.


Parallelizing large annotation jobs
-----------------------------------

Annotation can be computationally intensive, especially for large input files or pipelines with many steps. Because GAIn annotates each annotatable independently, these jobs can be accelerated by splitting the input into genomic regions and processing those regions in parallel across multiple CPU cores or cluster workers. Users could do this manually by splitting an input file into chunks, annotating each chunk separately, and merging the results. To avoid this extra workflow management, GAIn provides built-in parallelization support for indexed input files.

To use GAIn's parallelization features, the input file must be sorted by genomic coordinates and indexed with tabix, a widely used genomic indexing tool that is installed automatically with GAIn. This requirement applies to both input formats supported by GAIn: tabular files and VCF files. VCF files can be sorted and indexed with bcftools, while tabular files can be sorted, compressed with bgzip, and indexed with tabix. See the “Preparing annotation input files for parallelization”[] section for details and examples.

When GAIn detects an indexed input file, it splits the annotation job into smaller tasks and executes them in parallel using a Dask cluster. By default, GAIn uses the available CPU cores on the host where the annotation command is run. For larger jobs, users can control both how the input is split and how many workers are used.

The degree of parallelization can be controlled with the ``-j`` option, which specifies the number of workers. The optimal value depends on the input size, pipeline complexity, available CPU cores, memory, and storage performance.


For example, download the example input file (:download:`SSC_WES_variants_select.tsv.gz <files/SSC_WES_variants_select.tsv.gz>`), which contains all 1,413,298  variants on canonical chromosomes detected by WES in the SSC project. You can annotate this large variant collection with the ``pipeline/hg38_clinical_annotation`` pipeline by running the following command. However, even with cached resources, this annotation took approximately 17 minutes in our test:


.. code-block:: bash

    annotate_tabular SSC_WES_variants_select.tsv.gz pipeline/hg38_clinical_annotation


To take advantage of parallel computation, first prepare the input file for indexed genomic access:

.. code-block:: bash

    prepare_tabular SSC_WES_variants_select.tsv.gz

When run successfully, this command produces two files: ``SSC_WES_variants_select.sorted.tsv.bgz``, which contains the sorted and compressed version of the input file, and ``SSC_WES_variants_select.sorted.tsv.bgz.tbi``, its associated tabix index. These two files enable parallelization and fast genomic-region access in GAIn.

The following command uses parallelization, and with the required resources already cached, annotating the sorted file with ``pipeline/hg38_clinical_annotation`` took approximately 1 minute and 15 seconds in our test.

.. code-block:: bash

    annotate_tabular SSC_WES_variants_select.sorted.tsv.bgz pipeline/hg38_clinical_annotation

By default, GAIn splits indexed inputs by chromosome. For human genomes, this creates up to 24 chromosome-level tasks, which is already enough to use all available cores on our local test machine with 10 CPU cores. Therefore, splitting the input further with the ``-r`` option provides only a modest additional benefit on this computer. However, on larger compute systems or clusters with many more cores, chromosome-level splitting may not create enough tasks to fully use the available parallelism. In those cases, the ``-r`` option can split the input into smaller genomic regions and improve scaling. In our test, using the ``-r`` option reduced the annotation time to approximately 1 minute.

.. code-block:: bash

    annotate_tabular SSC_WES_variants_select.sorted.tsv.bgz pipeline/hg38_clinical_annotation -r 30_000_000

GAIn can also use a configured Dask cluster that creates workers on a larger compute system, such as SGE or SLURM. For example, if a Dask cluster named ``my_sge_cluster`` has been configured to create workers on an SGE cluster, the annotation can be run with:

.. code-block:: bash

    annotate_tabular SSC_WES_variants_select.sorted.tsv.bgz pipeline/hg38_clinical_annotation -r 30_000_000 -N my_sge_cluster -j 100

This runs the annotation across up to 100 workers on the configured cluster. See the “Configuring parallelization”[] and “Configuring Dask clusters”[] sections for more details on region splitting, worker configuration, and cluster setup.


Annotating VCF input
-----------------------------

GAIn can also annotate variants stored in VCF files. The command is similar to ``annotate_tabular``, but the input and 
output files are in VCF format. To annotate an example VCF file, download the example input file (:download:`small_input.vcf <files/small_input.vcf>`), whose content is shown below.

.. literalinclude:: files/small_input.vcf
    :language: text


To annotate them, run:

.. code-block:: bash

    annotate_vcf small_input.vcf custom_pipeline.yaml -o vcf.annotated.vcf

This command produces an output file named :download:`vcf.annotated.vcf <files/vcf.annotated.vcf>`, which contains the same variants with 
additional annotation fields in the ``INFO`` column.

.. literalinclude:: files/vcf.annotated.vcf
    :language: text

VCF files can also be prepared for parallel annotation. To do this, first install ``bcftools`` and then sort, compress, and index the VCF file:

.. code-block:: bash

    mamba install -c conda-forge -c bioconda bcftools
    bcftools sort small_input.vcf -o small_input.sorted.vcf.bgz -Oz -Wtbi

This creates a sorted, bgzip-compressed VCF file, ``small_input.sorted.vcf.bgz``, together with its tabix index, ``small_input.sorted.vcf.bgz.tbi``. GAIn can use this indexed VCF file for parallel annotation in the same way as indexed tabular inputs.

Annotating positions and regions
-----------------------------------

GAIn is well suited for annotating genetic variants obtained from sequencing data, 
but not all genomic experiments produce variant calls. Some assays instead identify genomic 
positions or regions of interest, such as transcription start sites mapped by CAGE-seq or 
regulatory intervals detected by ATAC-seq and ChIP-seq. For researchers working with these data types, 
it is often valuable to interpret them using the same kinds of genomic resources used in variant annotation. 
Although positions and regions do not contain allele information, and therefore cannot support every type of 
variant-based annotation, GAIn can still take these inputs and annotate them with many relevant resources using 
the ``annotate_tabular`` tool, aggregating scores when needed.

Position inputs require only two columns: chromosome and position. Download :download:`positions.tsv <files/positions.tsv>`, whose content is shown below: 

.. csv-table::
    :file: files/positions.tsv
    :header-rows: 1
    :delim: tab

Because position inputs do not include reference and alternate alleles, GAIn cannot infer the effect of a specific allelic change on a gene product. However, GAIn provides a dedicated ``simple_effect_annotator`` that can infer the broad genomic context of a position, such as whether it is intergenic, genic, or coding. GAIn can also use other resource types with position inputs and, when needed, aggregate their values to produce a position-level annotation. For example, position score resources map directly to genomic positions and can be applied without modification, while allele score resources can be used by aggregating across the possible allelic changes at that site. In the example below, we use a single pipeline that combines these annotation types. Download annotation pipeline :download:`position_pipeline.yaml <files/position_pipeline.yaml>`, whose content is shown below:

.. literalinclude:: files/position_pipeline.yaml
    :language: yaml


This pipeline combines three annotators. The ``simple_effect_annotator`` uses the ``MANE 1.5`` gene models resource to classify each position by genomic context, such as coding or intergenic, and to report overlapping genes when applicable. 
The ``position_score_annotator`` adds ``phyloP7way`` conservation scores and requests three aggregations: ``max``, ``mean``, and ``list``. The ``max`` and ``mean`` aggregators report the maximum and average score, while ``list`` reports the individual scores before aggregation. For single-position inputs, these aggregators have no effect because there is only one genomic position, but they become useful for region inputs, where scores must be summarized across many positions. The ``allele_score_annotator`` uses ``AlphaMissense`` to summarize possible allelic changes at each position. Here, ``max`` and ``mean`` report the maximum and mean ``am_pathogenicity`` values across possible alleles, while the allele source reports the observed alleles together with their ``am_pathogenicity`` values. Run the following command to annotate the positions:

.. code-block:: bash

    annotate_tabular positions.tsv position_pipeline.yaml

This produces :download:`positions.annotated.tsv <files/positions.annotated.tsv>` which contains:

.. csv-table::
    :file: files/positions.annotated.tsv
    :header-rows: 1
    :delim: tab

This output shows that the first position falls within a coding part of CFTR, whereas the second position is intergenic. The coding position has a higher ``phyloP7way`` conservation score and receives an aggregate ``am_pathogenicity`` score, while no ``am_pathogenicity`` value is reported for the intergenic position.



Region inputs require three columns: chromosome, beginning position, and end position. 
Download the example file :download:`regions.tsv <files/regions.tsv>`, whose content is shown below:

.. csv-table::
    :file: files/regions.tsv
    :header-rows: 1
    :delim: tab


As with position inputs, region inputs do not include reference and alternate alleles, so GAIn cannot infer the effect of a specific allelic change on a gene product. However, many of the same genomic resource types can still be applied to region inputs. Region inputs can also be evaluated with ``simple_effect_annotator``, which summarizes whether a region overlaps genic or intergenic sequence and reports broad functional categories when applicable. Position score resources can be used on region inputs by aggregating values across the positions spanned by each interval. Allele score resources can also be used, but in that case GAIn must aggregate both across the positions in the region and across the possible allelic changes at each position. 

To illustrate this, reuse ``position_pipeline.yaml``, shown below as a reminder.

.. literalinclude:: files/position_pipeline.yaml
    :language: yaml

Then run the following command to annotate the regions:

.. code-block:: bash

    annotate_tabular regions.tsv position_pipeline.yaml

This produces :download:`regions.annotated.tsv <files/regions.annotated.tsv>` which is too large to display in full here.


This output shows how the same pipeline summarizes annotations over genomic intervals. The ``simple_effect_annotator`` reports the broad genomic context of each region and any overlapping genes. For ``phyloP7way``, the ``max`` and ``mean`` columns summarize conservation scores across the positions spanned by each region, while the ``list`` column reports the individual position-level values. For ``AlphaMissense``, GAIn aggregates across both the positions in the region and the possible allelic changes at those positions, producing summary ``am_pathogenicity`` values and listing the contributing alleles when available.



Adding public GRRs
-------------------------

So far, the annotation examples have used resources from the main IossifovLab GRR. We also provide another public repository, `GRR-ENCODE <https://grr-encode.iossifovlab.com/>`_, which contains ENCODE-derived functional genomics tracks that can be used in annotation pipelines. GRR-ENCODE contains approximately 8,000 resources, including ATAC-seq, DNase-seq, histone ChIP-seq, and transcription factor ChIP-seq tracks. 

To use these resources, add GRR-ENCODE to the GRR definition file, ``~/.grr_definition.yaml``. The configuration below connects GAIn to both the main GRR and GRR-ENCODE:

.. code-block:: yaml

    id: "remote_GRRs"
    type: group
    cache_dir: "<path_to_cache>/remote_grr_cache"
    children:
    - id: "main-GRR"
      type: "url"
      url: "https://grr.iossifovlab.com"

    - id: "GRR-ENCODE"
      type: "url"
      url: "https://grr-encode.iossifovlab.com"

With this configuration, GAIn can use resources from both repositories. For example, after adding GRR-ENCODE to the GRR definition file, a pipeline can use an ENCODE ATAC-seq resource as a position score annotator:

.. code-block:: yaml

    - position_score_annotator:
        resource_id: ATAC-seq/ENCSR814RGG

This makes ENCODE-derived regulatory tracks available through the same pipeline syntax used for other position score resources.



Adding local GRRs
-----------------

Suppose you are using the public GRRs for variant annotation, but your analysis also requires a gene-level score that is not available in the main GRR or GRR-ENCODE. For example, you may want to annotate variants with the Collins rCNV 2022 dosage sensitivity scores, including pHaplo and pTriplo, which estimate gene-level sensitivity to deletion and duplication, respectively. In this situation, you can download the external dataset, define it as a local GAIn resource, and use it together with the public GRRs in the same annotation workflow.

Download the Collins rCNV dosage sensitivity score table and inspect the first few lines, shown below, to see the available columns before adding the resource file to a local GRR:

.. code-block:: bash

    curl -L -O https://zenodo.org/record/6347673/files/Collins_rCNV_2022.dosage_sensitivity_scores.tsv.gz
    gzip -dc Collins_rCNV_2022.dosage_sensitivity_scores.tsv.gz | head -n 5

.. csv-table::
    :header-rows: 1

    #gene,pHaplo,pTriplo
    CACNA1C,0.99898184581082,1
    ZNF462,1,0.987995995573708
    CHD8,0.991649600531021,0.999999986508108
    GRIN2B,0.996808517025246,0.999999958700358

The file contains a gene column with the header ``#gene`` and two gene scores, ``pHaplo`` and ``pTriplo``, for each gene. To make this file available as a GAIn resource, place it in a folder together with a :download:`genomic_resource.yaml <files/genomic_resource.yaml>` file:

.. code-block:: text

    My_First_GRR/
    └── my_score/
        ├── Collins_rCNV_2022.dosage_sensitivity_scores.tsv.gz
        └── genomic_resource.yaml

The ``genomic_resource.yaml`` file describes the resource to GAIn:

.. literalinclude:: files/genomic_resource.yaml
    :language: yaml

After creating this folder structure, initialize ``My_First_GRR`` as a local GRR:

.. code-block:: bash

    cd My_First_GRR
    grr_manage repo-init

Then add the local GRR to the GRR definition file, ``~/.grr_definition.yaml``. For example, the configuration below connects GAIn to the main GRR, GRR-ENCODE, and the new local GRR:

.. code-block:: yaml

    type: group
    id: "my_GRRs"
    children:
    - type: group
      id: "remote_GRRs"
      cache_dir: "<path_to_cache>/remote_grr_cache"
      children:
      - id: "main-GRR"
        type: "url"
        url: "https://grr.iossifovlab.com"

      - id: "GRR-ENCODE"
        type: "url"
        url: "https://grr-encode.iossifovlab.com"
    - id: "My_First_GRR"
      type: "directory"
      directory: "<path_to_My_First_GRR>/My_First_GRR"



With this configuration, GAIn can use the local resource in annotation pipelines, as well as the public resources in the main GRR and GRR-ENCODE. For example, the following custom pipeline combines resources from all three repositories: a gene-effect annotator from the main GRR, an ATAC-seq track from GRR-ENCODE, and the ``pHaplo`` score from ``My_First_GRR``.

Download the example pipeline (:download:`multiple_grr_pipeline.yaml <files/multiple_grr_pipeline.yaml>`) which uses multiple GRRs: 

.. literalinclude:: files/multiple_grr_pipeline.yaml
    :language: yaml



To annotate the original example input with this pipeline, run:

.. code-block:: bash

    annotate_tabular small_input.csv multiple_grr_pipeline.yaml -o small_input_multiple_grr.annotated.csv

The output contains the effect annotations, the ENCODE-derived position score, and the ``pHaplo`` score from the local GRR:

.. csv-table::
    :file: files/small_input_multiple_grr.annotated.csv
    :header-rows: 1


Reannotation
------------

When iterating on an analysis, you often want to run a new annotation pipeline on a dataset that has 
already been annotated. If the new pipeline shares any steps with the old one (for example, the same effect annotator or the same score lookup), recomputing those attributes can be wasteful—especially for large annotation jobs.

GAIn supports reannotation, which allows it to reuse attributes that were already computed by a previous pipeline run and only compute what is new. To illustrate this, we will use the clinical annotation pipeline, ``pipeline/hg38_clinical_annotation``. The full contents of this pipeline, including the attributes it produces, can be viewed here: `hg38 clinical annotation pipeline <https://grr.iossifovlab.com/pipeline/hg38_clinical_annotation/index.html>`_. This pipeline annotates ``hg38`` variants with commonly used clinical resources, including gene effects, conservation scores, allele frequencies, clinical significance, and gene-level constraint scores.


To illustrate the runtime of this pipeline, we run it on the larger SSC whole-exome sequencing input used earlier and record the elapsed time. This file contains approximately 1.4 million variants and has been sorted and indexed for parallel annotation (:download:`SSC_WES_variants_select.sorted.tsv.bgz <files/SSC_WES_variants_select.sorted.tsv.bgz>`).

.. code-block:: bash

    time annotate_tabular SSC_WES_variants_select.sorted.tsv.bgz pipeline/hg38_clinical_annotation -o clinical_annotation.tsv.bgz

In our test, this took approximately 7 minutes and produced ``clinical_annotation.tsv.bgz``, which contains the annotations generated by the clinical annotation pipeline.

Now suppose that, after running the clinical annotation pipeline, you want to add one more annotation from GRR-ENCODE. For example, you may be interested in an ATAC-seq position score track, ``ATAC-seq/ENCSR814RGG``. In this case, you can define a modified pipeline, :download:`hg38_clinical_extended.yaml <files/hg38_clinical_extended.yaml>`, that contains the same annotators as ``pipeline/hg38_clinical_annotation`` and adds one additional position-score annotator:

.. code-block:: yaml

    - position_score_annotator: ATAC-seq/ENCSR814RGG

You could run ``hg38_clinical_extended.yaml`` directly on the original input file, but that would recompute all of the clinical annotations that are already present in ``clinical_annotation.tsv.bgz``. Instead, use ``--reannotate`` and pass the original clinical annotation pipeline, so GAIn can reuse the previously computed attributes and only compute the new ATAC-seq annotation. 

The general form of a reannotation command is:

.. code-block:: bash

    annotate_tabular first_annotation second_pipeline --reannotate first_pipeline -o second_annotation

Here, ``first_annotation`` is the output from the previous annotation run, ``second_pipeline`` is the updated pipeline you want to apply, ``first_pipeline`` is the pipeline that produced the existing annotations, and ``second_annotation`` is the new output file. In this example, we also use ``time`` to record the elapsed time:

.. code-block:: bash

    time annotate_tabular clinical_annotation.tsv.bgz hg38_clinical_extended.yaml --reannotate pipeline/hg38_clinical_annotation -o extended_clinical_annotation.tsv.bgz


When you run this command, ``clinical_annotation.tsv.bgz`` is used as the input table. This file already contains the annotations produced by ``pipeline/hg38_clinical_annotation``. The new pipeline, ``hg38_clinical_extended.yaml``, requests the same clinical annotations plus the additional ATAC-seq position score. The key part of the command is ``--reannotate pipeline/hg38_clinical_annotation``: it tells GAIn which pipeline originally generated the annotation columns already present in the input file. GAIn can then recognize the shared annotation steps, reuse the existing attributes, and compute only the new ATAC-seq annotation. In our test, this reannotation step took less than 1 minute, because most of the requested annotations were already present in the input file. The result is written to ``extended_clinical_annotation.tsv.bgz``.