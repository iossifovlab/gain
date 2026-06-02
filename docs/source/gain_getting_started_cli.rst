
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


This output contains several pieces of information. The first line shows that GAIn is using the default GRR definition, which points to the IossifovLab GRR server. The next three lines show the default configuration. This section is useful for confirming that GAIn is connected to the expected GRR server. In this example, the GRR server is ``https://grr.iossifovlab.com`` and the resource namespace is ``default``. The following lines list the resources available on that server, including their type, size, and resource ID. For example, ``default/gene_properties/gene_scores/GTEx_V11_RNAexpression`` is the resource ID for the GTEx V11 RNA expression gene score resource. Resource IDs are used to refer to resources in annotation pipelines.


Quick annotation test
---------------------

After installation, GAIn can immediately run a small annotation test using the default IossifovLab GRR. This is a useful way to confirm that the command-line tools are working and can access the public resources.

In this example, we annotate a small tab-separated text file containing three variants. The test uses resources directly from the public GRR, so it is convenient for checking the setup but not intended for large annotation jobs.

Download the example input CSV file (:download:`small_input.csv<files/small_input.csv>`), whose content is shown below. The file contains three variant annotatables, each described by the columns ``chrom``, ``pos``, ``ref``, and ``alt``, which specify the chromosome, genomic position, reference allele, and alternate allele:

.. csv-table::
    :file: files/small_input.csv
    :header-rows: 1


To annotate the file, run:

.. code-block:: bash
    
    annotate_tabular small_input.csv pipeline/hg38_clinical_annotation

This command annotates ``small_input.csv`` using the predefined ``pipeline/hg38_clinical_annotation`` pipeline, which is hosted in the default GRR.

GAIn writes the annotated output to a new file whose name is derived from the input file. For example, the command above produces (:download:`small_input_annotated.csv<files/small_input_annotated.csv>`), with the following content:

.. csv-table::
    :file: files/small_input_annotated.csv
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

To review the attributes produced by the custom pipeline, run the following command. The generated HTML summary can be viewed here: `doc.html <_static/doc.html>`_.

.. code-block:: bash

    annotate_doc custom_pipeline.yaml > doc.html



To annotate the input file with this custom pipeline, run:

.. code-block:: bash

    annotate_tabular small_input.csv custom_pipeline.yaml -o small_input_custom_annotated.csv

This command applies the local ``custom_pipeline.yaml`` file to the variants in ``small_input.csv``. To avoid overwriting the output from the previous section, we write the result to (:download:`small_input_custom_annotated.csv <files/small_input_custom_annotated.csv>`), whose content is shown below.

.. csv-table::
    :file: files/small_input_custom_annotated.csv
    :header-rows: 1


This approach is convenient for small tests and for developing custom pipelines. However, when annotation uses resources directly from the public GRR, it is practical only for small inputs. For larger inputs, input files should be sorted by genomic coordinates for more efficient processing. Users can also configure local resource caching and parallel execution, as described in the next sections.


Caching resources for large annotation jobs
-------------------------------------------

By default, GAIn can access genomic resources directly from a remote GRR. This works well for small examples, but large annotation jobs may require repeated access to many large resources over the network. To make these jobs faster and more reliable, GAIn supports local resource caching.

When caching is enabled, GAIn downloads a required resource into a local cache directory the first time the resource is used. After that, GAIn uses the local copy for annotation and reuses it in future jobs without downloading it again.

So far, GAIn has been using the default GRR definition, which corresponds to the configuration shown by the first lines of ``grr_browse``. To enable caching, create a GRR definition file (``~/.grr_definition.yaml``), with the same default GRR configuration plus a ``cache_dir`` entry. For example: 

.. code-block:: yaml

    id: "main-GRR"
    type: "url"
    url: "https://grr.iossifovlab.com"
    cache_dir: "<path_to_cache>/remote_grr_cache"

