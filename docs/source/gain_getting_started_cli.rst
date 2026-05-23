
Getting started on CLI
======================

Prerequisites
-------------

This guide assumes that you are working on a recent Linux or Mac OS X machine.

We distribute GAIn as a Conda package and you can install it using 
``conda install``. For a faster installation, we recommend using libmamba 
solver with conda or directly using the mamba alternative. If you do not have 
a distribution of Conda or Mamba package manager or if you don't understand 
this description, we suggest following the instruction to install mamba 
through Miniforge distribution available 
at `https://github.com/conda-forge/miniforge <https://github.com/conda-forge/miniforge>`_.




Installation
------------

We assume that you have a working ``mamba``. If you don't have ``mamba`` but a working ``conda``, replace ``mamba`` with ``conda`` in the commands bellow. See above if you have no working conda or mamba.

Start by creating an empty Conda environment named ``gain_cli``:

.. code-block:: bash

    mamba create -n gain_cli

To use this environment, you need to activate it using the following command:

.. code-block:: bash

    mamba activate gain_cli

Afterwards, install the ``gain_core`` conda package:

.. code-block:: bash

    mamba install -c conda-forge -c bioconda -c iossifovlab gain-core

This command is going to install GAIn and all of its dependencies.


Browse available resources
-----------------------------------------


GAIn installs with access to the default IossifovLab GRR. 
You can confirm which GRRs are available to you and browse the 
resources hosted on them by running the command below:

.. code-block:: bash

    grr_browse

This will show that you have access to the IossifovLab GRR server and lists all the 
resources available to you on that server.

.. code-block:: bash

    No GRR definition found, using the DEFAULT_DEFINITION
    id: default
    type: http
    url: https://grr.iossifovlab.com

    gene_score           0      139 11.19 MB     GRR gene_properties/gene_scores/GTEx_V11_RNAexpression
    gene_score           0        6 7.8 MB       GRR gene_properties/gene_scores/Iossifov_Wigler_PNAS_2015
    gene_score           0       11 576.07 KB    GRR gene_properties/gene_scores/LGD
    gene_score           0        9 13.18 MB     GRR gene_properties/gene_scores/LOEUF
    gene_score           0       13 505.9 KB     GRR gene_properties/gene_scores/RVIS
    ...


Simple annotation pipeline
--------------------------

Using the command-line tools, users can annotate large sets of variants, positions, or regions 
on a standard personal computer. 
As a simple example, we will annotate the following three variants.

.. csv-table::
    :header-rows: 1

    chrom,pos,ref,alt
    chr14,21415880,G,A
    chr17,7674904,TCT,T
    chr7,117587806,G,A


The input consists of chromosomal positions, the reference allele and the alternate allele. 
The user should create a file named ``variants.txt`` with this content in a working folder of their choice. The columns should be tab-separated.

In order to tell GAIn which annotation attributes we are interested in, we use simple YAML files called annotation pipelines. 
Below we will introduce a simple annotation pipeline which we will use on ``variants.txt`` in the next section.

The preamble section is optional and can be used to define the genome the variants are in and to store additional 
metadata about the pipeline.

.. code-block:: yaml

    preamble:
      summary: Simple pipeline 
      input_reference_genome: hg38/genomes/GRCh38-hg38

After the preamble, various annotators are listed. Annotation runs from top to bottom. 
Attributes produced by earlier annotators can be used by later annotators. The following 
lines tell GAIn to use version 1.3 of the MANE gene model to find which genes are affected by 
each variant and what the worst predicted effect is.

.. code-block:: yaml

    annotators:

    - effect_annotator:
        gene_models: hg38/gene_models/MANE/1.3
        attributes:
        - worst_effect
        - gene_list

Next is a position score annotator. phyloP7way provides a score for conservation at this genomic coordinate, 
computed from a multiple alignment of seven species.

.. code-block:: yaml

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

Next, we add allele scores from ClinVar: CLNSIG, which encodes the clinical significance of a 
variant (e.g. benign, pathogenic), and CLNDN, the associated disease name. Allele score annotators are 
preceded by a ``normalize_allele_annotator``, which expresses the allele in canonical form.

.. code-block:: yaml

    - normalize_allele_annotator

    - allele_score_annotator: 
        resource_id: hg38/scores/ClinVar_20240730 
        input_annotatable: normalized_allele
        attributes:
        - CLNSIG
        - CLNDN

Copy all of the pipeline lines above into a new text file called ``annotation_pipeline.yaml``.

Annotating tabular input
---------------------------------

GAIn performs annotations by combining three ingredients: the genomic resources (in one or more GRRs), 
the annotatables to annotate, and a YAML annotation pipeline describing which attributes to compute. 
Now that all three are in place, we can execute the following command to apply the pipeline to our tabular variant file:

.. code-block:: bash

    annotate_columns variants.txt annotation_pipeline.yaml

This command tells GAIn to annotate the tabular file called ``variants.txt`` using ``annotation_pipeline.yaml``. 
Running this command produces an output file named ``variants_annotated.txt``, shown below.

