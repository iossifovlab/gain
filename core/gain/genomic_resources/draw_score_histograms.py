import argparse
import os
import sys

from gain import logging
from gain.genomic_resources.cli import (
    _create_proto,
    _find_resources,
)
from gain.genomic_resources.cli_errors import (
    RESOURCE_ERRORS,
    report_resource_failure,
)
from gain.genomic_resources.histogram import (
    NullHistogram,
    plot_histogram,
)
from gain.genomic_resources.repository import (
    GR_CONTENTS_FILE_NAME,
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.utils.fs_utils import find_directory_with_a_file
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("draw_score_histograms")


class ScorelessResourceError(TypeError):
    """A resource whose type carries no scores at all.

    Distinguished from the errors that mean a resource is *broken*: there
    is nothing wrong with a genome, it simply has no histograms to draw.
    A ``TypeError`` because that is what selecting such a resource by id
    has always raised, and callers still get exactly that.
    """


def parse_cli_arguments() -> argparse.ArgumentParser:
    """Create CLI parser."""
    parser = argparse.ArgumentParser(
        description="Draw histograms for genomic scores.")

    VerbosityConfiguration.set_arguments(parser)

    parser.add_argument(
        "-R",
        "--repository",
        help="Optional URL to the genomic resources repository.",
    )
    parser.add_argument(
        "-r",
        "--resource",
        help="Optional URL to the resource.",
    )

    return parser


def main(
        argv: list[str] | None = None,
) -> None:
    """Liftover dae variants tool main function."""
    if argv is None:
        argv = sys.argv[1:]

    parser = parse_cli_arguments()
    args = parser.parse_args(argv)

    VerbosityConfiguration.set(args)

    repo_path = find_directory_with_a_file(
        GR_CONTENTS_FILE_NAME,
        args.repository,
    )
    if repo_path is None:
        current_path = args.repository
        if current_path is None:
            current_path = os.getcwd()
        print("Can't find repository starting from: %s", current_path)
        sys.exit(1)

    repo_url = str(repo_path)
    print(f"working with repository: {repo_url}")

    proto = _create_proto(repo_url)

    if not isinstance(proto, ReadWriteRepositoryProtocol):
        raise TypeError(
            f"resource management works with RW protocols; "
            f"{proto.proto_id} ({proto.scheme}) is read only")

    resourses = _find_resources(proto, repo_url, resource=args.resource)
    if not resourses:
        print("Resource not found...")
        sys.exit(1)

    # Selecting exactly one resource is an assertion that it has
    # histograms to draw; being told it has none is then the answer, not
    # noise.  Across a sweep the same resource is merely uninteresting --
    # every real GRR holds a genome and gene models (gain#537).
    one_resource_selected = len(resourses) == 1

    failed: set[str] = set()
    for res in resourses:
        assert res.config is not None
        try:
            _draw_resource_histograms(res)
        except ScorelessResourceError:
            if one_resource_selected:
                raise
            logger.info(
                "nothing to draw for <%s>: a %s resource carries no scores",
                res.resource_id, res.get_type())
        except RESOURCE_ERRORS as err:
            # One resource the tool cannot build an implementation for
            # costs the user that resource, not the rest of the
            # repository -- the same bargain every `grr_manage` sweep
            # already makes (gain#364, gain#537).  NOT widened to
            # `Exception`: an unexpected error is still a crash.
            report_resource_failure(
                err, "could not draw histograms for", res.resource_id)
            failed.add(res.resource_id)

    if failed:
        # Reported once at the end as well as per resource: a long sweep
        # scrolls its individual failures out of sight, and the exit
        # status alone does not say which resources to go and look at.
        logger.error(
            "resources whose histograms could not be drawn in GRR <%s>: %s",
            repo_url, ", ".join(sorted(failed)))
        sys.exit(1)


def _draw_resource_histograms(res: GenomicResource) -> None:
    """Draw every non-null score histogram of one resource."""
    impl = build_resource_implementation(res)
    if not isinstance(impl, ScoreImplementationBase):
        raise ScorelessResourceError(
            f"can't draw histograms for resource <{res.resource_id}>: "
            f"a {res.get_type()} resource carries no scores")
    score = impl.score

    for score_id in score.get_all_scores():
        hist = score.get_score_histogram(score_id)
        if isinstance(hist, NullHistogram):
            continue
        score_def = score.score_definitions[score_id]
        plot_histogram(
            res,
            score.get_histogram_image_filename(score_id),
            hist,
            score_id,
            score_def.small_values_desc,
            score_def.large_values_desc,
        )