After this configuration, GAIn downloads each required resource to the specified cache directory before using it for annotation. Because genomic resources can be large, the cache directory should have sufficient disk space and write permission for the user. If <path_to_cache> does not have enough available space, use another cache directory with sufficient storage. The approximate space requirements for the resources used in this guide are described below.

This is especially important for large annotation pipelines. For example, a comprehensive clinical pipeline such as ``pipeline/hg38_clinical_annotation`` may require many large resources. These resources total approximately 40 GB and may take substantial time to download, depending on network speed and storage performance. Once cached, however, they can be reused directly from the local cache, making future annotation jobs much faster.

GAIn can automatically download required resources during annotation. For large pipelines, however, it is often better to pre-download them before starting the annotation job. GAIn provides a dedicated tool for this purpose:

.. code-block:: bash

    grr_cache_repo pipeline/hg38_clinical_annotation

This command downloads the resources required by the pipeline in one step, so that the actual annotation job does not need to pause while resources are being retrieved.

Custom pipelines can also reduce the amount of data that must be cached. A broad clinical pipeline may require more than 40 GB of resources, whereas a focused custom pipeline may require only the resources needed for a specific analysis. For example, the custom pipeline shown above requires approximately 8 GB of resources. Custom pipelines therefore help control annotation content while reducing storage requirements and setup time. You can cache the resources for the custom pipeline used above with:

.. code-block:: bash

    grr_cache_repo custom_pipeline.yaml

After the necessary resources have been cached, users can run large annotation jobs without waiting for GAIn to download each resource during the annotation process. To test this workflow, download the example input file (:download:`50k_variants.tsv.gz <files/50k_variants.tsv.gz>`), which contains 50,000 variants.


Depending on which pipeline you cached above, you can now run the annotation normally:

.. code-block:: bash

    annotate_tabular 50k_variants.tsv.gz pipeline/hg38_clinical_annotation

or

.. code-block:: bash

    annotate_tabular 50k_variants.tsv.gz custom_pipeline.yaml

Without caching, annotating a file of this size through remote resource access can take a very long time. With the required resources already cached, GAIn uses the local copies for annotation, making the same large-scale job much faster and less dependent on network performance. For example, annotating 50,000 variants with pipeline/hg38_clinical_annotation takes approximately 4 minutes on a local computer.

GAIn works more efficiently on input files sorted by genomic coordinates. The original 50k_variants.tsv.gz file is not sorted. To sort it by chromosome and position while preserving the header, run:

.. code-block:: bash

    (
    gzip -dc 50k_variants.tsv.gz | head -n 1
    gzip -dc 50k_variants.tsv.gz | tail -n +2 | LC_ALL=C sort -t $'\t' -k1,1V -k2,2n
    ) | bgzip > 50k_variants.sorted.tsv.bgz

You can also download the sorted file here:  (:download:`50k_variants.sorted.tsv.gz <files/50k_variants.sorted.tsv.gz>`). Then rerun the annotation on the sorted file:

.. code-block:: bash

    annotate_tabular 50k_variants.tsv.sorted.gz custom_pipeline.yaml

In this example, sorting reduces the runtime from approximately 4 minutes to approximately 3 minutes.

Parallelizing large annotation jobs
-----------------------------------

Annotation can be computationally intensive, especially for large input files or pipelines with many steps. Because GAIn annotates each annotatable independently, these jobs can be accelerated by splitting the input into genomic regions and processing those regions in parallel across multiple CPU cores or cluster workers. Users could do this manually by splitting an input file into chunks, annotating each chunk separately, and merging the results. To avoid this extra workflow management, GAIn provides built-in parallelization support for indexed input files.

To use GAIn's parallelization features, the input file must be sorted by genomic coordinates and indexed with tabix, a widely used genomic indexing tool that is installed automatically with GAIn. This requirement applies to both input formats supported by GAIn: tabular files and VCF files. VCF files can be sorted and indexed with bcftools, while tabular files can be sorted, compressed with bgzip, and indexed with tabix. See the “Preparing annotation input files for parallelization”[] section for details and examples.

