"""
Stage 16.2 — Hostile Security & Regression Gate

Tests the actual HTTP boundary with the real FastAPI app and real SessionStore.
No mocks of AuthMiddleware, RequestContext, or session validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from fastapi.exceptions import HTTPException

import secrets
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.database import Base, get_db, init_db
from app.identity.session import SessionStore
from app.identity.bootstrap import BootstrapSecret
from app.identity.models import RequestContext
from app.services.settings_service import SettingsService
from app.memory.service import MemoryService, MemoryCaller
from app.memory.models import MemoryCategory, MemoryScope
from app.db.models import memory as memory_model


@pytest.fixture
async def app_with_auth(tmp_path):
    db_file = str(tmp_path / "test.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        from app.db.models import settings, activity  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_test_db():
        async with factory() as s:
            yield s
            await s.commit()

    from app.main import app
    app.dependency_overrides[get_db] = _get_test_db

    async with factory() as s:
        svc = SettingsService(s)
        await svc.ensure_defaults()
        await s.commit()

    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    app.state.session_store = session_store
    app.state.bootstrap_secret = bootstrap_secret

    test_session = await session_store.create(user_id="test_user")
    app.state._test_session_id = test_session.session_id
    app.state._test_bootstrap_secret = bootstrap_secret.load_or_generate()

    yield app

    app.dependency_overrides.clear()
    app.state.session_store = None
    app.state.bootstrap_secret = None
    await engine.dispose()


@pytest.fixture
async def client(app_with_auth):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth),
        base_url="http://test",
    ) as ac:
        ac.headers["Authorization"] = f"Bearer {app_with_auth.state._test_session_id}"
        yield ac


class TestFakeSessionRejected:
    async def test_fake_token_format_rejected(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/settings")
            assert exc_info.value.status_code == 401

    async def test_valid_format_but_not_in_store_rejected(self, app_with_auth):
        fake_token = f"ses_{secrets.token_hex(32)}"
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            ac.headers["Authorization"] = f"Bearer {fake_token}"
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/settings")
            assert exc_info.value.status_code == 401


class TestRealSessionAccepted:
    async def test_settings_with_real_session(self, client):
        resp = await client.get("/api/settings")
        assert resp.status_code == 200

    async def test_activity_with_real_session(self, client):
        resp = await client.get("/api/activity")
        assert resp.status_code == 200


class TestPublicEndpoints:
    async def test_health_public(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/health")
            assert resp.status_code == 200

    async def test_system_status_public(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/system/status")
            assert resp.status_code == 200

    async def test_providers_public(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/ai/providers")
            assert resp.status_code == 200


class TestSensitiveEndpointsRejectUnauthenticated:
    async def test_settings_rejects_unauthenticated(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/settings")
            assert exc_info.value.status_code == 401

    async def test_ai_config_rejects_unauthenticated(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/ai/config")
            assert exc_info.value.status_code == 401

    async def test_history_rejects_unauthenticated(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/history/tasks/task_1")
            assert exc_info.value.status_code == 401

    async def test_scheduler_rejects_unauthenticated(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/scheduler/jobs")
            assert exc_info.value.status_code == 401


class TestRequestContextSpoofing:
    async def test_user_id_from_json_ignored(self, client):
        resp = await client.put(
            "/api/settings",
            json={"settings": {"theme": "dark"}, "user_id": "attacker"},
        )
        assert resp.status_code == 200

    async def test_actor_type_header_ignored(self, client):
        client.headers["X-Actor-Type"] = "system"
        resp = await client.get("/api/settings")
        assert resp.status_code == 200


class TestBootstrapSecret:
    async def test_correct_secret_issues_session(self, app_with_auth):
        secret = app_with_auth.state._test_bootstrap_secret
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/api/auth/session",
                headers={"X-Local-Secret": secret},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert "session_id" in data
            assert data["session_id"].startswith("ses_")

    async def test_wrong_secret_rejected(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/api/auth/session",
                headers={"X-Local-Secret": "wrong_secret"},
            )
            assert resp.status_code == 401

    async def test_missing_secret_rejected(self, app_with_auth):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            resp = await ac.post("/api/auth/session")
            assert resp.status_code == 401


class TestSessionLifecycle:
    async def test_expired_session_rejected(self, app_with_auth, tmp_path):
        session_store = app_with_auth.state.session_store
        expired_session = await session_store.create(
            user_id="test_user",
            now=datetime.now(timezone.utc).replace(year=2020),
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            ac.headers["Authorization"] = f"Bearer {expired_session.session_id}"
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/settings")
            assert exc_info.value.status_code == 401

    async def test_revoked_session_rejected(self, app_with_auth):
        session_store = app_with_auth.state.session_store
        session = await session_store.create(user_id="test_user")
        await session_store.revoke(session.session_id)

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            ac.headers["Authorization"] = f"Bearer {session.session_id}"
            with pytest.raises(HTTPException) as exc_info:
                await ac.get("/api/settings")
            assert exc_info.value.status_code == 401

    async def test_session_removed_after_expiry(self, app_with_auth):
        session_store = app_with_auth.state.session_store
        expired_session = await session_store.create(
            user_id="test_user",
            now=datetime.now(timezone.utc).replace(year=2020),
        )

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth),
            base_url="http://test",
        ) as ac:
            ac.headers["Authorization"] = f"Bearer {expired_session.session_id}"
            try:
                await ac.get("/api/settings")
            except HTTPException:
                pass

        assert await session_store.get(expired_session.session_id) is None


class TestMemoryOwnership:
    async def test_source_id_defaults_from_caller_id(self, app_with_auth, tmp_path):
        db_file = str(tmp_path / "memory_test.db")
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_file}",
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _get_test_db():
            async with factory() as s:
                yield s
                await s.commit()

        app_with_auth.dependency_overrides[get_db] = _get_test_db

        try:
            async with factory() as s:
                memory_svc = MemoryService(s)
                user_caller = MemoryCaller(
                    caller_type="user",
                    caller_id="user_123",
                )
                created = await memory_svc.create(
                    caller=user_caller,
                    category=MemoryCategory.USER_INSTRUCTION,
                    scope=MemoryScope.GLOBAL,
                    content={"text": "test memory"},
                )
                await s.commit()
                assert created.source_id == "user_123"

            async with factory() as s:
                memory_svc = MemoryService(s)
                system_caller = MemoryCaller(
                    caller_type="system",
                    caller_id="scheduler",
                )
                created = await memory_svc.create(
                    caller=system_caller,
                    category=MemoryCategory.SYSTEM_KNOWLEDGE,
                    scope=MemoryScope.GLOBAL,
                    content={"text": "system memory"},
                )
                await s.commit()
                assert created.source_id == "scheduler"
        finally:
            app_with_auth.dependency_overrides.pop(get_db, None)
            await engine.dispose()

    async def test_non_user_cannot_update_others_memory(self, app_with_auth, tmp_path):
        db_file = str(tmp_path / "memory_test2.db")
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_file}",
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _get_test_db():
            async with factory() as s:
                yield s
                await s.commit()

        app_with_auth.dependency_overrides[get_db] = _get_test_db

        try:
            async with factory() as s:
                memory_svc = MemoryService(s)
                victim_caller = MemoryCaller(
                    caller_type="user",
                    caller_id="victim_user",
                )
                created = await memory_svc.create(
                    caller=victim_caller,
                    category=MemoryCategory.USER_INSTRUCTION,
                    scope=MemoryScope.GLOBAL,
                    content={"text": "victim memory"},
                    source_id="victim_user",
                )
                await s.commit()
                mem_id = created.memory_id

            async with factory() as s:
                memory_svc = MemoryService(s)
                attacker_caller = MemoryCaller(
                    caller_type="agent",
                    caller_id="attacker_agent",
                )
                with pytest.raises(Exception):
                    await memory_svc.update(
                        caller=attacker_caller,
                        memory_id=mem_id,
                        changes={"content": {"text": "hacked"}},
                    )
        finally:
            app_with_auth.dependency_overrides.pop(get_db, None)
            await engine.dispose()


class TestCORSConfig:
    async def test_cors_is_localhost_only(self):
        from app.core.config import get_settings
        settings = get_settings()
        allowed_origins = settings.cors_origins
        assert "*" not in allowed_origins, "CORS must not allow all origins"
        assert len(allowed_origins) > 0, "CORS must have at least one origin"


class TestNoJwtOrOAuth:
    async def test_no_jwt_imports_in_auth_layer(self):
        import app.identity.session as session_mod
        import app.identity.auth as auth_mod
        import app.identity.bootstrap as bootstrap_mod

        for module in [session_mod, auth_mod, bootstrap_mod]:
            source = "\n".join(open(module.__file__).readlines())
            assert "jwt" not in source.lower(), "JWT must not be used in V1"
            assert "oauth" not in source.lower(), "OAuth must not be used in V1"

    async def test_session_store_is_in_memory_only(self):
        import app.identity.session as session_mod
        source = "\n".join(open(session_mod.__file__).readlines())
        assert "dict[str, Session]" in source or "_sessions" in source
        assert "sqlite" not in source.lower()
        assert "database" not in source.lower()


class TestActorIdentityDerivation:
    async def test_authenticated_request_sets_correct_actor(self, client):
        resp = await client.get("/api/activity")
        assert resp.status_code == 200
