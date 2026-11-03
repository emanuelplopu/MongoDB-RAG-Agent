"""Tests for per-tenant JWT signing keys and the tenant claim in login tokens.

Covers:
- ``validate_jwt_secret`` refuses missing, public and short keys where a key
  is required, and never falls back to the public default elsewhere.
- the API lifespan aborts startup without a key instead of serving sessions.
- docker-compose.yml forwards a different key variable to each tenant backend.
- tokens carry ``tid`` and a backend rejects tokens of another tenant, legacy
  tokens without ``tid``, foreign signatures and expired tokens.
- API-key authentication keeps working without any token.
"""

import importlib
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

# Imported before the fixtures clean the environment: importing the app runs
# load_dotenv() (via src.ingestion), which may put a JWT_SECRET_KEY from a
# nearby .env into os.environ.
import backend.main as main_module
from backend.core.config import settings
from backend.core.security import (
    MIN_JWT_SECRET_LENGTH,
    PUBLIC_JWT_SECRETS,
    InsecureJwtSecretError,
    get_jwt_secret,
    jwt_secret_required,
    reset_jwt_secret,
    validate_jwt_secret,
)
from backend.routers import auth as auth_module

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DEFAULT = "recallhub-secret-key-change-in-production"
GOOD_SECRET = "a" * 64
OTHER_SECRET = "b" * 64


def _load_real_jose() -> tuple[ModuleType, type]:
    """Import the installed python-jose although conftest stubs ``jose``.

    The stub stays registered for every other test; only the returned module
    objects point at the real implementation.
    """
    stub_modules = {name: sys.modules.pop(name) for name in list(sys.modules) if name == "jose" or name.startswith("jose.")}
    try:
        real_jwt = importlib.import_module("jose.jwt")
        real_error = importlib.import_module("jose").JWTError
    except ImportError:  # pragma: no cover - jose is a backend dependency
        pytest.skip("python-jose is not installed")
    finally:
        for name in [n for n in sys.modules if n == "jose" or n.startswith("jose.")]:
            sys.modules.pop(name)
        sys.modules.update(stub_modules)
    return real_jwt, real_error


REAL_JWT, REAL_JWT_ERROR = _load_real_jose()


@pytest.fixture(autouse=True)
def _clean_jwt_env(monkeypatch):
    """Start every test without signing-key settings and with an empty key cache."""
    for name in ("JWT_SECRET_KEY", "JWT_SECRET_REQUIRED", "APP_ENV", "TENANT_ID"):
        monkeypatch.delenv(name, raising=False)
    reset_jwt_secret()
    yield
    reset_jwt_secret()


@pytest.fixture
def real_jose(monkeypatch):
    """Let the auth router sign and verify with the real python-jose."""
    monkeypatch.setattr(auth_module, "jwt", REAL_JWT)
    monkeypatch.setattr(auth_module, "JWTError", REAL_JWT_ERROR)


def _use_secret(monkeypatch, secret: str) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    reset_jwt_secret()


