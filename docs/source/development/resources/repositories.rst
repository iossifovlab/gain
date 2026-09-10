Repositories and resources
==========================

A Genomic Resource Repository (GRR) is a tree of directories, each of which
may be a *resource*: a ``genomic_resource.yaml`` config plus the data files
it names. In Python that tree is a
:class:`~gain.genomic_resources.repository.GenomicResourceRepo`, and each
directory it serves is a
:class:`~gain.genomic_resources.repository.GenomicResource`.

A ``GenomicResource`` is deliberately *generic*: it can tell you the
resource's declared type through
:meth:`~gain.genomic_resources.repository.GenomicResource.get_type`, but it
has no behaviour specific to any of them — it reads config and files and
stops there. Turning one into a reference genome or a score is the job of the
typed builders described in the following pages, which is why every one of
them takes a repository and an id rather than a bare path.

Opening a repository
--------------------

:func:`~gain.genomic_resources.repository_factory.build_genomic_resource_repository`
called with no arguments builds the repository named by the user's
environment — in a standard installation, the public IossifovLab GRR. Passing
a definition dictionary instead builds exactly the repository it describes:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository

    # the environment's default repository
    grr = build_genomic_resource_repository()

    # one specific directory, as a repository of its own
    local = build_genomic_resource_repository({
        "id": "local_grr",
        "type": "directory",
        "directory": "/data/my-grr",
    })

The definition dictionary is the same structure the ``.grr_definition.yaml``
file holds. :doc:`/grr` documents its keys, the repository types available,
and the ``cache_dir`` key — caching is a key any type can carry, not a type
of its own.

Finding a resource
------------------

Two lookups differ only in what they do when nothing matches:
:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.get_resource`
raises, and
:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.find_resource`
returns ``None``. Use ``get_resource`` when the id is a constant in your
program and its absence is a bug; use ``find_resource`` when the id came from
outside and absence is an ordinary outcome.

Both accept a ``version_constraint`` in the same spelling the annotation
configuration uses, so a script can pin the resource it was written against:

.. code-block:: python

    res = grr.get_resource("hg38/scores/phastCons100way", version_constraint=">=0")
    print(res.get_full_id(), res.get_type())

A constraint nothing satisfies is a lookup failure, not a silently older
resource — ``get_resource`` raises and ``find_resource`` returns ``None``,
the same way they treat an unknown id. Check the resource's actual version
before pinning: many published resources are still at ``0``, so a plausible
looking ``>=1.0`` will simply fail to resolve.

To go the other way — from a property to the resources that have it — use
:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.search_resources`,
which takes a full-text term, a resource type, or a wildcard query over
resource ids and labels, and yields matches lazily:

.. code-block:: python

    for res in grr.search_resources(resource_type="position_score"):
        print(res.get_id())

:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.get_all_resources`
is the unfiltered form. Both are generators over a potentially large
repository, so prefer to consume them lazily rather than materialising a
list.

Reading a resource
------------------

A resource exposes its metadata through small accessors —
:meth:`~gain.genomic_resources.repository.GenomicResource.get_config`,
:meth:`~gain.genomic_resources.repository.GenomicResource.get_type`,
:meth:`~gain.genomic_resources.repository.GenomicResource.get_labels`,
:meth:`~gain.genomic_resources.repository.GenomicResource.get_description` —
and its files through a family of ``open_*`` methods that hide where the
resource actually lives. The same call works whether the repository is a
local directory, an HTTP mirror or an S3 bucket:

.. code-block:: python

    with res.open_raw_file("statistics/histogram_phastCons100way.json") as infile:
        print(infile.read())

:meth:`~gain.genomic_resources.repository.GenomicResource.open_raw_file` is
the general one; the typed openers
(:meth:`~gain.genomic_resources.repository.GenomicResource.open_tabix_file`,
:meth:`~gain.genomic_resources.repository.GenomicResource.open_vcf_file`,
:meth:`~gain.genomic_resources.repository.GenomicResource.open_fasta_file`,
:meth:`~gain.genomic_resources.repository.GenomicResource.open_bigwig_file`)
return the corresponding ``pysam`` or ``pyBigWig`` object, fetching the index
alongside the data file where the format needs one.

The manifest
------------

Every resource carries a :class:`~gain.genomic_resources.repository.Manifest`
— the list of its files with their sizes and checksums. It is what lets a
repository detect that a file has changed without reading it, and what a
cached or mirrored repository compares against when it decides whether its
copy is stale.

:meth:`~gain.genomic_resources.repository.GenomicResource.get_manifest`
returns it, *building* one if the resource has none. The build reads every
file, so where it is possible at all it is expensive — and it needs a
read-write protocol, so on a read-only mount a resource with no stored
manifest fails here rather than building one.
:meth:`~gain.genomic_resources.repository.GenomicResource.get_loaded_manifest`
is the cheap counterpart — it returns the stored manifest or ``None``, and
never builds one.

API
---

.. currentmodule:: gain.genomic_resources.repository_factory

.. autofunction:: build_genomic_resource_repository

.. currentmodule:: gain.genomic_resources.repository

.. autoclass:: GenomicResourceRepo
   :members:

.. autoclass:: GenomicResource
   :members:

.. autoclass:: Manifest
   :members:
