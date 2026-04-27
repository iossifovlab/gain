# GAIn: Genomic Annotation Infrastructure
                            
Genomic Annotation Infrastructure (GAIn) is an open-source platform
([https://github.com/iossifovlab/gpf/tree/master/gain_core](https://github.com/iossifovlab/gpf/tree/master/gain_core))
for building transparent, reproducible 
genomic annotation pipelines that enable users to annotate genetic positions,
regions, or variants using available genomic resources. GAIn's web interface
is a browser-based entry point to GAIn. It lets users explore annotation
pipelines, test single-allele queries, and run batch annotation jobs without
local installation.

[GAIn's documentation]([https://iossifovlab.com/gaindocs/index.html](https://iossifovlab.com/gaindocs/index.html))
contains a detailed description of GAIn's web interface, pipelines, resources,
and command-line usage, as well as getting-started guides to introduce new
users to the platform.

## What GAIn's web interface can do

GAIn's web interface currently supports two main workflows:
- Single allele annotation for quick interactive queries and pipeline testing.
- Annotation jobs for uploading tabular (e.g., CSV) of VCF files and running
  larger analyses on the server.

Users can start from a published pipeline or build their own pipeline in the 
web editor by combining annotators and resources.

## Connected repositories

GAIn's web interface connects to two public Genomic Resource Repositories
(GRRs) that provide genomic resources for annotation.

- GRR: (https://grr.iossifovlab.com/)
    
The main public GRR is a curated online collection of genomic resources for
GAIn workflows. It includes reference genomes, gene models, genomic scores,
CNV collections, gene properties, gene sets, and annotation pipelines.
These resources are organized in a consistent and documented format so they
can be browsed, understood, and used reproducibly in GAIn.

- GRR-ENCODE: (https://grr-encode.iossifovlab.com/)
    
GRR-ENCODE is a separate public repository containing curated ENCODE-derived
resources for GAIn workflows. It currently focuses on ENCODE position-score
tracks, including ATAC-seq, DNase-seq, Histone ChIP-seq, and 
TF ChIP-seq datasets. It is maintained separately from the main GRR so that
the large number of ENCODE tracks does not overwhelm the more heterogeneous
main repository.

## Accounts, history, and quotas

Signed-in users can access additional web features such as saved pipelines
and annotation history. GAIn's web interface applies quotas (more stringent
for guest users) for interactive use and job submission.


To ensure fair use and reliable performance, the GAIn web interface applies
different quotas for guest and registered users. 

These quotas include limits per job, per day, and per month, as well as disk
space for stored results, with no storage for guests and 2 GB for registered
users. Registered users can use this storage to keep and revisit their
annotation history.

Daily quotas reset at 12 AM EST, and monthly quotas reset at 12 AM EST on the
first day of each month.

Daily and monthly resets refill quotas to the usual limit, unless the user
already has a higher available balance.
Guests and registered users have different limits for annotatables, attributes,
jobs, single-annotatable queries, input file size, and storage, as shown in
the quota tab. 

If a job exceeds any available quota, it will be rejected with a message
showing the quota limits that were exceeded. 

For annotation needs larger than the registered-user limits, please contact us
at gain@iossifovlab.com. 

We may be able to provide additional quota for larger annotation projects.

### Guest user limits

|                | annotatables | attributes | jobs | single annotatables | input file size |
| -------------- | ------------ | ---------- | ---- | ------------------- | --------------- |
| Per job limits | 100K         | 1M         | X    | X                   | 100M            |
| Daily limits   | 1M           | 10M        | 100  | 1000                | X               |
| Monthly limits | 10M          | 100M       | 1000 | 10000               | X               |


### Registered user limits

|                | annotatables | attributes | jobs | single annotatables | input file size |
| -------------- | ------------ | ---------- | ---- | ------------------- | --------------- |
| Per job limits | 10K          | 100K       | X    | X                   | 10M             |
| Daily limits   | 100K         | 1M         | 10   | 100                 | X               |
| Monthly limits | 1M           | 10M        | 100  | 1000                | X               |