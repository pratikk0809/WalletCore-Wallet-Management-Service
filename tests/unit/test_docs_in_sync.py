"""The README and .env.example must keep up with the code (no database needed)."""

from pathlib import Path

from app.core.config import Settings
from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_every_setting_is_listed_in_env_example():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    missing = [name for name in Settings.model_fields if name.upper() not in text]

    assert not missing, f"add these to .env.example: {missing}"


def test_every_endpoint_is_listed_in_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    missing = [path for path in app.openapi()["paths"] if path not in readme]

    assert not missing, f"add these endpoints to README.md: {missing}"
