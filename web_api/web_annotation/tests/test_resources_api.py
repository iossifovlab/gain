# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
import pytest_mock
from django.test import Client
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
    SearchIndexUnavailableError,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

from web_annotation.resources.views import SearchResources


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
@pytest.mark.parametrize(
    "type_filter,search_term,expected",
    [
        (
            None, None,
            {
                "page": 0,
                "pages": 1,
                "total_resources": 11,
                "incomplete": [],
                "resources": {
                    "hg38/GRCh38-hg38/genome",
                    "liftover/mock",
                    "scores/allele1",
                    "scores/pos1",
                    "scores/pos2",
                    "t4c8/gene_scores/t4c8_score",
                    "t4c8/gene_sets/main",
                    "t4c8/genomic_scores/score_one",
                    "t4c8/t4c8_genes",
                    "t4c8/t4c8_genome",
                    "cnv_collections/test_collection",
                },
            },
        ),
        (
            "genome", None,
            {
                "page": 0,
                "pages": 1,
                "total_resources": 2,
                "incomplete": [],
                "resources": {
                    "hg38/GRCh38-hg38/genome",
                    "t4c8/t4c8_genome",
                },
            },
        ),
        (
            "gene_set_collection", None,
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "incomplete": [],
                 "resources": {
                    "t4c8/gene_sets/main",
                 },
            },
        ),
        (
            "position_score", None,
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 3,
                 "incomplete": [],
                 "resources": {
                    "scores/pos1",
                    "scores/pos2",
                    "t4c8/genomic_scores/score_one",
                 },
            },
        ),
        (
            "position_score", "score_one",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "incomplete": [],
                 "resources": {
                    "t4c8/genomic_scores/score_one",
                 },
            },
        ),
        (
            None, "score_one",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "incomplete": [],
                 "resources": {
                    "t4c8/genomic_scores/score_one",
                 },
            },
        ),
        (
            None, "t4c8",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 5,
                 "incomplete": [],
                 "resources": {
                    "t4c8/gene_scores/t4c8_score",
                    "t4c8/gene_sets/main",
                    "t4c8/genomic_scores/score_one",
                    "t4c8/t4c8_genes",
                    "t4c8/t4c8_genome",
                 },
            },
        ),
        # The two request shapes below answer out of the FTS index --
        # every filtered request does -- and both are sensitive to
        # whether `scores/allele1` is in it. The committed index was
        # stale: `scores/allele1` and `pipeline/allele_pipeline` had been
        # on the fixture filesystem for some time without ever having
        # been indexed, so a `search`/`type` request could not see them
        # while an unfiltered one (which short-circuits to
        # `get_all_resources()`) could. Rebuilding the index to carry the
        # label columns the query language needs also corrected that, and
        # these two cases pin the corrected answers so the next rebuild
        # cannot move them unnoticed.
        (
            None, "scores",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 5,
                 "incomplete": [],
                 "resources": {
                    "scores/allele1",
                    "scores/pos1",
                    "scores/pos2",
                    "t4c8/gene_scores/t4c8_score",
                    "t4c8/genomic_scores/score_one",
                 },
            },
        ),
        (
            "allele_score", None,
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "incomplete": [],
                 "resources": {
                    "scores/allele1",
                 },
            },
        ),
    ],
)
def test_get_resources(
    current_client: str, clients: dict[str, Client],
    type_filter: str | None,
    search_term: str | None,
    expected: list[str],
) -> None:
    client = clients[current_client]

    query_params = {}
    if type_filter:
        query_params["type"] = type_filter
    if search_term:
        query_params["search"] = search_term

    response = client.get("/api/resources/search", query_params=query_params)

    assert response.status_code == 200
    result = response.json()
    result["resources"] = {res["resource_id"] for res in result["resources"]}
    assert result == expected


def test_a_complete_search_answers_incomplete_empty(
    clients: dict[str, Client],
) -> None:
    """The payload says the totals are whole, not merely implies it.

    ``incomplete`` is always present so a client branches on its content,
    never on its existence (gain#686).
    """
    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"search": "scores"})

    assert response.status_code == 200
    assert response.json()["incomplete"] == []


