#!/usr/bin/bash

/opt/conda/bin/conda run --no-capture-output -n gpf \
    pip install --root-user-action ignore -e /wd/web_api

mkdir -p /wd/web_api/reports
cd /wd/web_api

/opt/conda/bin/conda run --no-capture-output -n gpf \
    py.test -v web_annotation/tests \
        --cov-config /wd/web_api/coveragerc \
        --cov web_annotation \
        --junitxml=/wd/web_api/reports/backend-junit-report.xml \
        --mailhog http://mail:8025

/opt/conda/bin/conda run -n gpf \
    coverage xml
sed "s/\/wd\///g" /wd/web_api/coverage.xml > /wd/web_api/reports/backend-coverage.xml

/opt/conda/bin/conda run -n gpf \
    coverage html --title web_annotation -d /wd/web_api/reports/coverage-html
