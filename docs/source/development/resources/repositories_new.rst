
Repositories and resources
==========================

.. note::

   Draft for review: this page is a revision of :doc:`repositories`. Both are
   published while the revision is discussed; one replaces the other.

A Genomic Resource Repository (GRR) organizes genomic resources in a directory hierarchy. Each resource is defined by a ``genomic_resource.yaml`` file and may have associated data files. As described in :doc:`/grr`, a repository can be stored locally or accessed remotely.

In Python, GAIn represents a repository with a :class:`~gain.genomic_resources.repository.GenomicResourceRepo` object and an individual resource with a :class:`~gain.genomic_resources.repository.GenomicResource` object. A repository object lets you find resources. A resource object provides access to its definition, metadata, and files.

A ``GenomicResource`` is not the same as an object that interprets a specific kind of genomic data. For example, it can identify a resource as a reference genome or a position score, but it does not itself retrieve DNA sequences or score values. Those operations belong to specialized Python objects, such as ``ReferenceGenome`` and score objects, which are introduced in the following chapters. This chapter covers the repository and generic resource objects used to locate and inspect the underlying resources.

Accessing a repository
----------------------

The usual way to access a GRR from Python is with :func:`~gain.genomic_resources.repository_factory.build_genomic_resource_repository`, available through ``gain.grr``:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()

With no arguments, this function uses the default GRR configuration for the user's environment. If no custom configuration is available, a standard GAIn installation uses the public IossifovLab GRR.

You can also provide a repository definition to access a particular GRR. For example, the following code accesses a local directory:

.. code-block:: python

    local = grr.build_genomic_resource_repository({
        "id": "local_grr",
        "type": "directory",
        "directory": "/data/my-grr",
    })

The dictionary uses the same structure as the ``.grr_definition.yaml`` file. The :doc:`Genomic resources and repositories </grr>` chapter explains the configuration fields and supported repository types, including ``cache_dir``. Caching can be configured for any repository type.

The ``build_genomic_resource_repository()`` function creates a Python object that provides access to a repository's resources. Multiple repositories can be combined into a group using :func:`~gain.genomic_resources.repository_factory.build_genomic_resource_group_repository`. Within a genomic context, the associated repository can be retrieved using :func:`~gain.genomic_resources.genomic_context.get_grr_from_context`.

Finding a resource
------------------

Resources are identified by path-like resource IDs. Once you have a repository object, you can retrieve a resource by its ID using :meth:`~gain.genomic_resources.repository.GenomicResourceRepo.get_resource`:

.. code-block:: python

    resource = repo.get_resource("hg38/scores/phastCons100way")
    print(resource.get_full_id(), resource.get_type())

Two methods are available for looking up a specific resource:

* :meth:`~gain.genomic_resources.repository.GenomicResourceRepo.get_resource` raises an exception if the resource cannot be found.
* :meth:`~gain.genomic_resources.repository.GenomicResourceRepo.find_resource` returns ``None`` if no resource matches.

Use ``get_resource()`` when a missing resource should stop the program, and ``find_resource()`` when a resource may legitimately be absent.

Both methods accept an optional ``version_constraint``, using the same syntax as version constraints in annotation configuration files. For example:

.. code-block:: python

    resource = repo.get_resource(
        "hg38/scores/phastCons100way", version_constraint=">=0"
    )

If no resource satisfies the version constraint, the lookup fails just as it would for an unknown ID: ``get_resource()`` raises, while ``find_resource()`` returns ``None``. Many published resources have version ``0``, so check the available version before requiring a higher one, such as ``>=1.0``.

If you do not know the resource ID, use :meth:`~gain.genomic_resources.repository.GenomicResourceRepo.search_resources`. It supports full-text search terms, resource types, and wildcard queries over resource IDs and labels. For example, to find position-score resources:

.. code-block:: python

    for resource in repo.search_resources(resource_type="position_score"):
        print(resource.get_id())

:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.get_all_resources` iterates over all resources without filtering. Both methods return generators, which produce resources as you iterate; for a large repository, this avoids loading the entire result set into a list at once.

Inspecting and reading a resource
---------------------------------

A ``GenomicResource`` provides information about a resource and access to its files without requiring you to know whether the GRR is stored in a local directory, an HTTP mirror, or an S3 bucket.

Its metadata can be inspected through methods such as :meth:`~gain.genomic_resources.repository.GenomicResource.get_config`, :meth:`~gain.genomic_resources.repository.GenomicResource.get_type`, :meth:`~gain.genomic_resources.repository.GenomicResource.get_labels`, and :meth:`~gain.genomic_resources.repository.GenomicResource.get_description`. These describe the resource; they do not interpret the genomic data in its files.

To read a file, specify its path within the resource. For example:

.. code-block:: python

    with resource.open_raw_file("statistics/histogram_phastCons100way.json") as infile:
        print(infile.read())

:meth:`~gain.genomic_resources.repository.GenomicResource.open_raw_file` provides general file access. Format-specific methods are also available: :meth:`~gain.genomic_resources.repository.GenomicResource.open_tabix_file`, :meth:`~gain.genomic_resources.repository.GenomicResource.open_vcf_file`, :meth:`~gain.genomic_resources.repository.GenomicResource.open_fasta_file`, and :meth:`~gain.genomic_resources.repository.GenomicResource.open_bigwig_file`. These return the appropriate ``pysam`` or ``pyBigWig`` objects and retrieve index files along with the data when the format requires them.

For routine analyses, you will often use a specialized resource object rather than read files directly. For example, the reference-genome and genomic-score chapters explain how to create objects that provide methods for retrieving sequences or scores. The generic resource object is useful when you need to inspect resource metadata or work directly with its files.

Closing and refreshing a repository
-----------------------------------

A repository object is ready to use as soon as it is built. Unlike many specialized resource objects, it does not require a separate ``open()`` call.

:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.close` releases resources held by the repository. After ``close()`` is called, the repository's ``definition`` attribute is ``None``.