def test_a_search_a_child_could_not_answer_says_how_totals_fall_short(
    clients: dict[str, Client],
    mocker: pytest_mock.MockFixture,
    tmp_path: pathlib.Path,
) -> None:
    """A skipped child is in the payload, not only in the server log.

    Without it, "1 of 1 resources" over a half-skipped group is wrong in
    a way that looks right (gain#686).
    """
    group = GenomicResourceGroupRepo([
        _an_indexed_child(tmp_path / "one", "scores/a", {"assay": "atac"}),
        _an_indexed_child(tmp_path / "two", "scores/b", {"unrelated": "x"}),
    ])
    mocker.patch("web_annotation.annotation_base_view.GRR", group)

    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"search": 'assay: "atac"'})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_resources"] == 1
    assert [res["resource_id"] for res in payload["resources"]] == ["scores/a"]
    [entry] = payload["incomplete"]
    assert entry["repo"] == group.children[1].repo_id
    assert "assay" in entry["reason"]


def _an_indexed_child(
    root: pathlib.Path,
    resource_id: str,
    labels: dict[str, str],
) -> GenomicResourceProtocolRepo:
    """Realize a one-resource GRR at ``root``, index included."""
    repo = (
        a_grr()
        .with_resource(resource_id, a_position_score().with_labels(**labels))
        .build_repo(root)
    )
    _create_contents_db(build_filesystem_test_protocol(root))
    return repo


def test_resource_query_selects_by_id_glob(clients: dict[str, Client]) -> None:
    response = clients["user"].get(
        "/api/resources/search", query_params={"query": "scores/*"})

    assert response.status_code == 200
    result = response.json()
    assert {res["resource_id"] for res in result["resources"]} == {
        "scores/allele1",
        "scores/pos1",
        "scores/pos2",
    }


def test_resource_query_filters_on_labels(clients: dict[str, Client]) -> None:
    response = clients["user"].get(
        "/api/resources/search",
        query_params={"query": 'scores/*[phenotype="autism"]'})

    assert response.status_code == 200
    result = response.json()
    assert {res["resource_id"] for res in result["resources"]} == {
        "scores/allele1",
        "scores/pos1",
    }


@pytest.mark.parametrize(
    "query_params,expected",
    [
        # The query alone: `scores/pos2` carries a different phenotype
        # label, `t4c8/genomic_scores/score_one` a non-matching id.
        (
            {"query": 'scores/*[phenotype="autism"]'},
            {"scores/allele1", "scores/pos1"},
        ),
        # ... narrowed by `type`, which drops the allele score the query
        # selected -- rather than replacing the query, which would have
        # admitted every position score in the repository.
        (
            {"query": 'scores/*[phenotype="autism"]',
             "type": "position_score"},
            {"scores/pos1"},
        ),
        # ... and by `search`, which drops the allele score too. Without
        # the query, this search term selects `scores/pos1` alone as well,
        # so the case that discriminates is the one below.
        (
            {"query": 'scores/*[phenotype="autism"]', "search": "pos1"},
            {"scores/pos1"},
        ),
        # All three at once: `search` here is wide enough to admit five
        # resources on its own, `type` three, and the query two; only the
        # one resource that satisfies every one of them comes back.
        (
            {"query": 'scores/*[phenotype="autism"]',
             "type": "position_score",
             "search": "scores"},
            {"scores/pos1"},
        ),
    ],
)
def test_resource_query_composes_with_search_and_type(
    clients: dict[str, Client],
    query_params: dict[str, str],
    expected: set[str],
) -> None:
    response = clients["user"].get(
        "/api/resources/search", query_params=query_params)

    assert response.status_code == 200
    result = response.json()
    assert {res["resource_id"] for res in result["resources"]} == expected
    assert result["total_resources"] == len(expected)


