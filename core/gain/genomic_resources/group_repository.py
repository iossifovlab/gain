"""Provides group genomic resources repository."""

from collections.abc import Generator

from gain import logging

from .repository import (
    GenomicResource,
    GenomicResourceRepo,
    SearchIndexUnavailableError,
    SearchTermError,
)
from .resource_query import ResourceQuery

logger = logging.getLogger(__name__)


class GenomicResourceGroupRepo(GenomicResourceRepo):
    """Defines group genomic resources repository."""

    def __init__(
            self, children: list[GenomicResourceRepo],
            repo_id: str | None = None):
        if repo_id is None:
            repo_id = "group_repo"
        super().__init__(repo_id)

        self.children = children

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def get_all_resources(self) -> Generator[GenomicResource, None, None]:
        for child_repo in self.children:
            yield from child_repo.get_all_resources()

    def find_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource | None:

        # This group *is* the requested repository: naming a repository by
        # its own id selects it, it does not filter it out. Below this point
        # there is nothing left to filter, exactly as when a child is
        # matched by id -- a group used to compare the filter only against
        # its children's ids, so naming the group a caller was holding
        # selected nothing at all. A leaf protocol repo has always
        # self-named; #447 made the rule the same at every layer.
        if repository_id and repository_id == self.repo_id:
            repository_id = None

        for child_repo in self.children:
            # Truthiness, not `is not None`: GenomicResourceProtocolRepo
            # ignores a falsy repository_id, so treating "" as a real filter
            # here would make the two layers disagree.
            if repository_id and child_repo.repo_id == repository_id:
                # This child *is* the requested repository. Re-applying the
                # filter inside it would compare repository_id against its
                # own children's ids and find nothing.
                res = child_repo.find_resource(
                    resource_id, version_constraint)
            else:
                # Forward rather than skip: a non-matching child may be a
                # nested group that contains the requested repository. A
                # non-matching leaf repo filters itself out and returns None.
                res = child_repo.find_resource(
                    resource_id, version_constraint, repository_id)
            if res:
                return res

        return None

    def search_resources(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[GenomicResource, None, None]:
        if resource_query:
            # Parsed and discarded: a malformed query must fail when the
            # call is made rather than when the first child is reached,
            # and a group with no children would otherwise never parse it
            # at all. The children parse it again for themselves; the
            # grammar is built once and cached.
            ResourceQuery.parse(resource_query)
        return self._search_resources(
            search_term, resource_type, resource_query)

    def search_resources_by_child(
        self,
        search_term: str | None = None,
        resource_type: str | None = None,
        resource_query: str | None = None,
    ) -> Generator[tuple[GenomicResourceRepo, GenomicResource], None, None]:
        """Search, pairing each hit with the child repository serving it.

        The pair names the repository that actually holds the resource: a
        nested group projects its own pairs upward rather than naming
        itself, so a caller never has to take a group apart to label a row.

        This is where a child that cannot answer the filter is skipped, and
        :meth:`search_resources` is its projection -- the two cannot drift,
        because there is only one loop.
        """
        if resource_query:
            # Eagerly, exactly as `search_resources` does: this method is
            # not a generator function, so a malformed query still fails
            # when the call is made rather than on the first iteration.
            ResourceQuery.parse(resource_query)
        return self._search_by_child(
            search_term, resource_type, resource_query)

    def _search_resources(
        self,
        search_term: str | None,
        resource_type: str | None,
        resource_query: str | None,
    ) -> Generator[GenomicResource, None, None]:
        for _, res in self._search_by_child(
                search_term, resource_type, resource_query):
            yield res

    def _search_by_child(
        self,
        search_term: str | None,
        resource_type: str | None,
        resource_query: str | None,
    ) -> Generator[tuple[GenomicResourceRepo, GenomicResource], None, None]:
        answered = False
        skipped: list[tuple[str, Exception]] = []
        for child_repo in self.children:
            # Counted rather than `yield from`ed: absorption is bounded to
            # a child that failed before yielding anything. Both absorbed
            # failures are raised ahead of the child's first row -- the
            # index is opened and the filter probed before the statement
            # runs -- so nothing legitimate is lost, and a failure arriving
            # mid-scan can never be turned into a warning over a truncated
            # result.
            yielded = 0
            try:
                # Asked for pairs, not resources: a child that is itself a
                # group answers with its own leaves, so the holder that
                # reaches the caller is never an intermediate group.
                for holder, res in child_repo.search_resources_by_child(
                        search_term, resource_type, resource_query):
                    yielded += 1
                    yield holder, res
            except (SearchTermError, SearchIndexUnavailableError) as err:
                if yielded:
                    raise
                # Either this child's index publishes no column the filter
                # names -- a fact about that index rather than about the
                # filter, since the column vocabulary is per-repository --
                # or it has no index to apply the filter with at all.
                # Skipped so the children that can answer still do
                # (gain#680).
                logger.warning(
                    "repository <%s> cannot answer this search, skipping "
                    "it: %s", child_repo.repo_id, err)
                skipped.append((child_repo.repo_id, err))
                continue
            # Answered, even if it matched nothing: only a skip leaves the
            # filter unevaluated against that child's resources.
            answered = True

        if skipped and not answered:
            # No child evaluated the filter at all, so an empty result would
            # be a lie -- it would read as "nothing matched" when nothing
            # was searched.
            raise self._nothing_could_answer(search_term, skipped)

    def _nothing_could_answer(
        self, search_term: str | None,
        skipped: list[tuple[str, Exception]],
    ) -> Exception:
        """Build the failure for a search no child could evaluate.

        Repository health wins. A child that read its index and still
        rejected the filter proves only that *its* column vocabulary lacks
        what the filter names -- which is the very thing that is not a
        property of the filter. So as long as one child could not be read
        at all, repairing it might publish the column the filter wants, and
        the caller has been told nothing wrong. Only when every child could
        read its index and every one of them rejected the filter is the
        filter itself at fault.
        """
        reasons = "; ".join(
            f"{repo_id}: {err}" for repo_id, err in skipped)
        if any(
            isinstance(err, SearchIndexUnavailableError)
            for _, err in skipped
        ):
            return SearchIndexUnavailableError(
                self.repo_id,
                f"no child repository could apply this search ({reasons})")
        # Every child read its index and rejected the filter, so the filter
        # names something no repository here publishes.
        return SearchTermError(
            search_term if search_term is not None else "",
            ValueError(
                f"no child repository of <{self.repo_id}> could answer it "
                f"({reasons})"),
        )

    def get_resource(
            self, resource_id: str, version_constraint: str | None = None,
            repository_id: str | None = None) -> GenomicResource:

        # Delegates to find_resource so the two cannot drift apart: they
        # previously carried duplicate copies of the child filter, and only
        # one of them forwarded repository_id to the child. See #429.
        res = self.find_resource(
            resource_id, version_constraint, repository_id)
        if res is None:
            raise ValueError(
                f"resource {resource_id} {version_constraint} "
                f"({repository_id}) not found")
        return res
