#!/usr/bin/bash

/opt/conda/bin/conda run --no-capture-output -n gpf \
    pip install --root-user-action ignore -e /wd/backend

mkdir -p /wd/backend/reports
cd /wd/backend

/opt/conda/bin/conda run --no-capture-output -n gpf \
    py.test -v web_annotation/tests \
        --cov-config /wd/backend/coveragerc \
        --cov web_annotation \
        --junitxml=/wd/backend/reports/backend-junit-report.xml \
        --mailhog http://mail:8025

/opt/conda/bin/conda run -n gpf \
    coverage xml
sed "s/\/wd\///g" /wd/backend/coverage.xml > /wd/backend/reports/backend-coverage.xml

/opt/conda/bin/conda run -n gpf \
    coverage html --title web_annotation -d /wd/backend/reports/coverage-html
