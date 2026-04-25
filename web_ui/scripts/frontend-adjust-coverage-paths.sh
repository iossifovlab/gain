#!/bin/sh


sed "s/>\/app</>frontend</g" \
    frontend/reports/coverage/cobertura-coverage.xml > frontend/reports/frontend-coverage.xml