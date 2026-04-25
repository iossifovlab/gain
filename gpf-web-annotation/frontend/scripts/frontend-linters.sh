#!/bin/sh

cd /app

npx eslint "**/*.{html,ts}" --format checkstyle >/reports/ts-lint-report.xml
npx stylelint --custom-formatter stylelint-checkstyle-formatter "**/*.css" >/reports/css-lint-report.xml