class TestSecretRequired:
    def test_off_by_default(self):
        assert jwt_secret_required() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "ON"])
    def test_jwt_secret_required_flag(self, monkeypatch, value: str):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", value)
        assert jwt_secret_required() is True

    def test_production_requires_a_key(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        assert jwt_secret_required() is True


class TestValidateJwtSecret:
    @pytest.mark.parametrize("secret", ["", "   ", PUBLIC_DEFAULT], ids=["empty", "blank", "public-default"])
    def test_required_rejects_missing_or_public_key(self, monkeypatch, secret: str):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", "true")
        monkeypatch.setenv("JWT_SECRET_KEY", secret)
        with pytest.raises(InsecureJwtSecretError, match="JWT_SECRET_KEY must be set"):
            validate_jwt_secret()

    def test_required_rejects_short_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", "true")
        monkeypatch.setenv("JWT_SECRET_KEY", "x" * (MIN_JWT_SECRET_LENGTH - 1))
        with pytest.raises(InsecureJwtSecretError, match="at least"):
            validate_jwt_secret()

    def test_error_names_the_tenant_variable_but_not_the_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", "true")
        monkeypatch.setenv("TENANT_ID", "quellex")
        monkeypatch.setenv("JWT_SECRET_KEY", PUBLIC_DEFAULT)
        with pytest.raises(InsecureJwtSecretError) as exc:
            validate_jwt_secret()
        assert "QUELLEX_JWT_SECRET_KEY" in str(exc.value)
        assert PUBLIC_DEFAULT not in str(exc.value)

    def test_required_accepts_own_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", "true")
        monkeypatch.setenv("JWT_SECRET_KEY", f"  {GOOD_SECRET}  ")
        assert validate_jwt_secret() == GOOD_SECRET

    @pytest.mark.parametrize("secret", ["", PUBLIC_DEFAULT], ids=["empty", "public-default"])
    def test_development_never_uses_the_public_default(self, monkeypatch, secret: str):
        monkeypatch.setenv("JWT_SECRET_KEY", secret)
        first = validate_jwt_secret()
        second = validate_jwt_secret()
        assert first not in PUBLIC_JWT_SECRETS
        assert re.fullmatch(r"[0-9a-f]{64}", first)
        assert first != second  # random per process, not a fixed fallback

    def test_get_jwt_secret_is_cached_until_reset(self, monkeypatch):
        _use_secret(monkeypatch, GOOD_SECRET)
        assert get_jwt_secret() == GOOD_SECRET
        monkeypatch.setenv("JWT_SECRET_KEY", OTHER_SECRET)
        assert get_jwt_secret() == GOOD_SECRET
        reset_jwt_secret()
        assert get_jwt_secret() == OTHER_SECRET

    def test_public_default_only_survives_as_a_blocklist_entry(self):
        holders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "backend").rglob("*.py")
            if "tests" not in path.parts and PUBLIC_DEFAULT in path.read_text(encoding="utf-8")
        ]
        assert holders == ["backend/core/security.py"]
        assert PUBLIC_DEFAULT in PUBLIC_JWT_SECRETS


class TestLifespanFailsFast:
    async def test_startup_aborts_without_a_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_REQUIRED", "true")
        database = MagicMock(side_effect=AssertionError("database must not be touched"))
        monkeypatch.setattr(main_module, "DatabaseManager", database)

        with pytest.raises(InsecureJwtSecretError):
            async with main_module.lifespan(main_module.app):
                pass  # pragma: no cover - startup must not get here
        database.assert_not_called()


class TestComposeForwarding:
    @pytest.fixture(scope="class")
    def services(self) -> dict[str, Any]:
        compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        return compose["services"]

    @staticmethod
    def _env(service: dict[str, Any]) -> dict[str, str]:
        entries = service.get("environment") or []
        if isinstance(entries, dict):
            return {key: str(value) for key, value in entries.items()}
        return dict(entry.split("=", 1) for entry in entries)

    @pytest.mark.parametrize(
        ("service", "variable"),
        [("backend", "RECALLHUB_JWT_SECRET_KEY"), ("backend-quellex", "QUELLEX_JWT_SECRET_KEY")],
    )
    def test_each_backend_gets_its_tenant_key(self, services, service: str, variable: str):
        env = self._env(services[service])
        assert env["JWT_SECRET_KEY"] == "${" + variable + ":-}"
        assert env["JWT_SECRET_REQUIRED"] == "true"

    def test_tenants_use_different_variables(self, services):
        recallhub = self._env(services["backend"])["JWT_SECRET_KEY"]
        quellex = self._env(services["backend-quellex"])["JWT_SECRET_KEY"]
        assert recallhub != quellex

    def test_no_service_carries_a_literal_key(self, services):
        for name, service in services.items():
            value = self._env(service).get("JWT_SECRET_KEY")
            if value is not None:
                assert value.startswith("${"), f"{name} hard-codes JWT_SECRET_KEY"


