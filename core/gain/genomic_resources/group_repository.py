"""Provides group genomic resources repository."""

from collections.abc import Generator

from .repository import GenomicResource, GenomicResourceRepo


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
        for child_repo in self.children:
            yield from child_repo.search_resources(
                search_term, resource_type, resource_query)

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
