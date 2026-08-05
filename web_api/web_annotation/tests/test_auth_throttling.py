# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
from types import SimpleNamespace

import pytest
import pytest_mock
from django.test import Client
from pytest_django.fixtures import SettingsWrapper

from web_annotation import settings_e2e
from web_annotation.throttling import (
    AccountConfirmRateThrottle,
    LoginIdentifierRateThrottle,
    LoginRateThrottle,
    PasswordResetIdentifierRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
)

LOGIN = "/api/login"
REGISTER = "/api/register"
FORGOTTEN_PASSWORD = "/api/forgotten_password"  # noqa: S105
CONFIRM_ACCOUNT = "/api/confirm_account?code=no-such-code"
RESET_PASSWORD = "/api/reset_password?code=no-such-code"  # noqa: S105


def _login(client: Client, email: str, ip: str = "10.0.0.1") -> int:
    response = client.post(
        LOGIN,
        {"email": email, "password": "not-the-password"},
        content_type="application/json",
        REMOTE_ADDR=ip,
    )
    return int(response.status_code)


def _register(client: Client, email: str, ip: str = "10.0.0.1") -> int:
    response = client.post(
        REGISTER,
        {"email": email, "password": "a-fresh-password"},
        content_type="application/json",
        REMOTE_ADDR=ip,
    )
    return int(response.status_code)


def _forgotten_password(
    client: Client, email: str, ip: str = "10.0.0.1",
) -> int:
    response = client.post(
        FORGOTTEN_PASSWORD, {"email": email}, REMOTE_ADDR=ip)
    return int(response.status_code)


def _anonymous_request(
    *, ip: str = "10.0.0.1", x_forwarded_for: str | None = None,
) -> SimpleNamespace:
    """A stand-in request exposing only what an IP-keyed throttle reads."""
    meta = {"REMOTE_ADDR": ip}
    if x_forwarded_for is not None:
        meta["HTTP_X_FORWARDED_FOR"] = x_forwarded_for
    return SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False),
        session=SimpleNamespace(session_key=None),
        META=meta,
    )


def test_ip_axis_keys_on_the_shared_client_ip_helper() -> None:
    """The throttle bucket and the anonymous quota must read one address.

    With no trusted-hop count configured, ``get_ip_from_request`` takes the
    leftmost entry of ``X-Forwarded-For`` while DRF's own ``get_ident`` keys
    on the whole header -- so two requests that differ only in a later entry
    land in one bucket here and in two under DRF. That is the discriminator:
    if this passes, the throttle is going through the shared helper
    (gain#667) rather than re-deriving the address.
    """
    throttle = LoginRateThrottle()

    key_a = throttle.get_cache_key(
        _anonymous_request(x_forwarded_for="203.0.113.5, 10.0.0.1"),
        view=None)
    key_b = throttle.get_cache_key(
        _anonymous_request(x_forwarded_for="203.0.113.5, 10.0.0.2"),
        view=None)

    assert key_a == key_b
    assert key_a == "throttle_auth_login_203.0.113.5"


