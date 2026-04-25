# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import shutil
from collections.abc import Generator
from typing import cast
from urllib.parse import urlparse

import pytest
import pytest_mock
from django.conf import LazySettings, settings
from django.test import Client
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

from web_annotation.models import Job, User
from web_annotation.tests.mailhog_client import MailhogClient


@pytest.fixture(autouse=True)
def clean_genomic_context(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "gain.genomic_resources.genomic_context._REGISTERED_CONTEXTS",
        [])


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--url",
        dest="url",
        action="store",
        default="http://localhost:21011",
        help="REST API URL",
    )

    parser.addoption(
        "--mailhog",
        dest="mailhog",
        action="store",
        default="http://localhost:8025",
        help="Mailhog REST API URL",
    )


@pytest.fixture
def test_grr(mocker: pytest_mock.MockFixture) -> GenomicResourceRepo:
    """Genomic resource repository fixture."""
    grr_dir = pathlib.Path(__file__).parent / "fixtures" / "grr"
    return build_genomic_resource_repository(
        {
            "id": "test",
            "type": "dir",
            "directory": str(grr_dir),
        },
    )


@pytest.fixture(scope="session", autouse=True)
def setup_data_dirs() -> Generator[None, None, None]:
    assert not pathlib.Path(settings.DATA_STORAGE_DIR).exists()
    pathlib.Path(settings.DATA_STORAGE_DIR).mkdir()
    pathlib.Path(settings.ANNOTATION_CONFIG_STORAGE_DIR).mkdir()
    pathlib.Path(settings.JOB_INPUT_STORAGE_DIR).mkdir()
    pathlib.Path(settings.JOB_RESULT_STORAGE_DIR).mkdir()
    yield
    shutil.rmtree(settings.DATA_STORAGE_DIR)


@pytest.fixture(autouse=True)
def setup_test_db(
    db: None,
    tmp_path: pathlib.Path,
) -> None:
    user = User.objects.create_user(
        "test-user",
        "user@example.com",
        "secret",
        id=1,
    )
    user.save()
    user_input = tmp_path / "user-input.vcf"
    user_input.write_text("mock vcf data")
    user_config = tmp_path / "user-config.yaml"
    user_config.write_text("mock annotation config")
    user_result = tmp_path / "user-result.vcf"
    user_result.write_text("mock annotated vcf")
    Job(
        input_path=user_input,
        config_path=user_config,
        result_path=user_result,
        owner=user,
        duration=1.0,
        command_line="annotate_vcf mock command line",
        id=1,
        name=1,
        disk_size=10000000,
    ).save()

    admin = User.objects.create_superuser(
        "test-admin",
        "admin@example.com",
        "secret",
        id=2,
    )
    admin.save()
    admin_input = tmp_path / "admin-input.vcf"
    admin_input.write_text("mock vcf data 2")
    admin_config = tmp_path / "admin-config.yaml"
    admin_config.write_text("mock annotation config 2")
    admin_result = tmp_path / "admin-result.vcf"
    admin_result.write_text("mock annotated vcf 2")
    Job(
        input_path=admin_input,
        config_path=admin_config,
        result_path=admin_result,
        owner=admin,
        duration=1.0,
        command_line="annotate_vcf mock command line",
        id=2,
        name=2,
        disk_size=10000000,
    ).save()


@pytest.fixture
def admin_client() -> Client:
    client = Client()
    client.login(email="admin@example.com", password="secret")
    return client


@pytest.fixture
def user_client() -> Client:
    client = Client()
    client.login(email="user@example.com", password="secret")
    return client


@pytest.fixture
def anonymous_client() -> Client:
    return Client()


@pytest.fixture
def clients(
    admin_client: Client,
    user_client: Client,
    anonymous_client: Client,
) -> dict[str, Client]:
    return {
        "admin": admin_client,
        "user": user_client,
        "anonymous": anonymous_client,
    }


@pytest.fixture
def mail_client(mailhog_url: str, settings: LazySettings) -> MailhogClient:
    """REST client fixture."""
    # Workaround for django test environment setup being hardcoded to
    # always set up a locmem backend
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    return MailhogClient(mailhog_url)


@pytest.fixture
def mailhog_url(request: pytest.FixtureRequest) -> str:
    """Mailhog URL fixture."""
    res = cast(str, request.config.getoption("--mailhog"))
    parsed = urlparse(res)
    if not parsed.scheme:
        res = f"http://{res}"
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Invalid URL: {res}")
    parsed = urlparse(res)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
