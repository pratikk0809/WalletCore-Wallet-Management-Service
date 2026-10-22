"""The architecture document exists, its diagrams are well-formed, and the README links to it."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "ARCHITECTURE.md"
DIAGRAM_TYPES = {"flowchart", "sequenceDiagram", "erDiagram"}


def mermaid_blocks() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    return re.findall(r"```mermaid\n(.*?)```", text, flags=re.DOTALL)


def test_the_architecture_document_has_all_five_diagrams():
    assert DOC.exists()

    assert len(mermaid_blocks()) == 5


def test_every_diagram_starts_with_a_known_diagram_type():
    for block in mermaid_blocks():
        first_line = block.strip().splitlines()[0]
        assert first_line.split()[0] in DIAGRAM_TYPES, first_line


def test_the_readme_links_to_the_architecture_document():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/ARCHITECTURE.md" in readme
