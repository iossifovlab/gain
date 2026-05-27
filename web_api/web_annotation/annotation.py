"""Module for annotation CLI function adaptations."""
import subprocess

from fsspec.asyn import reset_lock


def annotate_vcf_file(*args: str) -> subprocess.CompletedProcess:
    """Run annotate vcf on the files from a task."""
    reset_lock()

    return subprocess.run(
        ["annotate_vcf", *args],  # noqa: S607
        check=True,
    )


def annotate_tabular_file(*args: str) -> subprocess.CompletedProcess:
    """Run annotate tabular on the files from a task."""
    reset_lock()

    return subprocess.run(
        ["annotate_tabular", *args],  # noqa: S607
        check=True,
    )
