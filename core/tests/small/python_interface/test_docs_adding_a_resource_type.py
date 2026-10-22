"""Pin the offline claims of the "Adding new resource types" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/adding_a_resource_type.rst``. The
tests pin these claims:

- A resource implementation subclasses ``GenomicResourceImplementation``,
  whose constructor takes the ``GenomicResource``.
- The five abstract methods are ``get_info``, ``get_statistics_info``,
  ``calc_info_hash``, ``create_statistics_build_tasks`` and
  ``calc_statistics_hash``, and ``files`` is a property.
- ``create_statistics_build_tasks`` takes the keyword-only ``region_size``
  and ``grr``.
- The imports and the class of the page's example work.
- The entry-point group ``gain.genomic_resources.implementations``
  registers ``genome`` with ``ReferenceGenomeImplementation``.
- ``get_resource_implementation_builder`` returns ``None`` for an unknown
  type, and ``register_implementation`` registers a builder in the
  process.
- ``fragment_score`` and ``cnv_collection`` share
  ``FragmentScoreImplementation``.
"""
import inspect
import pathlib
from importlib.metadata import entry_points
from typing import Any

from gain.genomic_resources import (
    GenomicResource,
    get_resource_implementation_builder,
    register_implementation,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
)
from gain.genomic_resources.implementations.reference_genome_impl import (
    ReferenceGenomeImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    DEFAULT_STATISTICS_REGION_SIZE,
    GenomicResourceImplementation,
)
from gain.genomic_resources.testing import setup_directories
from gain.task_graph.graph import TaskDesc

# Unique to this module, because the registry is global to the process.
MY_RESOURCE_TYPE = "docs_python_interface_my_resource_type"


class MyResourceImplementation(GenomicResourceImplementation):
    """The page's example, with each method filled in."""

    # As the page writes it: the constructor takes the resource.
    def __init__(  # pylint: disable=useless-parent-delegation
        self, resource: GenomicResource,
    ):
        super().__init__(resource)

    @property
    def files(self) -> set[str]:
        return {self.config["filename"]}

    def get_info(self, **kwargs: Any) -> str:
        return "info"

    def get_statistics_info(self, **kwargs: Any) -> str:
        return "statistics"

    def calc_info_hash(self) -> bytes:
        return b"info"

    def calc_statistics_hash(self) -> bytes:
        return b"statistics"

    def create_statistics_build_tasks(
        self, *,
        region_size: int = DEFAULT_STATISTICS_REGION_SIZE,
        grr: GenomicResourceRepo | None = None,
    ) -> list[TaskDesc]:
        return []


def _my_resource(root: pathlib.Path) -> GenomicResource:
    setup_directories(root / "my" / "resource" / "id", {
        "genomic_resource.yaml": (
            f"type: {MY_RESOURCE_TYPE}\nfilename: data.txt\n"),
        "data.txt": "content",
    })
    repo = build_genomic_resource_repository({
        "id": "mine", "type": "directory", "directory": str(root),
    })
    return repo.get_resource("my/resource/id")


def test_five_methods_are_abstract() -> None:
    """A subclass implements exactly these five methods."""
    assert GenomicResourceImplementation.__abstractmethods__ == {
        "get_info", "get_statistics_info", "calc_info_hash",
        "create_statistics_build_tasks", "calc_statistics_hash",
    }


def test_files_is_a_property() -> None:
    """``files`` names the files that the implementation uses."""
    assert isinstance(
        inspect.getattr_static(GenomicResourceImplementation, "files"),
        property)


def test_statistics_tasks_take_two_keyword_only_arguments() -> None:
    """``region_size`` and ``grr`` are keyword-only, with nothing else."""
    parameters = inspect.signature(
        GenomicResourceImplementation.create_statistics_build_tasks,
    ).parameters

    assert [p for p in parameters if p != "self"] == ["region_size", "grr"]
    assert all(
        parameters[p].kind is inspect.Parameter.KEYWORD_ONLY
        for p in ("region_size", "grr"))


def test_example_implementation_is_built_from_a_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The constructor takes the resource, and ``files`` reads its config."""
    resource = _my_resource(tmp_path)

    impl = MyResourceImplementation(resource)

    assert impl.resource is resource
    assert impl.files == {"data.txt"}
    assert not impl.create_statistics_build_tasks(
        region_size=1000, grr=None)


def test_genome_is_registered_by_an_entry_point() -> None:
    """``core/pyproject.toml`` registers the built-in ``genome`` type."""
    registered = {
        ep.name: ep.value
        for ep in entry_points(group="gain.genomic_resources.implementations")
    }

    assert registered["genome"] == (
        "gain.genomic_resources.implementations.reference_genome_impl"
        ":ReferenceGenomeImplementation")
    assert get_resource_implementation_builder("genome") is \
        ReferenceGenomeImplementation


def test_unknown_type_has_no_builder() -> None:
    """The lookup returns ``None`` for a type that nothing registers."""
    assert get_resource_implementation_builder(
        "docs_python_interface_no_such_type") is None


def test_register_implementation_in_the_process(
    tmp_path: pathlib.Path,
) -> None:
    """The page's example registers a class and builds with it."""
    register_implementation(MY_RESOURCE_TYPE, MyResourceImplementation)

    builder = get_resource_implementation_builder(MY_RESOURCE_TYPE)
    assert builder is MyResourceImplementation
    impl = builder(_my_resource(tmp_path))

    assert isinstance(impl, MyResourceImplementation)


def test_two_type_names_share_the_fragment_implementation() -> None:
    """``cnv_collection`` is the deprecated name of ``fragment_score``."""
    assert get_resource_implementation_builder("fragment_score") is \
        FragmentScoreImplementation
    assert get_resource_implementation_builder("cnv_collection") is \
        FragmentScoreImplementation
