#!/usr/bin/bash

/opt/conda/bin/conda run --no-capture-output -n gpf \
    pip install --root-user-action ignore -e /wd/web_api

cd /wd/web_api/
mkdir -p /wd/web_api/reports

/opt/conda/bin/conda run --no-capture-output -n gpf ruff check \
    --exit-zero \
    --output-format=pylint \
    --output-file=/wd/web_api/reports/ruff_report web_annotation || true

/opt/conda/bin/conda run --no-capture-output -n gpf \
    pylint web_annotation -f parseable --reports=no -j 4 \
    --exit-zero > /wd/web_api/reports/pylint_report || true

/opt/conda/bin/conda run --no-capture-output -n gpf mypy \
    web_annotation \
    --pretty \
    --show-error-context \
    --no-incremental > /wd/web_api/reports/mypy_report || true

/opt/conda/bin/conda run --no-capture-output -n gpf \
    python scripts/convert_mypy_output.py \
    /wd/web_api/reports/mypy_report > /wd/web_api/reports/mypy_pylint_report || true
