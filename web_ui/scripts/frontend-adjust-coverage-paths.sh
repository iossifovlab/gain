#!/bin/sh


sed "s/>\/app</>web_ui</g" \
    web_ui/reports/coverage/cobertura-coverage.xml > web_ui/reports/frontend-coverage.xml