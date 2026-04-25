"""Module for annotation CLI function adaptations."""
from fsspec.asyn import reset_lock
import subprocess

def annotate_vcf_file(*args: str) -> subprocess.CompletedProcess:
    """Run annotate vcf on the files from a task."""
    reset_lock()

    return subprocess.run(["annotate_vcf", *args], check=True)

def annotate_columns_file(*args: str) -> subprocess.CompletedProcess:
    """Run annotate columns on the files from a task."""
    reset_lock()

    return subprocess.run(["annotate_columns", *args], check=True)
