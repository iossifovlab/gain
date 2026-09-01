"""Classes for handling of gene sets and gene set collections."""
from __future__ import annotations

import abc
import gzip
import json
import os
from threading import Lock
from typing import IO, Annotated, Any, Literal, cast

from pydantic import BaseModel, Field

from gain import logging
from gain.gene_sets.gene_term import (
    read_ewa_set_file,
    read_gmt_file,
    read_mapping_file,
)
from gain.genomic_resources.fsspec_protocol import build_local_resource
from gain.genomic_resources.histogram import (
    Histogram,
    load_histogram,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

logger = logging.getLogger(__name__)


class MetaSchema(BaseModel):
    description: str | None = None
    labels: dict[str, Any] | None = None


class BaseResourceSchema(BaseModel):
    type: str | None = None
    meta: MetaSchema | None = None


class ViewRangeSchema(BaseModel):
    min: float | None = None
    max: float | None = None


# pylint: disable=missing-class-docstring
class NumericHistogramSchema(BaseModel):
    type: Literal["number"]
    plot_function: str | None = None
    number_of_bins: int | None = None
    view_range: ViewRangeSchema | None = None
    x_log_scale: bool | None = None
    y_log_scale: bool | None = None
    x_min_log: float | None = None
    value_order: list[str | int] | None = None
    displayed_values_count: int | None = None


# pylint: disable=missing-class-docstring
class CategoricalHistogramSchema(BaseModel):
    type: Literal["categorical"]
    displayed_values_count: int | None = None
    displayed_values_percent: float | None = None
    value_order: list[str | int] | None = None
    y_log_scale: bool | None = None
    label_rotation: int | None = None
    plot_function: str | None = None
    enforce_type: bool | None = None
    natural_order: bool | None = None


HistogramConfig = Annotated[
    NumericHistogramSchema | CategoricalHistogramSchema,
    Field(discriminator="type"),
]


# pylint: disable=missing-class-docstring
class GeneSetResourceSchema(BaseModel):
    resource_id: str = Field(alias="id")
    filename: str | None = None
    directory: str | None = None
    resource_format: str | None = Field(alias="format")
    web_label: str | None = None
    web_format_str: str | None = None
    histograms: dict[
        Literal["genes_per_gene_set", "gene_sets_per_gene"],
        HistogramConfig,
    ] | None = None


class GeneSet:
    """Class representing a set of genes."""

    # pylint: disable=too-few-public-methods
    name: str
    desc: str
    count: int
    syms: list[str]

    def __init__(self, name: str, desc: str, syms: list[str]) -> None:
        self.name = name
        self.desc = desc
        self.count = len(syms)
        self.syms = syms

    def __getitem__(self, name: str) -> Any:
        # This is done so that GeneSet instances and
        # denovo gene set dictionaries can be accessed in a uniform way
        if name == "name":
            return self.name
        if name == "desc":
            return self.desc
        if name == "count":
            return self.count
        if name == "syms":
            return self.syms

        raise KeyError


class BaseGeneSetCollection(abc.ABC):
    """Base class for gene set collections."""
    def __init__(self, collection_id: str) -> None:
        self.collection_id = collection_id
        self.web_label: str = ""
        self.web_format_str: str = ""
        self.gene_sets: dict[str, GeneSet] = {}

    @abc.abstractmethod
    def load(self) -> BaseGeneSetCollection:
        """Load the gene sets from the resource."""

    @abc.abstractmethod
    def get_gene_set(self, gene_set_id: str) -> GeneSet | None:
        """Return the gene set if found; returns None if not found."""

    @abc.abstractmethod
    def get_all_gene_sets(self) -> list[GeneSet]:
        """Return list of all gene sets in the collection."""


class GeneSetCollection(
    BaseGeneSetCollection,
):
    """Class representing a collection of gene sets in a resource."""

    def __init__(self, resource: GenomicResource) -> None:
        config = resource.get_config()
        self.resource = resource

        self.config = GeneSetResourceSchema.model_validate(config)
        super().__init__(self.config.resource_id)

        assert self.collection_id != "denovo"
        if resource.get_type() not in {"gene_set_collection", "gene_set"}:
            raise ValueError("Invalid resource type for gene set collection")
        if resource.get_type() == "gene_set":
            logger.warning(
                "'gene_set' resource type is deprecated; "
                "use 'gene_set_collection' instead")

        self.web_label = self.config.web_label or ""
        self.web_format_str = self.config.web_format_str or ""
        logger.debug("loading %s: %s", self.collection_id, config)
        self.gene_sets: dict[str, GeneSet] = {}

        assert self.collection_id, self.gene_sets

    @property
    def files(self) -> set[str]:
        """Return a list of resource files the implementation utilises."""
        res = set()
        collection_format = self.config.resource_format

        if collection_format == "map":
            filename = self.config.filename
            assert filename is not None
            res.add(filename)
            names_filename = filename.removesuffix(".gz")[:-4] + "names.txt"
            # The same test ``load_gene_sets`` makes. Manifest membership
            # would answer it too, but reading a manifest the resource has
            # not got scans and writes state across the whole root (#911).
            if self.resource.file_exists(names_filename):
                res.add(names_filename)
        elif collection_format == "gmt":
            filename = self.config.filename
            assert filename is not None
            res.add(filename)
        elif collection_format == "directory":
            directory = self.config.directory
            assert directory is not None
            if directory == ".":
                directory = ""
            for filepath, _ in self.resource.get_manifest().get_files():
                if filepath.startswith(directory) and \
                        filepath.endswith(".txt"):
                    res.add(filepath)
        else:
            raise ValueError("Invalid collection format type")

        return res

    def is_loaded(self) -> bool:
        """Check if the gene sets have been loaded."""
        return bool(self.gene_sets)

    def load(self) -> GeneSetCollection:
        """Load the gene sets from the resource."""
        if self.is_loaded():
            logger.debug(
                "gene sets already loaded from %s", self.collection_id)
            return self
        self.gene_sets = self.load_gene_sets()
        logger.debug(
            "loaded %d gene sets from %s",
            len(self.gene_sets), self.collection_id,
        )
        return self

    def load_gene_sets(self) -> dict[str, GeneSet]:
        """Build a gene set collection from a given GenomicResource."""
        assert self.resource is not None
        gene_sets = {}
        collection_format = self.config.resource_format
        logger.debug("loading %s", self.collection_id)

        if collection_format == "map":
            filename = self.config.filename
            assert filename is not None
            names_filename = filename.removesuffix(".gz")[:-4] + "names.txt"
            names_file = None
            if self.resource.file_exists(names_filename):
                names_file = self.resource.open_raw_file(names_filename)
            map_file: IO[Any]
            if filename.endswith(".gz"):
                map_file = gzip.open(  # ruff: ignore[open-file-with-context-handler]
                    self.resource.open_raw_file(filename, "rb"), "rt")
            else:
                map_file = self.resource.open_raw_file(filename)
            gene_terms = read_mapping_file(map_file, names_file)
        elif collection_format == "gmt":
            filename = self.config.filename
            assert filename is not None
            gene_terms = read_gmt_file(self.resource.open_raw_file(filename))
        elif collection_format == "directory":
            directory = self.config.directory
            assert directory is not None
            filepaths = []
            if directory == ".":
                directory = ""  # Easier check with startswith
            for filepath, _ in self.resource.get_manifest().get_files():
                if filepath.startswith(directory) and \
                        filepath.endswith(".txt"):
                    filepaths.append(filepath)

            files = [self.resource.open_raw_file(f) for f in filepaths]

            gene_terms = read_ewa_set_file(files)
        else:
            raise ValueError("Invalid collection format type")

        for key, value in gene_terms.t_desc.items():
            syms = list(gene_terms.t2g[key].keys())
            gene_set = GeneSet(key, value, syms)
            gene_sets[gene_set.name] = gene_set
        return gene_sets

    def get_gene_set(self, gene_set_id: str) -> GeneSet | None:
        """Return the gene set if found; returns None if not found."""
        gene_set = self.gene_sets.get(gene_set_id)
        if gene_set is None:
            logger.warning(
                "%s not found in %s", gene_set_id, self.gene_sets.keys(),
            )
        return gene_set

    def get_all_gene_sets(self) -> list[GeneSet]:
        return list(self.gene_sets.values())

    def get_genes_per_gene_set_hist_image_filename(self) -> str:
        return "statistics/genes_per_gene_set_histogram.png"

    def get_genes_per_gene_set_hist_filename(self) -> str:
        return "statistics/genes_per_gene_set_histogram.json"

    def get_genes_per_gene_set_hist(self) -> Histogram | None:
        hist_filename = self.get_genes_per_gene_set_hist_filename()
        return load_histogram(self.resource, hist_filename)

    def get_gene_sets_per_gene_hist_image_filename(self) -> str:
        return "statistics/gene_sets_per_gene_histogram.png"

    def get_gene_sets_per_gene_hist_filename(self) -> str:
        return "statistics/gene_sets_per_gene_histogram.json"

    def get_gene_sets_per_gene_hist(self) -> Histogram | None:
        hist_filename = self.get_gene_sets_per_gene_hist_filename()
        return load_histogram(self.resource, hist_filename)

    def get_gene_sets_list_statistics(self) -> list[dict] | None:
        """Get gene sets list statistics from the resource."""
        try:
            with self.resource.proto.open_raw_file(
                self.resource,
                "statistics/gene_sets_list_statistics.json",
                "rt",
            ) as statistics_file:
                return cast(list, json.load(statistics_file))
        except FileNotFoundError:
            return None

    def get_gene_collection_count_statistics(self) -> dict | None:
        """Get gene collection count statistics from the resource."""
        try:
            with self.resource.proto.open_raw_file(
                self.resource,
                "statistics/gene_collection_count_statistics.json",
                "rt",
            ) as statistics_file:
                return cast(dict, json.load(statistics_file))
        except FileNotFoundError:
            return None


_RESOURCE_CACHE: dict[tuple[str, str, str], GeneSetCollection] = {}
_FILE_CACHE: dict[tuple[str, str], GeneSetCollection] = {}
_INMEMORY_CACHE_LOCK = Lock()

_FORMAT_BY_EXTENSION = {
    ".txt": "map",
    ".gmt": "gmt",
}


def _detect_collection_format(filename: str) -> str:
    """Return the collection format implied by ``filename``."""
    if os.path.isdir(filename):
        return "directory"
    extension = os.path.splitext(filename)[1]
    if extension not in _FORMAT_BY_EXTENSION:
        raise ValueError("Cannot find collection format automatically")
    return _FORMAT_BY_EXTENSION[extension]


def build_gene_set_collection_from_file(
        filename: str,
        collection_id: str | None = None,
        collection_format: str | None = None,
        web_label: str | None = None,
        web_format_str: str | None = None,
) -> GeneSetCollection:
    """Return a Gene Set Collection by adapting a file to a local resource."""
    # Normalising is what puts the containing directory -- for a bare relative
    # name, the working directory -- into the cache key.
    filename = os.path.abspath(filename)
    dirname = os.path.dirname(filename)
    basename = os.path.basename(filename)
    if collection_format is None:
        collection_format = _detect_collection_format(filename)

    if collection_id is None:
        collection_id = basename

    config: dict[str, Any] = {
        "type": "gene_set_collection",
        "id": collection_id,
        "format": collection_format,
        "web_label": web_label,
        "web_format_str": web_format_str,
    }
    # A single file format is addressed by basename from the directory that
    # holds it -- the dirname/basename split ADR 0010 records for these
    # factories. The directory format has no such containing directory to
    # fall back on: rooting it at the parent would put every unrelated
    # sibling inside the resource, and reading a manifest scans -- and
    # writes state beside -- everything under the root (#911).
    if collection_format == "directory":
        root = filename
        config["directory"] = "."
    else:
        root = dirname
        config["filename"] = basename

    # Keyed on the serialized config so that every config-shaping argument --
    # present and future -- participates in the key. A resource id plus repo
    # url identifies a resource only when it is a subdirectory of a
    # repository; this one is the repository root, so which file it describes
    # lives in the config alone. Hence a cache of its own, and a collection
    # built directly rather than through the resource-keyed factory (#894).
    cache_id = (filename, json.dumps(config, sort_keys=True))

    with _INMEMORY_CACHE_LOCK:
        if cache_id in _FILE_CACHE:
            return _FILE_CACHE[cache_id]

        resource = build_local_resource(root, config)
        collection = GeneSetCollection(resource)
        _FILE_CACHE[cache_id] = collection
        return collection


def build_gene_set_collection_from_resource(
    resource: GenomicResource,
) -> GeneSetCollection:
    """Return a Gene Set Collection built from a resource."""
    cache_id = resource.get_memo_key()
    with _INMEMORY_CACHE_LOCK:
        if cache_id in _RESOURCE_CACHE:
            return _RESOURCE_CACHE[cache_id]

        collection = GeneSetCollection(resource)
        _RESOURCE_CACHE[cache_id] = collection
        return collection


def build_gene_set_collection_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GeneSetCollection:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_gene_set_collection_from_resource(
        grr.get_resource(resource_id))
