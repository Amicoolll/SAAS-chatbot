"""Load local .env before importing app settings (DATABASE_URL for integration tests)."""

from pathlib import Path

_root = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env", override=False)
except ImportError:
    pass


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: tests that need a live Postgres with pgvector (see DATABASE_URL)",
    )