:meth:`~gain.genomic_resources.repository.GenomicResourceRepo.invalidate` serves a different purpose: it clears the repository's cached state so that subsequent access reloads the resource list, metadata, and file contents. This can be useful if the underlying repository has changed.

Closing a repository does not automatically close specialized Python objects created from its resources. For example, a reference genome or score object may hold its own open file handles and has its own ``close()`` method. The following chapters explain the opening and closing requirements for each resource type.

The resource manifest
---------------------

Each resource can have a :class:`~gain.genomic_resources.repository.Manifest`, which records its files, sizes, and checksums. GAIn uses this information to detect changed files and to determine whether a cached or mirrored copy is out of date, without having to read the full contents of each file during that comparison.

Two methods provide access to a resource's manifest:

* :meth:`~gain.genomic_resources.repository.GenomicResource.get_loaded_manifest` returns an existing, loaded manifest, or ``None`` if there is none. It does not attempt to construct a manifest.
* :meth:`~gain.genomic_resources.repository.GenomicResource.get_manifest` returns the manifest, constructing one if necessary. Constructing a manifest requires reading every file and can therefore be expensive.

Building a missing manifest also requires a read-write protocol. If the repository is available only through a read-only mount, ``get_manifest()`` may fail when no stored manifest is available. Use ``get_loaded_manifest()`` when you only need to check an existing manifest without triggering that work.


API
---

The classes below are defined in the module
``gain.genomic_resources.repository``. ``GenomicResourceRepo`` and
``GenomicResource`` are also exported by ``gain.grr``, as
``grr.GenomicResourceRepo`` and ``grr.GenomicResource``.

.. currentmodule:: gain.genomic_resources.repository

.. autosummary::

   GenomicResourceRepo
   GenomicResourceRepo.close
   GenomicResourceRepo.definition
   GenomicResourceRepo.find_resource
   GenomicResourceRepo.get_all_resources
   GenomicResourceRepo.get_resource
   GenomicResourceRepo.invalidate
   GenomicResourceRepo.repo_id
   GenomicResourceRepo.search_resources
   GenomicResourceRepo.search_resources_by_child
   GenomicResource
   GenomicResource.file_exists
   GenomicResource.get_config
   GenomicResource.get_description
   GenomicResource.get_file_content
   GenomicResource.get_file_url
   GenomicResource.get_full_id
   GenomicResource.get_id
   GenomicResource.get_labels
   GenomicResource.get_loaded_manifest
   GenomicResource.get_manifest
   GenomicResource.get_meta
   GenomicResource.get_public_url
   GenomicResource.get_repo_public_url
   GenomicResource.get_repo_url
   GenomicResource.get_summary
   GenomicResource.get_type
   GenomicResource.get_url
   GenomicResource.get_version_str
   GenomicResource.open_bigwig_file
   GenomicResource.open_fasta_file
   GenomicResource.open_raw_file
   GenomicResource.open_tabix_file
   GenomicResource.open_vcf_file
   Manifest
   Manifest.from_file_content
   Manifest.get_files
   Manifest.names

.. autoclass:: GenomicResourceRepo
   :no-index:
   :members:

.. autoclass:: GenomicResource
   :no-index:
   :members:

.. autoclass:: Manifest
   :no-index:
   :members:

The repository builders below are defined in the module
``gain.genomic_resources.repository_factory``. They are also exported by
``gain.grr``, as ``grr.build_genomic_resource_repository``,
``grr.build_genomic_resource_group_repository``,
``grr.get_default_grr_definition`` and
``grr.get_default_grr_definition_path``.

.. currentmodule:: gain.genomic_resources.repository_factory

.. autosummary::

   build_genomic_resource_repository
   build_genomic_resource_group_repository
   get_default_grr_definition
   get_default_grr_definition_path

.. autofunction:: build_genomic_resource_repository
   :no-index:

.. autofunction:: build_genomic_resource_group_repository
   :no-index:

.. autofunction:: get_default_grr_definition
   :no-index:

.. autofunction:: get_default_grr_definition_path
   :no-index:

The genomic-context functions below are defined in the module
``gain.genomic_resources.genomic_context``. They are also exported by
``gain.grr``, as ``grr.get_genomic_context`` and
``grr.get_grr_from_context``.

.. currentmodule:: gain.genomic_resources.genomic_context

.. autosummary::

   get_genomic_context
   get_grr_from_context

.. autofunction:: get_genomic_context
   :no-index:

.. autofunction:: get_grr_from_context
   :no-index:
