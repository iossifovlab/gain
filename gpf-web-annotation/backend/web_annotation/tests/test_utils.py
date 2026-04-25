
# pylint: disable=W0621,C0114,C0116,W0212,W0613
from pathlib import Path
from subprocess import CalledProcessError
import textwrap
import pytest
from pytest_mock import MockerFixture

from web_annotation.utils import bytes_to_readable, convert_size, validate_vcf
from web_annotation.utils import get_ip_from_request
from web_annotation.utils import calculate_used_disk_space


@pytest.mark.parametrize(
    "raw_bytes, result",
    [
        (79345670000000, "79.3 TB"),
        (13000030600, "13.0 GB"),
        (19004345, "19.0 MB"),
        (1203000, "1.2 MB"),
        (900000, "900.0 KB"),
        (15600, "15.6 KB"),
        (3333, "3.3 KB"),
        (150, "0.1 KB"),
    ]
)
def test_bytes_to_readable(
  raw_bytes: int,
  result: str,
) -> None:
    assert bytes_to_readable(raw_bytes) == result


@pytest.mark.parametrize(
    "readable, result",
    [
        ("79.3 TB", 79300000000000),
        ("13.0 GB", 13000000000),
        ("19.0 MB", 19000000),
        ("1.2 MB", 1200000),
        ("900.0 KB", 900000),
        ("15.6 KB", 15600),
        ("3.3 KB", 3300),
        ("0.1 KB", 100),
        (679, 679),
    ]
)
def test_readable_to_bytes(
  readable: str,
  result: int,
) -> None:
    assert convert_size(readable) == result


def test_validate_vcf_file_valid(
  tmp_path: Path,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip("\n")
    vcf_path = tmp_path / "valid.vcf"
    vcf_path.write_text(vcf)

    assert validate_vcf(str(vcf_path), 1) is True


def test_validate_vcf_file_limit(
  tmp_path: Path,
  mocker: MockerFixture,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
        chr1	2	.	B	A	.	.	.
        chr1	3	.	T	A	.	.	.
    """).strip("\n")
    vcf_path = tmp_path / "invalid.vcf"
    vcf_path.write_text(vcf)

    assert validate_vcf(str(vcf_path), 2) is False


def test_validate_vcf_file_invalid_chromosome(
  tmp_path: Path,
  mocker: MockerFixture,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
        chr1	2	.	B	A	.	.	.
        chr2	3	.	T	A	.	.	.
    """).strip("\n")
    vcf_path = tmp_path / "invalid.vcf"
    vcf_path.write_text(vcf)

    with pytest.raises(CalledProcessError) as err:
        validate_vcf(str(vcf_path))

    assert "1 Contig not defined in header" in str(err.value.stderr)


def test_validate_vcf_file_invalid_header(
  tmp_path: Path,
  mocker: MockerFixture,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        chr1	1	.	C	A	.	.	.
        chr1	2	.	B	A	.	.	.
        chr1	3	.	T	A	.	.	.
    """).strip("\n")
    vcf_path = tmp_path / "invalid.vcf"
    vcf_path.write_text(vcf)

    with pytest.raises(CalledProcessError) as err:
        validate_vcf(str(vcf_path))

    assert "does not have valid header" in str(err.value.stderr)


@pytest.mark.parametrize(
    "x_forwarded_for, remote_addr, expected_ip",
    [
        ("192.168.1.100, 10.0.0.1", None, "192.168.1.100"),
        ("203.0.113.45, 198.51.100.20, 192.0.2.1", None, "203.0.113.45"),
        (None, "192.168.1.50", "192.168.1.50"),
        ("", "10.0.0.5", "10.0.0.5"),
    ]
)
def test_get_ip_from_request(
    mocker: MockerFixture,
    x_forwarded_for: str | None,
    remote_addr: str | None,
    expected_ip: str,
) -> None:
    mock_request = mocker.MagicMock()
    mock_request.META = {
        "HTTP_X_FORWARDED_FOR": x_forwarded_for,
        "REMOTE_ADDR": remote_addr,
    }
    
    assert get_ip_from_request(mock_request) == expected_ip


def test_calculate_used_disk_space_with_jobs(
    mocker: MockerFixture,
) -> None:

    mock_user = mocker.MagicMock()
    mock_job1 = mocker.MagicMock(disk_size="1000")
    mock_job2 = mocker.MagicMock(disk_size="2000")
    mock_job3 = mocker.MagicMock(disk_size="3000")
    mock_user.get_jobs.return_value = [mock_job1, mock_job2, mock_job3]

    assert calculate_used_disk_space(mock_user) == 6000


def test_calculate_used_disk_space_no_jobs(
    mocker: MockerFixture,
) -> None:

    mock_user = mocker.MagicMock()
    mock_user.get_jobs.return_value = []

    assert calculate_used_disk_space(mock_user) == 0


def test_calculate_used_disk_space_single_job(
    mocker: MockerFixture,
) -> None:

    mock_user = mocker.MagicMock()
    mock_job = mocker.MagicMock(disk_size="5000")
    mock_user.get_jobs.return_value = [mock_job]

    assert calculate_used_disk_space(mock_user) == 5000


def test_calculate_used_disk_space_with_numeric_disk_sizes(
    mocker: MockerFixture,
) -> None:

    mock_user = mocker.MagicMock()
    mock_job1 = mocker.MagicMock(disk_size=1500)
    mock_job2 = mocker.MagicMock(disk_size=2500)
    mock_user.get_jobs.return_value = [mock_job1, mock_job2]

    assert calculate_used_disk_space(mock_user) == 4000