.. csv-table::
    :header-rows: 1

    chrom,pos,ref,alt,worst_effect,genes,phylop7way,CLNSIG,CLNDN
    chr14,21415880,G,A,nonsense,CHD8,0.917,Pathogenic/Likely_pathogenic,Intellectual_developmental_disorder_with_autism_and_macrocephaly|not_provided
    chr17,7674904,TCT,T,frame-shift,TP53,-0.12,Pathogenic,Li-Fraumeni_syndrome_1|Hereditary_cancer-predisposing_syndrome|Li-Fraumeni_syndrome|Ovarian_neoplasm|not_provided|TP53-related_disorder
    chr7,117587806,G,A,missense,CFTR,0.917,Pathogenic,Hereditary_pancreatitis|CFTR-related_disorder|Cystic_fibrosis|Congenital_bilateral_aplasia_of_vas_deferens_from_CFTR_mutation|ivacaftor_response_-_Efficacy|Bronchiectasis_with_or_without_elevated_sweat_chloride_1|not_provided

Annotating VCF input
-----------------------------

GAIn can also annotate variants stored in VCF files. The command is similar to ``annotate_columns``, but the input and 
output files are in VCF format. Here, the same variants are stored in a file called ``variants.vcf``. 

.. code-block:: yaml

    ##fileformat=VCFv4.1
    ##reference=GRCh38-hg38
    ##contig=<ID=chr7>
    ##contig=<ID=chr14>
    ##contig=<ID=chr17>
    #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    chr14	21415880	.	G	A	.	.	.
    chr17	7674904	.	TCT	T	.	.	.
    chr7	117587806	.	G	A	.	.	.


To annotate them, run:

.. code-block:: bash

    annotate_vcf variants.vcf annotation_pipeline.yaml

This command produces an output file named ``variants_annotated.vcf``, which contains the same variants with 
additional annotation fields in the ``INFO`` column.

.. code-block:: yaml

    ##fileformat=VCFv4.1
    ##FILTER=<ID=PASS,Description="All filters passed">
    ##reference=GRCh38-hg38
    ##contig=<ID=chr7>
    ##contig=<ID=chr14>
    ##contig=<ID=chr17>
    ##pipeline_annotation_tool=GPF variant annotation.
    ##INFO=<ID=worst_effect,Number=A,Type=String,Description="Worst effect accross all transcripts.">
    ##INFO=<ID=genes,Number=A,Type=String,Description="Comma separated list of all affected genes.">
    ##INFO=<ID=phylop7way,Number=A,Type=String,Description="The score is a number that reflects the conservation at a position.">
    ##INFO=<ID=CLNSIG,Number=A,Type=String,Description="Aggregate germline classification for this single variant; multiple values are separated by a vertical bar">
    ##INFO=<ID=CLNDN,Number=A,Type=String,Description="ClinVar's preferred disease name for the concept specified by disease identifiers in CLNDISDB">
    #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    chr14	21415880	.	G	A	.	.	worst_effect=nonsense;genes=CHD8;phylop7way=0.917;CLNSIG=Pathogenic/Likely_pathogenic;CLNDN=Intellectual_developmental_disorder_with_autism_and_macrocephaly|not_provided
    chr17	7674904	.	TCT	T	.	.	worst_effect=frame-shift;genes=TP53;phylop7way=-0.12;CLNSIG=Pathogenic;CLNDN=Li-Fraumeni_syndrome_1|Hereditary_cancer-predisposing_syndrome|Li-Fraumeni_syndrome|Ovarian_neoplasm|not_provided|TP53-related_disorder
    chr7	117587806	.	G	A	.	.	worst_effect=missense;genes=CFTR;phylop7way=0.917;CLNSIG=Pathogenic;CLNDN=Hereditary_pancreatitis|CFTR-related_disorder|Cystic_fibrosis|Congenital_bilateral_aplasia_of_vas_deferens_from_CFTR_mutation|ivacaftor_response_-_Efficacy|Bronchiectasis_with_or_without_elevated_sweat_chloride_1|not_provided


Annotating positions and regions
-----------------------------------

GAIn is well suited for annotating genetic variants obtained from sequencing data, 
but not all genomic experiments produce variant calls. Some assays instead identify genomic 
positions or regions of interest, such as transcription start sites mapped by CAGE-seq or 
regulatory intervals detected by ATAC-seq and ChIP-seq. For researchers working with these data types, 
it is often valuable to interpret them using the same kinds of genomic resources used in variant annotation. 
Although positions and regions do not contain allele information, and therefore cannot support every type of 
variant-based annotation, GAIn can still take these inputs and annotate them with many relevant resources using 
the ``annotate_columns`` tool.

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
        gene_models: hg38/gene_models/MANE/1.4


Then run the following command to annotate the positions:

.. code-block:: bash

    annotate_columns positions.txt annotation_pipeline2.yaml

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

    annotate_columns positions.txt annotation_pipeline2.yaml

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

    annotate_columns regions.txt annotation_pipeline2.yaml

This produces ``regions_annotated.txt`` which contains:

.. csv-table::
    :header-rows: 1

    chrom,pos_beg,pos_end,worst_effect,worst_effect_genes,phylop7way,cadd_raw
    chr1,1,100000,coding,OR4F5,0.0599,0.43
    chr1,11796321,11800000,coding,MTHFR,0.0348,0.269

This output shows how GAIn summarizes the functional context of each region. Depending on the interval, 
a region may be classified as intergenic, coding, or another category, and overlapping genes are reported 
when applicable.


Parallelization []
---------------

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

    annotate_columns variants.txt pipeline_A.yaml -o variants_A.txt

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

    annotate_columns variants_A.txt pipeline_B.yaml --reannotate pipeline_A.yaml -o variants_B.txt


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

