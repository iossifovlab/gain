import argparse
import os
import sys

from gain.genomic_resources.cli import (
    _create_proto,
    _find_resources,
)
from gain.genomic_resources.histogram import (
    NullHistogram,
    plot_histogram,
)
from gain.genomic_resources.repository import (
    GR_CONTENTS_FILE_NAME,
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

    for res in resourses:
        assert res.config is not None
        impl = build_resource_implementation(res)
        if not isinstance(impl, ScoreImplementationBase):
            raise TypeError(
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