@pytest.mark.parametrize(
    "page,expected_on_page",
    [(0, 2), (1, 1)],
)
def test_resource_query_pages_over_what_it_selected(
    clients: dict[str, Client],
    page: int,
    expected_on_page: int,
) -> None:
    response = clients["user"].get(
        "/api/resources/search",
        query_params={"query": "scores/*", "page_size": 2, "page": page})

    assert response.status_code == 200
    result = response.json()
    assert len(result["resources"]) == expected_on_page
    assert result["page"] == page
    # The counts describe the query's result set, not the repository.
    assert result["pages"] == 2
    assert result["total_resources"] == 3


def test_resource_query_cannot_select_an_unsupported_resource_type(
    clients: dict[str, Client],
) -> None:
    """A query is not a way around the endpoint's type allowlist.

    ``pipeline/*`` names two real resources in the test repository, both
    annotation pipelines -- a type this endpoint does not serve.
    """
    response = clients["user"].get(
        "/api/resources/search", query_params={"query": "pipeline/*"})

    assert response.status_code == 200
    assert response.json() == {
        "page": 0,
        "pages": 0,
        "total_resources": 0,
        "incomplete": [],
        "resources": [],
    }


def test_an_empty_resource_query_is_an_unset_one(
    clients: dict[str, Client],
) -> None:
    """``query=`` is what a client substitutes for a selector it never got."""
    without_query = clients["user"].get("/api/resources/search")
    with_empty_query = clients["user"].get(
        "/api/resources/search", query_params={"query": ""})

    assert with_empty_query.status_code == 200
    assert with_empty_query.json() == without_query.json()


def test_an_empty_search_term_is_an_unset_one(
    clients: dict[str, Client],
) -> None:
    """``search=`` is the same client mistake as ``query=`` (gain#633)."""
    without_search = clients["user"].get("/api/resources/search")
    with_empty_search = clients["user"].get(
        "/api/resources/search", query_params={"search": ""})

    assert with_empty_search.status_code == 200
    assert with_empty_search.json() == without_search.json()


def test_a_resource_record_says_nothing_about_its_labels(
    clients: dict[str, Client],
) -> None:
    """The response body is what it was before labels became queryable.

    ``scores/pos1`` carries a ``phenotype`` label the query language can
    select on; none of it belongs in what the endpoint reports about the
    resource.
    """
    response = clients["user"].get(
        "/api/resources/search", query_params={"search": "pos1"})

    assert response.status_code == 200
    record, = response.json()["resources"]
    assert record.keys() == {
        "full_id", "resource_id", "type", "version", "summary", "url",
    }
    assert record["full_id"] == "scores/pos1"
    assert record["resource_id"] == "scores/pos1"
    assert record["type"] == "position_score"
    assert record["version"] == [0]
    assert record["summary"] == ""
    # The GRR is a directory in the test fixtures, so only the tail of the
    # url is fixed.
    assert record["url"].endswith("/scores/pos1")


def test_malformed_resource_query_is_a_bad_request(
    clients: dict[str, Client],
) -> None:
    response = clients["user"].get(
        "/api/resources/search",
        query_params={"query": 'scores/*[phenotype ~ "autism"]'})

    assert response.status_code == 400
    message = response.json()["error"]
    # The message has to say which query failed and where -- a bare 400
    # leaves the caller guessing which of its parameters was rejected.
    assert 'scores/*[phenotype ~ "autism"]' in message
    assert "~" in message


@pytest.mark.parametrize("search_term", [
    '"',       # unterminated string
    "a AND",   # a binary operator with nothing on its right
    "(",       # unbalanced parenthesis
    "*",       # not a term at all
    "a:b",     # a column filter naming a column the index has not got
    "NEAR/",   # a truncated NEAR
])
def test_a_malformed_search_term_is_a_bad_request(
    clients: dict[str, Client],
    search_term: str,
) -> None:
    """A term FTS5 rejects is a bad request, not a 500 (gain#632).

    Asked as the anonymous caller because the endpoint has neither an
    authentication nor a throttle class of its own: an unauthenticated
    caller is exactly who reaches this. The view has no per-caller
    branch, so the other two clients would only repeat the case.
    """
    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"search": search_term})

    assert response.status_code == 400
    # Same contract as the malformed `query` above: the message names the
    # term, so a caller with several parameters knows which was rejected.
    assert search_term in response.json()["error"]


