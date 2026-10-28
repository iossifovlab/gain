#!/usr/bin/env bash
# Runs mutator on the gain.utils package, report only (#2062).
#
# The gain-mutator job (Jenkinsfile.mutator) runs this script in the core
# CI image, from /workspace/core. mutator mutates only the files under its
# working directory and writes target/ and .metrics/ there.
#
# Steps:
#   1. The coverage step runs tests/small/utils under pytest-cov and
#      writes the LCOV report where mutator reads it. The scope is the
#      same test run as the mutant test command, so a line that only
#      other tests cover counts as uncovered.
#   2. mutator runs on gain/utils with --use-existing-coverage. The
#      snapshot in .metrics/mutate/ makes a later run differential.
#
# Environment:
#   GAIN_PYTHON          the interpreter of the gain venv. mutator runs
#                        pytest with its own interpreter otherwise, and
#                        that interpreter has no gain.
#   MUTATOR_MAX_WORKERS  the cap on the mutator workers (default: nproc).
#   MUTATOR_REPORT_DIR   where the report goes (default: /reports).
#
# Exit codes:
#   0  mutator exited 0 (every mutant killed) or 3 (a mutant survived).
#      Survivors are the report, not a failure.
#   1  the coverage step failed or wrote no LCOV report. mutator did not run.
#   N  mutator exited N, for example 2 when the baseline tests fail.
# The job marks the build UNSTABLE on every non-zero exit.
set -u

GAIN_PYTHON="${GAIN_PYTHON:-/workspace/.venv/bin/python}"
MUTATOR_MAX_WORKERS="${MUTATOR_MAX_WORKERS:-$(nproc)}"
MUTATOR_REPORT_DIR="${MUTATOR_REPORT_DIR:-/reports}"

PACKAGE=gain/utils
TESTS=tests/small/utils
LCOV=target/coverage/python/lcov.info

mkdir -p "$MUTATOR_REPORT_DIR" "$(dirname "$LCOV")"
rm -f "$LCOV"

echo "mutator: coverage step on $TESTS"
"$GAIN_PYTHON" -m pytest -q -n auto -p no:cacheprovider \
    --cov=gain --cov-branch --cov-report="lcov:$LCOV" \
    "$TESTS"
coverage_exit=$?
if [ "$coverage_exit" -ne 0 ]; then
    echo "mutator: the coverage step failed with exit $coverage_exit, mutator did not run"
    exit 1
fi
# With --use-existing-coverage, mutator treats a file that is missing
# from the report as uncovered. A missing report would therefore run
# no mutant and still exit 0.
if [ ! -s "$LCOV" ]; then
    echo "mutator: the coverage step wrote no $LCOV, mutator did not run"
    exit 1
fi

# The mutant test command has no -n: mutator runs one worker for each
# mutant, and --max-workers sets that parallelism. -x stops a run at
# the first failure, which is all that a kill needs. `python -m pytest`
# puts the working directory first on sys.path, so each worker imports
# the mutated gain of its own overlay, not the editable install.
test_command="$GAIN_PYTHON -m pytest -q -x -p no:cacheprovider $TESTS"

echo "mutator: $PACKAGE, $MUTATOR_MAX_WORKERS workers"
start=$(date +%s)
mutator --use-existing-coverage \
    --max-workers "$MUTATOR_MAX_WORKERS" \
    --test-command "$test_command" \
    "$PACKAGE" 2>&1 | tee "$MUTATOR_REPORT_DIR/mutator.txt"
mutator_exit=${PIPESTATUS[0]}
echo "mutator: exit $mutator_exit, took $(($(date +%s) - start)) s"

case "$mutator_exit" in
    0|3) exit 0 ;;
    *) exit "$mutator_exit" ;;
esac
