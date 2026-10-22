"""The CI workflow and dependency files exist and keep their key pieces."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_workflow_runs_lint_format_and_tests_against_postgres():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for expected in ("postgres:16", "ruff check .", "black --check .", "python -m pytest"):
        assert expected in workflow, f"ci.yml no longer contains: {expected}"


def test_dev_requirements_include_the_runtime_requirements_and_the_tools():
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "-r requirements.txt" in dev
    for tool in ("pytest", "httpx2", "ruff", "black"):
        assert tool in dev
        assert tool not in runtime  # the production image stays free of test tools