When GAIn detects an indexed input file, it splits the annotation job into smaller tasks and executes them in parallel using a Dask cluster. By default, GAIn uses the available CPU cores on the host where the annotation command is run. For larger jobs, users can control both how the input is split and how many workers are used.

The degree of parallelization can be controlled with the ``-j`` option, which specifies the number of workers. The optimal value depends on the input size, pipeline complexity, available CPU cores, memory, and storage performance.


For example, after downloading the example input file (:download:`1million_variants.tsv.gz <files/1million_variants.tsv.gz>`), which contains 1 million variants detected by WES from the SSC project, prepare it for parallel annotation by running:

.. code-block:: bash

    prepare_tabular 1million_variants.tsv.gz

When run successfully, this command produces two files: ``1million_variants.sorted.tsv.bgz``, which contains the sorted and compressed version of the input file, and ``1million_variants.sorted.tsv.bgz.tbi``, its associated tabix index. These two files enable parallelization and fast genomic-region access in GAIn.

The following command uses parallelization, and with the required resources already cached, it can complete very quickly. In this example, annotating the sorted one-million-variant file with the custom pipeline took approximately 1 minute.

.. code-block:: bash

    annotate_tabular 1million_variants.sorted.tsv.bgz custom_pipeline.yaml

GAIn splits indexed inputs by chromosome. For very large input files, chromosome-level splitting may create tasks that are too large or uneven. The ``-r`` option can instead split the input into genomic regions of a specified size. In this example, using the ``-r`` option reduced the annotation time to approximately 45 seconds.

.. code-block:: bash

    annotate_tabular 1million_variants.sorted.tsv.bgz custom_pipeline.yaml -r 30_000_000

GAIn can also use a configured Dask cluster that creates workers on a larger compute system, such as SGE or SLURM. For example, if a Dask cluster named ``my_sge_cluster`` has been configured to create workers on an SGE cluster, the annotation can be run with:

.. code-block:: bash

    annotate_tabular 1million_variants.sorted.tsv.bgz custom_pipeline.yaml -r 30_000_000 -N my_sge_cluster -j 100

This runs the annotation across up to 100 workers on the configured cluster. See the “Configuring parallelization”[] and “Configuring Dask clusters”[] sections for more details on region splitting, worker configuration, and cluster setup.




Annotating VCF input
-----------------------------

GAIn can also annotate variants stored in VCF files. The command is similar to ``annotate_tabular``, but the input and 
output files are in VCF format. To annotate an example VCF file, download the example input file (:download:`small_input.vcf <files/small_input.vcf>`), whose content is shown below.

.. literalinclude:: files/small_input.vcf
    :language: text


To annotate them, run:

.. code-block:: bash

    annotate_vcf small_input.vcf custom_pipeline.yaml -o vcf_annotated.vcf

This command produces an output file named :download:`vcf_annotated.vcf <files/vcf_annotated.vcf>`, which contains the same variants with 
additional annotation fields in the ``INFO`` column.

.. literalinclude:: files/vcf_annotated.vcf
    :language: text


Annotating positions and regions
-----------------------------------

GAIn is well suited for annotating genetic variants obtained from sequencing data, 
but not all genomic experiments produce variant calls. Some assays instead identify genomic 
positions or regions of interest, such as transcription start sites mapped by CAGE-seq or 
regulatory intervals detected by ATAC-seq and ChIP-seq. For researchers working with these data types, 
it is often valuable to interpret them using the same kinds of genomic resources used in variant annotation. 
Although positions and regions do not contain allele information, and therefore cannot support every type of 
variant-based annotation, GAIn can still take these inputs and annotate them with many relevant resources using 
the ``annotate_tabular`` tool.

Position inputs require only two columns: chromosome and position. Save the following tab-delimited text in a 
file called ``positions.txt``.

