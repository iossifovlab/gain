
Adding new resource types
=========================

GAIn supports different types of genomic resources, such as reference genomes, genomic scores, and gene set collections. New resource types can also be added without modifying GAIn itself. This allows external Python packages to extend the kinds of data that a Genomic Resource Repository (GRR) can manage.

Each resource type is handled by a *resource implementation*, a Python class that defines how GAIn works with resources of that type. In particular, the implementation describes the resource, generates its HTML summary pages, and defines how its statistics are computed.

New resource types are made available to GAIn through Python entry points. This is different from adding a new annotator, which uses a separate extension mechanism described in :doc:`../annotators/plugins`.


Implementing a resource type
----------------------------

A resource implementation subclasses :class:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation`. Its constructor receives the :class:`~gain.genomic_resources.repository.GenomicResource` object representing the resource.

The subclass must implement five abstract methods, covering two aspects of resource management.

1. **Describing the resource.** These methods provide the information displayed in the repository browser and determine when it needs to be updated:

   * :meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.get_info` generates the HTML describing the resource.
   * :meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.get_statistics_info` generates the HTML presenting its statistics.
   * :meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.calc_info_hash` calculates a hash of the inputs used to generate the resource information. GAIn uses this hash to determine whether the information needs to be regenerated.

2. **Computing statistics.** These methods define how resource statistics are calculated and determine when they need to be updated:

   * :meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.create_statistics_build_tasks` defines the tasks needed to calculate statistics for the resource.
   * :meth:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.calc_statistics_hash` calculates a hash of the inputs used to compute statistics. This allows ``grr_manage`` to skip recalculation when the statistics are already up to date.

The ``create_statistics_build_tasks()`` method must accept two keyword-only arguments, ``region_size`` and ``grr``, even if the implementation does not use them. These arguments are passed consistently by ``grr_manage`` to different resource implementations. The method should not accept arbitrary additional keyword arguments, so unexpected arguments produce an error rather than being silently ignored.

A resource implementation must also provide the :attr:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation.files` property, which identifies the files it uses. GAIn relies on this information when retrieving files for caching or mirroring repositories. Omitting a file may cause it to be unavailable in a cached copy. Resource publishing uses the resource manifest instead.



Registering a resource type
---------------------------

GAIn discovers resource implementations through the Python entry-point group ``gain.genomic_resources.implementations``. Each registration associates a resource type name with a builder that creates its implementation.

The resource type name corresponds to the ``type:`` field in a resource's ``genomic_resource.yaml`` file. A builder is a callable that takes a ``GenomicResource`` object and returns the corresponding resource implementation. Since an implementation class can be instantiated directly from a resource, the class itself can serve as its builder.

GAIn registers its built-in resource types in ``core/pyproject.toml``. For example, the ``genome`` resource type is registered as follows:

.. code-block:: toml

    [project.entry-points."gain.genomic_resources.implementations"]
    genome = "gain.genomic_resources.implementations.reference_genome_impl:ReferenceGenomeImplementation"

An external package can register a new resource type in its own ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."gain.genomic_resources.implementations"]
    my_resource_type = "my_package.my_impl:MyResourceImplementation"

This registration tells GAIn to use the ``MyResourceImplementation`` class from ``my_package.my_impl`` for resources whose type is ``my_resource_type``.

The following example illustrates the structure of a resource implementation:

.. code-block:: python

    from gain.genomic_resources import GenomicResource
    from gain.genomic_resources.repository import GenomicResourceRepo
    from gain.genomic_resources.resource_implementation import (
        DEFAULT_STATISTICS_REGION_SIZE,
        GenomicResourceImplementation,
    )
    from gain.task_graph.graph import TaskDesc


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

        def create_statistics_build_tasks(
            self, *,
            region_size: int = DEFAULT_STATISTICS_REGION_SIZE,
            grr: GenomicResourceRepo | None = None,
        ) -> list[TaskDesc]:
            ...

The example shows the required interface rather than a complete implementation. The methods must be filled in with the logic appropriate for the new resource type.

Once the package is installed, GAIn can discover the registered implementation. Resources using the new type can then be handled by ``grr_manage`` and the repository browser without additional configuration.

Looking up and registering builders
-----------------------------------

GAIn also provides functions for accessing and registering resource implementations directly in Python.

:func:`~gain.genomic_resources.get_resource_implementation_builder` looks up the builder associated with a resource type. It first checks the in-process registry and then searches the installed entry points. Discovered builders are cached for subsequent use. The function returns ``None`` if the resource type is not registered.

:func:`~gain.genomic_resources.register_implementation` registers a builder directly in the current Python process, without requiring a package entry point. This is useful for testing or for implementations defined and used within the same script.

For example:

.. code-block:: python

    from gain.genomic_resources import (
        get_resource_implementation_builder,
        register_implementation,
    )

    register_implementation("my_resource_type", MyResourceImplementation)

    builder = get_resource_implementation_builder("my_resource_type")
    impl = builder(repo.get_resource("my/resource/id"))

Here, ``register_implementation()`` associates the resource type name with its implementation class. The lookup function retrieves that class as a builder, which is then used to create an implementation object for a resource.

Direct registration applies only to the current Python process and requires the registration code to have been executed. For resource types distributed as installable Python packages, entry-point registration is therefore preferred.

Multiple resource type names can also refer to the same implementation. For example, GAIn registers both ``fragment_score`` and the deprecated ``cnv_collection`` type with ``FragmentScoreImplementation``. The deprecated name remains supported for compatibility with existing repositories, although using it produces a warning.

Because resource type names share a common registry, external packages should choose distinctive names to avoid conflicts with built-in or other third-party resource types.


API
---

The classes below are defined in the module
``gain.genomic_resources.resource_implementation``. The functions are defined
in the package ``gain.genomic_resources``. ``gain.grr`` exports none of them.

.. currentmodule:: gain.genomic_resources.resource_implementation

.. autosummary::

   GenomicResourceImplementation
   GenomicResourceImplementation.calc_info_hash
   GenomicResourceImplementation.calc_statistics_hash
   GenomicResourceImplementation.create_statistics_build_tasks
   GenomicResourceImplementation.files
   GenomicResourceImplementation.get_config
   GenomicResourceImplementation.get_info
   GenomicResourceImplementation.get_statistics
   GenomicResourceImplementation.get_statistics_info
   GenomicResourceImplementation.reload_statistics
   GenomicResourceImplementation.resource_id

.. autoclass:: GenomicResourceImplementation
   :no-index:
   :members:

.. currentmodule:: gain.genomic_resources

.. autosummary::

   register_implementation
   get_resource_implementation_builder

.. autofunction:: register_implementation
   :no-index:

.. autofunction:: get_resource_implementation_builder
   :no-index:
