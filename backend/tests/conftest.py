"""Shared test fixtures."""

import os
import pytest
from sqlmodel import SQLModel, Session, create_engine
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

# Set test env vars before any app imports
os.environ["APP_ENV"] = "dev"
os.environ["DEV_AUTH_BYPASS"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["KB_REPO_LOCAL_PATH"] = "./kb_data"
os.environ["KB_REPO_LOCAL_ONLY"] = "true"
os.environ["AZURE_OPENAI_ENDPOINT"] = "https://test.openai.azure.com/"
os.environ["AZURE_OPENAI_API_KEY"] = "test-key"
os.environ["ENTRA_TENANT_ID"] = "test-tenant"
os.environ["ENTRA_API_CLIENT_ID"] = "test-client"
os.environ["ENTRA_API_AUDIENCE"] = "api://test-client"
# Tests run with all phases unlocked so existing test fixtures keep working.
# The phase-gating system itself is exercised by tests/test_phase_gates.py,
# which overrides this via monkeypatch as needed.
os.environ["NEXUS_PHASE"] = "3"

# Pin spend-cap config so tests don't inherit the developer's real .env (which
# may set a tiny cap for local "watch it block" testing). Individual tests
# monkeypatch these as needed (enforcement, disabled-endpoint).
os.environ["USAGE_CAP_ENABLED"] = "true"
os.environ["USAGE_CAP_ENFORCED"] = "false"
os.environ["USAGE_WEEKLY_CAP_USD_DEFAULT"] = "20.0"


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset the Azure OpenAI circuit breaker state before each test.

    The circuit breaker uses module-level variables that persist across tests
    in the same process.  Tests that exercise the 'failing client' path would
    otherwise open the circuit and contaminate subsequent tests.
    """
    from app.agent import circuit_breaker
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
async def client():
    """Create an async test client with full app lifespan."""
    from app.main import app
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


AUTH_HEADERS = {"Authorization": "Bearer fake"}
