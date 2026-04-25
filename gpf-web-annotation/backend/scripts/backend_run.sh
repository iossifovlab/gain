#!/usr/bin/bash

/opt/conda/bin/conda run --no-capture-output -n gpf \
    pip install --root-user-action ignore -e /wd/backend

cd /wd/backend/
while true; do
    /opt/conda/bin/conda run --no-capture-output -n gpf \
        django-admin runserver 0.0.0.0:8000
    sleep 1
done
