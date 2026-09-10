Adding a resource type
======================

The resource types GAIn understands are not a fixed list. Each one is a
*resource implementation* — a class that knows how to describe a resource of
that type, compute its statistics, and render its HTML summary page — and
implementations are discovered through a Python entry-point group. A package
installed alongside GAIn can add a type without any change to GAIn itself.

This is one of the extension seams named on the
:doc:`../architecture_overview` map. It is the seam for a new *kind of
resource*; adding a new annotator is a different seam, with its own
entry-point group.

The interface
-------------

An implementation subclasses
:class:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation`,
whose constructor takes the
:class:`~gain.genomic_resources.repository.GenomicResource` it wraps. Five
members are abstract, and they divide into two jobs:

**Describing the resource.**
:meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.get_info`
and
:meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.get_statistics_info`
return the HTML for the resource's summary and statistics pages.
:meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.calc_info_hash`
returns a digest of everything ``get_info`` depends on, so a page is only
re-rendered when its inputs changed.

**Computing statistics.**
:meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.create_statistics_build_tasks`
returns the task-graph tasks that compute this resource's statistics, and
:meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.calc_statistics_hash`
digests the inputs those tasks read — it is what lets ``grr_manage`` skip a
resource whose statistics are already current.

The concrete
:attr:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.files`
property should report every file the implementation reads. A caching or
mirroring repository builds its fetch list from it, so a file left out is a
file that may be missing from a cached copy. (Publishing works off the
resource's manifest instead, so it is unaffected.)

Registering
-----------

The entry-point group is ``gain.genomic_resources.implementations``. Its keys
are resource *type* names — the ``type`` field in a resource's
``genomic_resource.yaml`` — and its values point at the builder for that
type. A builder is any callable taking a ``GenomicResource`` and returning an
implementation, so an implementation class whose constructor takes exactly
that is its own builder.

GAIn's own types are registered this way in ``core/pyproject.toml``. The
``genome`` type is the smallest complete example:

.. code-block:: toml

    [project.entry-points."gain.genomic_resources.implementations"]
    genome = "gain.genomic_resources.implementations.reference_genome_impl:ReferenceGenomeImplementation"

A third-party package declares its own type the same way, in its own
``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."gain.genomic_resources.implementations"]
    my_resource_type = "my_package.my_impl:MyResourceImplementation"

.. code-block:: python

    from gain.genomic_resources import GenomicResource
    from gain.genomic_resources.resource_implementation import (
        GenomicResourceImplementation,
    )


    class MyResourceImplementation(GenomicResourceImplementation):
        """Resource implementation for my_resource_type."""

        def __init__(self, resource: GenomicResource):
            super().__init__(resource)

        @property
        def files(self) -> set[str]:
            return {self.config["filename"]}

        def get_info(self, **kwargs):
            ...

        def get_statistics_info(self, **kwargs):
            ...

        def calc_info_hash(self) -> bytes:
            ...

        def calc_statistics_hash(self) -> bytes:
            ...

        def create_statistics_build_tasks(self, **kwargs):
            ...

Once the package is installed, ``grr_manage`` and the repository browser
handle resources of that type with no further configuration.

Looking a builder up
--------------------

:func:`~gain.genomic_resources.get_resource_implementation_builder` is the
lookup GAIn itself uses. It consults the in-process registry first and falls
back to the entry points, loading and caching whatever it finds; it returns
``None`` for a type nothing has registered.

:func:`~gain.genomic_resources.register_implementation` adds a builder to the
in-process registry directly, without an entry point. That is the right tool
for a test, or for a type defined in the same script that uses it; it is not
a substitute for the entry point in a package meant to be installed, because
it only takes effect once the registering module has been imported.

.. code-block:: python

    from gain.genomic_resources import (
        get_resource_implementation_builder,
        register_implementation,
    )

    register_implementation("my_resource_type", MyResourceImplementation)

    builder = get_resource_implementation_builder("my_resource_type")
    impl = builder(grr.get_resource("my/resource/id"))

A note on the shipped registrations: two keys may point at the same class.
``cnv_collection`` and ``fragment_score`` both map to
``FragmentScoreImplementation`` because the first is a deprecated spelling
kept registered for repositories outside our control — opening one warns.
Nothing prevents a third-party package from registering a type name GAIn
already uses; the entry-point group is flat, so prefer a name qualified by
your project.

API
---

.. currentmodule:: gain.genomic_resources.resource_implementation

.. autoclass:: GenomicResourceImplementation
   :members:

.. currentmodule:: gain.genomic_resources

.. autofunction:: register_implementation

.. autofunction:: get_resource_implementation_builder