class TestTenantClaim:
    def _login_token(self, monkeypatch, tenant: str, user_id: str = "user-1") -> str:
        monkeypatch.setattr(settings, "tenant_id", tenant)
        return auth_module.create_access_token({"sub": user_id})

    def test_token_carries_tenant_and_round_trips(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        token = self._login_token(monkeypatch, "quellex")

        claims = auth_module.decode_access_token(token)
        assert claims is not None
        assert claims["sub"] == "user-1"
        assert claims[auth_module.TENANT_CLAIM] == "quellex"

    def test_other_tenant_rejects_token_even_with_the_same_key(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        token = self._login_token(monkeypatch, "recallhub")
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        assert auth_module.decode_access_token(token) is None

    def test_legacy_token_without_tenant_is_rejected(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        legacy = REAL_JWT.encode(
            {"sub": "user-1", "exp": datetime.utcnow() + timedelta(hours=1)}, GOOD_SECRET, algorithm="HS256"
        )
        assert auth_module.decode_access_token(legacy) is None

    def test_token_signed_with_another_key_is_rejected(self, monkeypatch, real_jose):
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        forged = REAL_JWT.encode(
            {"sub": "user-1", "tid": "quellex", "exp": datetime.utcnow() + timedelta(hours=1)},
            PUBLIC_DEFAULT,
            algorithm="HS256",
        )
        _use_secret(monkeypatch, GOOD_SECRET)
        assert auth_module.decode_access_token(forged) is None

    def test_expired_token_is_rejected(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        token = auth_module.create_access_token({"sub": "user-1"}, expires_delta=timedelta(seconds=-5))
        assert auth_module.decode_access_token(token) is None


def _request_with(users: Optional[dict[str, Any]] = None, api_key_doc: Optional[dict[str, Any]] = None) -> MagicMock:
    """A request whose app.state.db.db holds ``users`` and ``api_keys`` mocks."""
    users_collection = MagicMock()
    users_collection.find_one = AsyncMock(side_effect=lambda query: (users or {}).get(query.get("_id")))
    api_keys_collection = MagicMock()
    api_keys_collection.find_one = AsyncMock(return_value=api_key_doc)
    api_keys_collection.update_one = AsyncMock()
    request = MagicMock()
    request.app.state.db.db = {"users": users_collection, "api_keys": api_keys_collection}
    return request


USER_DOC = {
    "_id": "user-1",
    "email": "anna@example.com",
    "name": "Anna",
    "created_at": datetime(2026, 1, 1),
    "is_active": True,
}


class TestGetCurrentUser:
    async def test_valid_token_of_this_tenant_resolves_the_user(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        token = auth_module.create_access_token({"sub": "user-1"})
        credentials = MagicMock(credentials=token)

        user = await auth_module.get_current_user(_request_with({"user-1": USER_DOC}), credentials, None)
        assert user is not None and user.id == "user-1"

    async def test_token_of_another_tenant_resolves_nobody(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        monkeypatch.setattr(settings, "tenant_id", "recallhub")
        token = auth_module.create_access_token({"sub": "user-1"})
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        credentials = MagicMock(credentials=token)

        assert await auth_module.get_current_user(_request_with({"user-1": USER_DOC}), credentials, None) is None

    async def test_api_key_still_authenticates(self, monkeypatch, real_jose):
        _use_secret(monkeypatch, GOOD_SECRET)
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        api_key = "rag_" + "c" * 36
        key_doc = {
            "_id": "key-1",
            "key_hash": auth_module.hash_api_key(api_key),
            "user_id": "user-1",
            "is_active": True,
            "expires_at": None,
        }
        request = _request_with({"user-1": USER_DOC}, api_key_doc=key_doc)

        user = await auth_module.get_current_user(request, None, api_key)
        assert user is not None and user.id == "user-1"
        request.app.state.db.db["api_keys"].update_one.assert_awaited_once()