.. csv-table::
    :header-rows: 1

    chrom,pos
    chr7,117587806
    chr7,115587806

Because position inputs do not include reference and alternate alleles, GAIn cannot infer the effect of a 
specific allelic change on a gene product. However, it can still determine whether a position falls within a gene and, 
if so, what broad part of the gene it overlaps. To do this, use ``simple_effect_annotator``, which classifies loci into 
broad categories such as intergenic and genic, and further subdivides genic loci into coding and several noncoding 
classes. Save the following text as ``annotation_pipeline2.yaml``.

.. code-block:: yaml

    - simple_effect_annotator:
        gene_models: hg38/gene_models/MANE/1.5


Then run the following command to annotate the positions:

.. code-block:: bash

    annotate_tabular positions.txt annotation_pipeline2.yaml

This produces ``positions_annotated.txt`` which contains:

.. csv-table::
    :header-rows: 1

    chrom,pos,worst_effect,worst_effect_genes
    chr7,117587806,coding,CFTR
    chr7,115587806,intergenic

This shows that the first position falls within a coding part of CFTR, whereas the second position is intergenic.

Position score resources can be applied directly to genomic positions, so ``position_score_annotator`` works on this 
input without modification. GAIn can also use allele score resources with position inputs. In that case, because 
the input specifies only the genomic position and not a particular allele, GAIn reports an aggregate value across 
possible allelic changes at that site.

To extend the example, add the following annotators to ``annotation_pipeline2.yaml``.

.. code-block:: yaml

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

    - allele_score_annotator:
        resource_id: hg38/scores/CADD_v1.6
        attributes:
        - cadd_raw

Then run the command again:

.. code-block:: bash

    annotate_tabular positions.txt annotation_pipeline2.yaml

This produces ``positions_annotated.txt`` which contains: 

.. csv-table::
    :header-rows: 1

    chrom,pos,worst_effect,worst_effect_genes,phylop7way,cadd_raw
    chr7,117587806,coding,CFTR,0.917,3.98
    chr7,115587806,intergenic,,0.158,0.472

phyloP7way measures evolutionary conservation at a genomic position. In this example, 
the coding position has a higher conservation score than the intergenic position. CADD estimates the 
deleteriousness of allelic changes, and for position inputs GAIn reports an aggregate value for the possible 
alleles at that site. Here, the first position has a higher aggregate ``cadd_raw`` score than the second.

Region inputs require three columns: chromosome, beginning position, and end position. 
Save the following tab-delimited text in a file called ``regions.txt``.

.. csv-table::
    :header-rows: 1

    chrom,pos_beg,pos_end
    chr1,1,100000
    chr1,11796321,11800000

As with position inputs, region inputs do not include reference and alternate alleles, 
so GAIn cannot infer the effect of a specific allelic change on a gene product. However, many of the same 
genomic resource types can still be applied to region inputs. Region inputs can also be evaluated with 
``simple_effect_annotator``, which summarizes whether a region overlaps genic or intergenic sequence and 
reports broad functional categories when applicable. Position score resources can be used on region 
inputs by aggregating values across the positions spanned by each interval. Allele score resources can also be used, 
but in that case GAIn must aggregate both across the positions in the region and across the possible allelic 
changes at each position. 

To illustrate this, reuse ``annotation_pipeline2.yaml``, shown below as a reminder.

.. code-block:: yaml

    - simple_effect_annotator:
        gene_models: hg38/gene_models/MANE/1.4

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

    - allele_score_annotator:
        resource_id: hg38/scores/CADD_v1.6
        attributes:
        - cadd_raw

Then run the following command to annotate the regions:

.. code-block:: bash

    annotate_tabular regions.txt annotation_pipeline2.yaml

This produces ``regions_annotated.txt`` which contains:

.. csv-table::
    :header-rows: 1

    chrom,pos_beg,pos_end,worst_effect,worst_effect_genes,phylop7way,cadd_raw
    chr1,1,100000,coding,OR4F5,0.0599,0.43
    chr1,11796321,11800000,coding,MTHFR,0.0348,0.269

