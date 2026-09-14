Annotators
==========

This chapter is the in-depth description of the *annotation* half of GAIn's
declared API: the pipeline you load from YAML, the annotators it drives, the
objects an annotator receives and the attributes it declares — and the seam
through which a package of your own becomes an annotator a pipeline can name.

Where the line falls
--------------------

Everything here is Python. The same pipeline has a user-facing side — the
YAML that lists its annotators, the parameters each one accepts, the
``annotate_tabular`` and ``annotate_vcf`` command lines that run it — and
that side is documented on :doc:`/annotation_infrastructure`, not here. The
two pages meet at one place: an annotator's *type*. On the YAML side it is
the key of a list entry (``- position_score_annotator: ...``); on this side
it is the name a package registers in the ``gain.annotation.annotators``
entry-point group, and :doc:`annotators/plugins` says how the one becomes
the other.

If you are new to the Python interface, :doc:`/python_interface` is the
getting-started guide. Its fourth section loads a pipeline and annotates one
allele; its fifth walks through writing, registering and running a small
plugin end to end. This chapter picks up where those stop: it describes each
object those examples touch, and the contract a plugin has to keep.

The shape of the API
--------------------

One builder function is the entry point. It takes the YAML text of a
pipeline and a repository, and returns a pipeline whose annotators have
already been resolved — by type, to a factory registered in the entry-point
group — and configured:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.annotation.annotation_factory import load_pipeline_from_yaml
    from gain.annotation.annotatable import VCFAllele

    grr = build_genomic_resource_repository()

    pipeline = load_pipeline_from_yaml("""
    - effect_annotator:
        gene_models: hg38/gene_models/MANE/1.5
    """, grr)

    with pipeline.open() as pipeline:
        result = pipeline.annotate(VCFAllele("chr1", 11796321, "G", "A"))

Three conventions run through everything below.

**The pipeline drives the lifecycle.** An annotator is built closed, opened
by the pipeline's :meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.open`
before the first annotation, and closed once at the end. An annotator may
assume it is open when asked to annotate; it never opens itself on demand.
As with the resource objects, ``with pipeline.open() as pipeline:`` is the
spelling that both opens and guarantees the close.

**Attributes are keyed by name, not by source.** An annotator *declares*
the attributes it can produce, keyed by *source* — the name the annotator
itself uses. A pipeline *configures* which of those to emit and under what
*name* — the column that ends up in the output. The two are the same string
unless the YAML says otherwise, but the contract is stated in names: what an
annotator answers is a mapping from attribute name to value.

**The context flows forward.** A pipeline annotates by running its
annotators in order over one growing dictionary, the *context*. Each
annotator reads what the annotators before it produced, and the pipeline
merges each annotator's answer back into the context before moving on. An
annotator that depends on an earlier attribute reads it from the context and
says so, which is what lets a reannotation know what to rerun.

.. toctree::
   :maxdepth: 2

   annotators/annotatables
   annotators/attributes
   annotators/writing_an_annotator
   annotators/pipelines
   annotators/plugins

Not covered here
----------------

How to *test* an annotator. GAIn's own test fixtures live in
``gain.genomic_resources.testing``, which is outside the declared API and is
not documented on this site. The annotators GAIn ships — the score,
effect, liftover and gene-set annotators, SpliceAI and VEP — are described
from the user side on :doc:`/annotation_infrastructure`; their Python
classes appear in the generated :doc:`module index <module_index>`.