@pytest.mark.django_db
def test_login_is_rate_limited_per_client_ip(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Credential stuffing from one host must stop being served.

    The rate is patched down rather than exercised at its configured value --
    see ``test_pipelines.test_validate_is_rate_limited`` for why the patch
    goes on ``THROTTLE_RATES`` and not through the settings fixture.
    """
    mocker.patch.dict(
        LoginRateThrottle.THROTTLE_RATES, {"auth_login": "2/minute"},
    )

    codes = [_login(anonymous_client, "user@example.com") for _ in range(3)]

    assert codes == [400, 400, 429]


@pytest.mark.django_db
def test_login_is_rate_limited_per_submitted_email_across_ips(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """One account sprayed from many hosts must stop being served.

    Every request here comes from a different address, so the per-IP axis
    never fires -- only a bucket keyed on the submitted address can refuse
    them.
    """
    mocker.patch.dict(
        LoginIdentifierRateThrottle.THROTTLE_RATES,
        {"auth_login_identifier": "2/minute"},
    )

    codes = [
        _login(anonymous_client, "user@example.com", ip=f"203.0.113.{host}")
        for host in (1, 2, 3)
    ]

    assert codes == [400, 400, 429]


@pytest.mark.django_db
def test_a_login_refused_by_the_ip_axis_does_not_charge_the_identifier(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A refused request must not spend the victim's identifier budget.

    The two axes are only safe together if the generous one cannot be
    emptied from behind the tight one. If a request the per-IP bucket
    already refused still counts against the per-address bucket, one host
    can keep a named account's bucket permanently full -- ten of its
    requests a minute are served and the other twenty are 429s that still
    charge -- and the real owner is refused login from every address. That
    is the lockout denial-of-service the generous identifier rate exists to
    avoid.
    """
    mocker.patch.dict(
        LoginRateThrottle.THROTTLE_RATES,
        {"auth_login": "1/minute", "auth_login_identifier": "3/minute"},
    )

    attacker = [
        _login(anonymous_client, "user@example.com", ip="198.51.100.9")
        for _ in range(3)
    ]
    victim = _login(anonymous_client, "user@example.com", ip="203.0.113.77")

    assert attacker == [400, 429, 429]
    assert victim == 400


@pytest.mark.django_db
def test_a_reset_refused_by_the_ip_axis_does_not_charge_the_identifier(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Same lockout, and worse: reset budgets are hourly.

    Nine POSTs an hour from one host -- three served, six already refused --
    would exhaust the per-address reset bucket and leave the owner of that
    address unable to ask for a reset from anywhere for the rest of the
    hour.
    """
    mocker.patch.dict(
        PasswordResetRateThrottle.THROTTLE_RATES,
        {
            "auth_password_reset": "1/hour",
            "auth_password_reset_identifier": "3/hour",
        },
    )

    attacker = [
        _forgotten_password(
            anonymous_client, "user@example.com", ip="198.51.100.9")
        for _ in range(3)
    ]
    victim = _forgotten_password(
        anonymous_client, "user@example.com", ip="203.0.113.77")

    assert attacker == [200, 429, 429]
    assert victim == 200


@pytest.mark.django_db
def test_the_ip_axis_binds_a_client_that_holds_a_session(
    user_client: Client,
    admin_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A session must not buy a private login bucket.

    Login authenticates before DRF checks throttles, so a request carrying a
    valid session cookie arrives at the throttle already authenticated. If
    the per-IP throttle then keys such a request by user id -- as DRF's
    UserRateThrottle does -- one host can register and log into N accounts
    and password-spray at N times the per-IP rate, none of it charged to its
    address. The identifier axis catches none of that: spraying is one
    attempt per victim address. So the two sessions here share the one
    address bucket, and the second request is refused.
    """
    mocker.patch.dict(
        LoginRateThrottle.THROTTLE_RATES, {"auth_login": "1/minute"},
    )

    first = _login(user_client, "victim-one@example.com", ip="198.51.100.7")
    second = _login(admin_client, "victim-two@example.com", ip="198.51.100.7")

    assert first == 400
    assert second == 429


@pytest.mark.django_db
def test_identifier_axis_does_not_disclose_account_existence(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The 429 boundary must fall in the same place for any address.

    If the identifier bucket only existed for real accounts, the request that
    trips 429 would answer "this address is registered" -- reintroducing the
    enumeration oracle of gain#696 through the throttle.
    """
    mocker.patch.dict(
        LoginIdentifierRateThrottle.THROTTLE_RATES,
        {"auth_login_identifier": "2/minute"},
    )

    registered = [
        _login(anonymous_client, "user@example.com", ip=f"203.0.113.{host}")
        for host in (1, 2, 3)
    ]
    unregistered = [
        _login(anonymous_client, "nobody@example.com", ip=f"203.0.113.{host}")
        for host in (1, 2, 3)
    ]

    # Anchored, so the comparison cannot pass by nothing being throttled.
    assert registered == [400, 400, 429]
    assert unregistered == registered


def test_submitted_email_is_hashed_into_the_cache_key() -> None:
    """A raw address in the key is PII in the cache, and hostile to encode."""
    throttle = LoginIdentifierRateThrottle()

    key = throttle.get_cache_key(
        SimpleNamespace(data={"email": "User@Example.com"}), view=None)

    assert key is not None
    assert "User@Example.com" not in key
    assert "user@example.com" not in key
    digest = hashlib.sha256(b"user@example.com").hexdigest()
    assert digest in key


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"password": "x"}, id="missing"),
        pytest.param({"email": "not-an-address"}, id="malformed"),
        pytest.param({"email": ""}, id="empty"),
        pytest.param({"email": ["a@b.com"]}, id="not-a-string"),
    ],
)
def test_no_usable_identifier_means_no_identifier_bucket(
    body: dict,
) -> None:
    """No key at all, rather than a bucket shared by unrelated clients."""
    throttle = LoginIdentifierRateThrottle()

    assert throttle.get_cache_key(
        SimpleNamespace(data=body), view=None) is None


@pytest.mark.django_db
def test_login_without_an_email_still_answers_the_view_400(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The identifier axis must not turn a bad request into a 429.

    Its rate is driven to a single request, so an identifier bucket keyed on
    "missing" would refuse the second call instead of letting the view refuse
    the body.
    """
    mocker.patch.dict(
        LoginIdentifierRateThrottle.THROTTLE_RATES,
        {"auth_login_identifier": "1/minute"},
    )

    codes = [
        int(anonymous_client.post(
            LOGIN, {"password": "x"}, content_type="application/json",
        ).status_code)
        for _ in range(3)
    ]

    assert codes == [400, 400, 400]


@pytest.mark.django_db
def test_register_is_rate_limited_per_client_ip(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Every served registration creates a user and sends a mail."""
    mocker.patch.dict(
        RegisterRateThrottle.THROTTLE_RATES, {"auth_register": "2/hour"},
    )

    codes = [
        _register(anonymous_client, f"fresh-{n}@example.com")
        for n in (1, 2, 3)
    ]

    assert codes == [200, 200, 429]


@pytest.mark.django_db
def test_forgotten_password_is_rate_limited_per_client_ip(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Each served request sends a mail through the production relay."""
    mocker.patch.dict(
        PasswordResetRateThrottle.THROTTLE_RATES,
        {"auth_password_reset": "2/hour"},
    )

    codes = [
        _forgotten_password(anonymous_client, "user@example.com")
        for _ in range(3)
    ]

    assert codes == [200, 200, 429]


@pytest.mark.django_db
def test_rendering_the_reset_form_does_not_spend_the_mail_budget(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A page render must not lock a user out of resetting their password.

    The login page links to this endpoint, so the GET is an ordinary browser
    navigation. Charging it against a three-an-hour budget would mean three
    clicks on "Forgotten password" -- or one typo and a reload -- cost the
    user the reset for an hour, with no mail ever sent. The rate here is
    driven below the number of renders so that a throttle counting them
    could not pass.
    """
    mocker.patch.dict(
        PasswordResetRateThrottle.THROTTLE_RATES,
        {"auth_password_reset": "2/hour"},
    )

    renders = [
        int(anonymous_client.get(FORGOTTEN_PASSWORD).status_code)
        for _ in range(4)
    ]
    # The POST budget is untouched by those renders: two mails, then 429.
    posts = [
        _forgotten_password(anonymous_client, "user@example.com")
        for _ in range(3)
    ]

    assert renders == [200, 200, 200, 200]
    assert posts == [200, 200, 429]


@pytest.mark.django_db
def test_forgotten_password_is_rate_limited_per_email_across_ips(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """One mailbox must not be floodable by rotating the source address."""
    mocker.patch.dict(
        PasswordResetIdentifierRateThrottle.THROTTLE_RATES,
        {"auth_password_reset_identifier": "2/hour"},
    )

    codes = [
        _forgotten_password(
            anonymous_client, "user@example.com", ip=f"203.0.113.{host}")
        for host in (1, 2, 3)
    ]

    assert codes == [200, 200, 429]


@pytest.mark.django_db
def test_confirm_account_is_rate_limited_per_client_ip(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The traffic that matters on a code-redeeming endpoint is guessing."""
    mocker.patch.dict(
        AccountConfirmRateThrottle.THROTTLE_RATES, {"auth_confirm": "2/hour"},
    )

    codes = [
        int(anonymous_client.get(CONFIRM_ACCOUNT).status_code)
        for _ in range(3)
    ]

    assert codes == [302, 302, 429]


@pytest.mark.django_db
def test_reset_password_is_rate_limited_per_client_ip(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.dict(
        AccountConfirmRateThrottle.THROTTLE_RATES, {"auth_confirm": "2/hour"},
    )

    codes = [
        int(anonymous_client.get(RESET_PASSWORD).status_code)
        for _ in range(3)
    ]

    assert codes == [400, 400, 429]


@pytest.mark.django_db
def test_confirm_account_and_reset_password_share_one_bucket(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Same activity, same code space -- deliberately one budget.

    Splitting them would double what a guesser is allowed to spend against
    the codes without buying either endpoint anything.
    """
    mocker.patch.dict(
        AccountConfirmRateThrottle.THROTTLE_RATES, {"auth_confirm": "2/hour"},
    )

    anonymous_client.get(CONFIRM_ACCOUNT)
    anonymous_client.get(CONFIRM_ACCOUNT)
    spillover = anonymous_client.get(RESET_PASSWORD)

    assert spillover.status_code == 429


@pytest.mark.django_db
def test_exhausting_login_does_not_throttle_registration(
    anonymous_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Endpoint families must not draw on one bucket.

    Login is budgeted for a human mistyping a password, registration for an
    outbound mail. Sharing a bucket means a login flood locks new users out
    of registering -- and the reverse.
    """
    mocker.patch.dict(
        LoginRateThrottle.THROTTLE_RATES,
        {"auth_login": "1/minute", "auth_register": "1/hour"},
    )

    first_login = _login(anonymous_client, "user@example.com")
    exhausted_login = _login(anonymous_client, "user@example.com")
    register = _register(anonymous_client, "fresh@example.com")

    assert first_login == 400
    assert exhausted_login == 429
    assert register == 200


def test_identifier_axis_is_disabled_under_the_e2e_flag(
    settings: SettingsWrapper,
) -> None:
    """Session-scoping cannot isolate an identifier-keyed bucket.

    The IP axis is session-scoped under e2e (gain#179) so each Playwright
    test gets its own budget, but a bucket keyed on the submitted address is
    shared by every test that logs in as the same seeded account -- they
    would cross-exhaust it. Under the flag the axis returns no key at all.
    """
    settings.E2E_DISABLE_IDENTIFIER_THROTTLE = True
    throttle = LoginIdentifierRateThrottle()

    key = throttle.get_cache_key(
        SimpleNamespace(data={"email": "user@example.com"}), view=None)

    assert key is None


@pytest.mark.django_db
def test_an_unparseable_body_is_answered_by_the_view_not_the_throttle(
    anonymous_client: Client,
) -> None:
    """Reading the body in a throttle must not turn a 400 into a 500.

    The identifier axis has to look inside the request to find an address,
    which means it meets bodies that will not parse before the view does.
    """
    response = anonymous_client.post(
        LOGIN, "{not json", content_type="application/json")

    assert response.status_code == 400


def test_settings_e2e_disables_the_identifier_axis() -> None:
    # The flag is only ever true under settings_e2e (the module the e2e
    # daphne server runs under); pytest runs under test_settings, so assert
    # on the settings_e2e module object directly.
    assert settings_e2e.E2E_DISABLE_IDENTIFIER_THROTTLE is True
