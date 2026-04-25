### Backend Dependency Installation

```bash
mamba env remove -n gpf-web-annotation
mamba env create --name gpf-web-annotation --file ./backend/gpf/environment.yml
mamba env update --file ./backend/environment.yml
conda run -n gpf-web-annotation pip install -e ./backend/gpf/dae
conda activate gpf-web-annotation
mamba env update -n gpf-web-annotation --file ./backend/dev-environment.yml
pip install -e ./backend
```

### Frontend Dependency Installation

```bash
cd frontend
npm install
```

### Running the app

Run the backend

```bash
cd backend
conda activate gpf-web-annotation
export GRR_DEFINITION_FILE=~/grr_definition.yaml
python manage.py migrate
python manage.py runserver

```

Run the frontend

```bash
cd frontend
ng serve
```

### Running frontend tests

```bash
cd frontend
npm test
```

### Running backend tests

```bash
cd backend
export DJANGO_SETTINGS_MODULE=web_annotation.test_settings
pytest web_annotation/tests/
```


## Run using Docker compose

### Initial build
```
mv ~/conda-channel.tar.gz .
tar zxvf conda-channel.tar.gz 
docker compose build ubuntu-image
docker compose build gpf-image
docker compose build backend
docker compose build frontend
```

### Initial start
```
docker compose up frontend
```

```
http://localhost
```

### Rebuild and re-start the frontend
```
docker compose down frontend
docker compose build frontend
docker compose up frontend
```
