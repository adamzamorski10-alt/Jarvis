"""
Test configuration and shared fixtures.
Uses an in-memory SQLite database for isolation.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.database import Base, get_db
from app.services.settings_service import SettingsService
from app.services.activity_service import ActivityService


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a fresh in-memory engine per test."""
    eng = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with eng.begin() as conn:
        # Import models to register them
        from app.db.models import settings, activity, conversation, permission_grant, audit_event, pending_clarification  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def session(engine):
    """Provide a fresh async session per test."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture(scope="function")
async def session_factory(engine):
    """Session factory bound to the isolated in-memory test engine."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autocommit=False)


@pytest_asyncio.fixture(scope="function")
async def client(engine):
    """Provide an AsyncClient wired to a test DB with authentication."""
    from app.main import app
    from app.identity.session import SessionStore
    from app.identity.bootstrap import BootstrapSecret

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autocommit=False)

    async def override_get_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Seed defaults for tests that need them
    async with factory() as s:
        svc = SettingsService(s)
        await svc.ensure_defaults()
        await s.commit()

    # Set up test identity/session
    test_session_store = SessionStore()
    test_bootstrap_secret = BootstrapSecret()
    app.state.session_store = test_session_store
    app.state.bootstrap_secret = test_bootstrap_secret

    # Create a test session directly in the store
    test_session = await test_session_store.create(user_id="test_user")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Include auth header in all requests
        ac.headers["Authorization"] = f"Bearer {test_session.session_id}"
        yield ac

    app.dependency_overrides.clear()
    # Clean up session store state
    app.state.session_store = None
    app.state.bootstrap_secret = None


@pytest_asyncio.fixture
async def settings_service(session):
    svc = SettingsService(session)
    await svc.ensure_defaults()
    await session.commit()
    return svc


@pytest_asyncio.fixture
async def activity_service(session):
    return ActivityService(session)