This output shows how GAIn summarizes the functional context of each region. Depending on the interval, 
a region may be classified as intergenic, coding, or another category, and overlapping genes are reported 
when applicable.








When the annotation input file is large, GAIn can run split the large 
annotation the annotation in parallel
by splitting the workload across multiple CPU cores to speed up processing.


Reannotation
------------

When iterating on an analysis, you often want to run a new annotation pipeline on a dataset that has 
already been annotated. If the new pipeline shares any steps with the old one (for example, the same effect 
annotator or the same score lookup), recomputing those attributes can be wasteful—especially for large annotation jobs.

GAIn supports reannotation, which allows it to reuse attributes that were already computed by a 
previous pipeline run, and only compute what is new. To see an example for reannotation, create the 
following annotation pipeline and save it as ``pipeline_A.yaml``:

.. code-block:: yaml

    - effect_annotator:
        gene_models: hg38/gene_models/MANE/1.3
        attributes:
        - genes
        - worst_effect

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

Run the pipeline on your input variants:

.. code-block:: bash

    annotate_tabular variants.txt pipeline_A.yaml -o variants_A.txt

This produces ``variants_A.txt``, which includes the requested attributes:

.. csv-table::
    :header-rows: 1

    chrom,pos,ref,alt,genes,worst_effect,phyloP7way
    chr14,21415880,G,A,CHD8,nonsense,0.917
    chr17,7674904,TCT,T,TP53,frame-shift,-0.12
    chr7,117587806,G,A,CFTR,missense,0.917

Now suppose you want to annotate the same variants with a modified pipeline saved as ``pipeline_B.yaml``. 
In this example, Pipeline B is the same as Pipeline A, but adds two additional position-score annotators.

.. code-block:: yaml

    - effect_annotator:
        gene_models: hg38/gene_models/MANE/1.3
        attributes:
        - genes
        - worst_effect

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

    - position_score_annotator:
        resource_id: hg38/scores/phyloP30way

    - position_score_annotator:
        resource_id: hg38/scores/phyloP100way


You could run Pipeline B directly on ``variants_A.txt``, but that would recompute genes, worst_effect, 
and phyloP7way even though they are already present. Instead, use ``--reannotate`` and pass the old pipeline 
that produced the existing annotations:

.. code-block:: bash

    annotate_tabular variants_A.txt pipeline_B.yaml --reannotate pipeline_A.yaml -o variants_B.txt


When you run the command above, ``variants_A.txt`` is used as the input table 
(the output produced by Pipeline A), and ``pipeline_B.yaml`` is the updated 
pipeline you want to apply. The key part is ``--reannotate pipeline_A.yaml``: 
it tells GAIn which pipeline originally generated the annotation columns already present 
in ``variants_A.txt``, so GAIn can recognize any overlapping work and reuse those precomputed 
attributes instead of recalculating them. The result is written to ``variants_B.txt``, which 
contains the attributes requested by Pipeline B, with any shared attributes carried forward 
from the earlier run.

.. csv-table::
    :header-rows: 1

    chrom,pos,ref,alt,genes,worst_effect,phyloP7way,phyloP30way,phyloP100way
    chr14,21415880,G,A,CHD8,nonsense,0.917,1.18,1.25
    chr17,7674904,TCT,T,TP53,frame-shift,-0.12,-0.076,-1.14
    chr7,117587806,G,A,CFTR,missense,0.917,1.18,8.82



Adding public GRRs
-------------------------

So far, the annotation examples have used resources from the main IossifovLab GRR. We also provide another public repository, `GRR-ENCODE <https://grr-encode.iossifovlab.com/>`_, which contains ENCODE-derived functional genomics tracks that can be used in annotation pipelines. GRR-ENCODE contains approximately 8,000 resources, including ATAC-seq, DNase-seq, histone ChIP-seq, and transcription factor ChIP-seq tracks. 