def test_an_over_long_resource_query_is_a_bad_request(
    clients: dict[str, Client],
) -> None:
    """A long query is refused on length, before anything parses it.

    The grammar is ambiguous (``and_`` recurses through ``?operation``),
    so Earley costs about O(n^3) in the length of the query: 500 clauses
    -- 5003 characters, well inside Apache's 8190-byte request line -- is
    ~95 seconds of CPU. This endpoint is anonymous and unthrottled, so a
    handful of such GETs would pin every worker.
    """
    clauses = " and ".join(['k="v"'] * 500)
    query = f"scores/*[{clauses}]"

    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"query": query})

    assert response.status_code == 400
    message = response.json()["error"]
    # The caller has to learn what the limit is to get under it.
    assert str(len(query)) in message
    assert str(SearchResources.MAX_RESOURCE_QUERY_LENGTH) in message


def test_a_resource_query_at_the_length_cap_is_still_served(
    clients: dict[str, Client],
) -> None:
    """The cap refuses what is over it, and nothing else.

    A query as long as the limit allows is answered normally -- the guard
    is a bound on the parser's input, not a new way for a well-formed
    query to be rejected.
    """
    cap = SearchResources.MAX_RESOURCE_QUERY_LENGTH
    clauses = " and ".join(['phenotype="autism"'] * 10)
    query = f"scores/pos1[{clauses}]"
    # The grammar ignores spaces, so the padding changes nothing but the
    # length.
    query = query.replace("]", " " * (cap - len(query)) + "]")
    assert len(query) == cap

    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"query": query})

    assert response.status_code == 200
    result = response.json()
    assert {res["resource_id"] for res in result["resources"]} == {
        "scores/pos1",
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pagination(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    query_params = {"page_size": 2}
    response = client.get("/api/resources/search", query_params=query_params)
    assert response.status_code == 200
    response_json = response.json()
    assert len(response_json["resources"]) == 2
    assert response_json["pages"] == 6
    assert response_json["page"] == 0


def test_every_listed_resource_is_indexed(
    clients: dict[str, Client],
) -> None:
    """A resource in the fixture GRR has to be indexed, not just present.

    Every filtered request answers out of the committed FTS index, while
    an unfiltered listing walks the filesystem, so a resource dropped into
    the fixture without a rebuild is visible to the one and invisible to
    the other -- the drift the `scores/allele1` cases above were added to
    pin, one resource at a time.  This pins it for the whole fixture: the
    union of every ``type`` filter has to be the unfiltered listing, so
    the next addition without a rebuild (gain#1165 was the last) fails
    here rather than surfacing as an empty picker somewhere.
    """
    client = clients["anonymous"]
    listed = set(client.get("/api/resources").json())
    assert listed, "an empty listing would make the union below vacuous"

    indexed: set[str] = set()
    for resource_type in client.get("/api/resources/types").json():
        response = client.get(
            "/api/resources/search",
            query_params={"type": resource_type, "page_size": 100})
        assert response.status_code == 200, response.content
        indexed |= {
            res["resource_id"] for res in response.json()["resources"]}

    assert indexed == listed


@pytest.mark.parametrize("page_size", [0, -1])
def test_a_non_positive_page_size_is_a_bad_request(
    anonymous_client: Client,
    page_size: int,
) -> None:
    """``page_size<1`` is refused up front, not a ZeroDivisionError.

    Asked as the anonymous caller for the same reason as the malformed
    search term above: an unauthenticated caller is exactly who reaches
    this endpoint (gain#631).
    """
    response = anonymous_client.get(
        "/api/resources/search", query_params={"page_size": page_size})

    assert response.status_code == 400
    message = response.json()["error"]
    # Same contract as the malformed `query` above: the message names the
    # parameter and its value, so a caller with several of them knows
    # which was rejected.
    assert "page_size" in message
    assert str(page_size) in message


@pytest.mark.parametrize("param", ["page", "page_size"])
def test_a_non_integer_page_parameter_is_a_bad_request(
    anonymous_client: Client,
    param: str,
) -> None:
    """A non-integer ``page``/``page_size`` names itself in the refusal.

    The 400 itself predates gain#631; the error body naming the rejected
    parameter is the same contract as the range refusals below.
    """
    response = anonymous_client.get(
        "/api/resources/search", query_params={param: "abc"})

    assert response.status_code == 400
    message = response.json()["error"]
    assert param in message
    if param == "page":
        # `page` alone would also match a misattributed `page_size`.
        assert "page_size" not in message
    assert "abc" in message


@pytest.mark.parametrize("param", ["page", "page_size"])
def test_a_bound_past_the_maximal_slice_index_is_a_bad_request(
    anonymous_client: Client,
    param: str,
) -> None:
    """Bounds ``islice`` cannot address are refused, not a 500 (gain#631).

    ``islice`` accepts indices only up to ``sys.maxsize``; a request past
    that bound -- through either parameter -- used to surface the
    resulting ValueError as an unhandled 500.
    """
    value = 99999999999999999999

    response = anonymous_client.get(
        "/api/resources/search", query_params={param: value})

    assert response.status_code == 400
    message = response.json()["error"]
    assert param in message
    assert str(value) in message


def test_a_negative_page_is_a_bad_request(
    anonymous_client: Client,
) -> None:
    """A negative ``page`` is refused, not handed to ``islice`` (gain#631)."""
    response = anonymous_client.get(
        "/api/resources/search", query_params={"page": -1})

    assert response.status_code == 400
    message = response.json()["error"]
    # `page` alone would also match a misattributed `page_size` message.
    assert "page" in message
    assert "page_size" not in message
    assert "-1" in message


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_get_resource_types(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.get("/api/resources/types")

    assert response.status_code == 200
    assert set(response.json()) == {
        "genome",
        "gene_models",
        "liftover_chain",
        "gene_set_collection",
        "position_score",
        "allele_score",
        "gene_score",
        # Both accepted spellings are advertised here, unlike the editor's
        # annotator menu: this endpoint reports which resource types the
        # API can READ, and it can read the deprecated one until it stops
        # being accepted in 2027.1.0 (gain#471, gain#538).
        "fragment_score",
        "cnv_collection",
    }


def test_a_repository_with_no_search_index_is_a_handled_server_error(
    clients: dict[str, Client],
    mocker: pytest_mock.MockFixture,
) -> None:
    """No index is the repository's problem, not the caller's (ADR 0012).

    A filter nothing can be applied with must not be reported as a bad
    request: the caller supplied nothing wrong and has no repair to make.
    It must not escape as an unhandled exception either -- the answer
    names the repositories and the repair instead of being a traceback.
    """
    # Patched where the view reads it: the base view copies the module
    # global into `self._grr` when the request is constructed.
    mocker.patch(
        "web_annotation.annotation_base_view.GRR", _unsearchable_repo())

    response = clients["anonymous"].get(
        "/api/resources/search", query_params={"search": "anything"})

    assert response.status_code == 500
    assert "repo-repair" in response.json()["error"]


def _unsearchable_repo() -> GenomicResourceRepo:
    """A repository that cannot apply a search filter at all."""
    class _NoIndex(GenomicResourceRepo):
        def invalidate(self) -> None:
            return

        def get_all_resources(self):  # type: ignore[no-untyped-def]
            return iter(())

        def find_resource(  # type: ignore[no-untyped-def]
            self, resource_id, version_constraint=None, repository_id=None,
        ):
            return None

        def get_resource(  # type: ignore[no-untyped-def]
            self, resource_id, version_constraint=None, repository_id=None,
        ):
            raise ValueError(resource_id)

        def search_resources(  # type: ignore[no-untyped-def]
            self, search_term=None, resource_type=None, resource_query=None,
        ):
            raise SearchIndexUnavailableError(
                "unindexed", "no search index was published")

    return _NoIndex("unindexed")
