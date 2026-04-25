all: frontend_ package run

frontend_:
	set -e; \
	set -x; \
	cd frontend; \
	npm i; \
	npx ng build --configuration development --base-href "/static/" --source-map; \
	rm -rf ../django-gpf-web-annotation/gpf_web_annotation_frontend/static; \
	rm -rf ../django-gpf-web-annotation/gpf_web_annotation_frontend/templates; \
	cp -r dist/frontend/browser ../django-gpf-web-annotation/gpf_web_annotation_frontend/static; \

frontend_prod:
	set -e; \
	set -x; \
	cd frontend; \
	rm -rf dist; \
	npm i; \
	npx ng build --aot --configuration 'production' --base-href '/' --deploy-url '/' --source-map

package:
	set -e; \
	set -x; \
	cd django-gpf-web-annotation; \
	rm -rf *.egg-info dist/*; \
	python -m build .; \
	pip install dist/django_gpf_web_annotation-0.1-py3-none-any.whl --force-reinstall;

docker: frontend_prod package
	docker build -f Dockerfile.prod -t gpf-web-annotation-backend .

run:
	DJANGO_SETTINGS_MODULE='web_annotation.settings' django-admin runserver