To use these resources, add GRR-ENCODE to the GRR definition file, ``~/.grr_definition.yaml``. The configuration below connects GAIn to both the main GRR and GRR-ENCODE:

.. code-block:: yaml

    id: "remote_GRRs"
    type: group
    cache_dir: "<path_to>/remote_grr_cache"
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
        resource_id: ATAC-seq_ENCSR814RGG

This makes ENCODE-derived regulatory tracks available through the same pipeline syntax used for other position score resources.



Adding local GRRs
-----------------

In addition to connecting GAIn to public GRRs, users can create local GRRs containing their own resources. These resources may come from downloaded public data, processed datasets, or experimental results generated in a specific project.

As a minimal example resource, download the experimental score file (:download:`experimental_scores.tsv <files/experimental_scores.tsv>`) which contains five scores measured at five genomic positions:suppose we have an experimental score measured at five genomic positions. Three of these positions correspond to the variants used in "`Quick Annotation Test <file:///Users/muratcokol/Desktop/gain/docs/build/html/gain_getting_started_cli.html#quick-annotation-test>`_".

.. csv-table::
    :header-rows: 1

    chrom,pos,experimental_scores
    chr14,21415880,0.82
    chr17,7674904,0.15
    chr7,117587806,0.94
    chr1,11800000,0.31
    chr3,50000000,0.67

To make this file available as a GAIn resource, place it in a folder together with a :download:`genomic_resource.yaml <files/genomic_resource.yaml>` file:

.. code-block:: text

    My_First_GRR/
    └── my_score/
        ├── experimental_scores.tsv
        └── genomic_resource.yaml

The ``genomic_resource.yaml`` file describes the resource to GAIn:

.. code-block:: yaml

    type: position_score

    table:
    filename: experimental_scores.tsv
    header_mode: file

    scores:
    - id: my_score
        type: float
        name: experimental_scores

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
      cache_dir: "<path_to>/remote_grr_cache"
      children:
      - id: "main-GRR"
        type: "url"
        url: "https://grr.iossifovlab.com"

      - id: "GRR-ENCODE"
        type: "url"
        url: "https://grr-encode.iossifovlab.com"
    - id: "My_First_GRR"
      type: "directory"
      directory: "<path>/My_First_GRR"




With this configuration, GAIn can use the local resource in annotation pipelines, as well as the public resources in the main GRR and GRR-ENCODE. For example, the following custom pipeline combines resources from all three repositories: a gene-effect annotator from the main GRR, a transcription factor ChIP-seq track from GRR-ENCODE, and the local experimental score from ``My_First_GRR``.

Download the example pipeline (:download:`multiple_grr_pipeline.yaml <files/multiple_grr_pipeline.yaml>`) which uses multiple GRRs: 

.. code-block:: yaml

    preamble:
      summary: Pipeline using public and local GRRs
      input_reference_genome: hg38/genomes/GRCh38-hg38

    annotators:
    - effect_annotator:
        gene_models: hg38/gene_models/MANE/1.5
        attributes:
        - worst_effect
        - gene_list

    - position_score_annotator:
        resource_id: ATAC-seq_ENCSR814RGG

    - position_score_annotator:
        resource_id: my_score


To annotate the original example input with this pipeline, run:

.. code-block:: bash

    annotate_tabular small_input.csv multiple_grr_pipeline.yaml -o small_input_multiple_grr_annotated.csv

The output contains the effect annotations, the ENCODE-derived position score, and the local experimental score. The local score column comes from ``experimental_scores.tsv``:

.. csv-table::
    :header-rows: 1

    chrom,pos,ref,alt,worst_effect,genes,ATAC-seq_ENCSR814RGG,my_score
    chr14,21415880,G,A,nonsense,CHD8,,0.82
    chr17,7674904,TCT,T,frame-shift,TP53,2.18,0.15
    chr7,117587806,G,A,missense,CFTR,,0.94