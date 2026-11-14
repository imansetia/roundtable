"""0.2.1: metadata & discoverability.

The 0.2.0 PyPI page sits next to a different, unrelated project in
search, so this release sharpens the package metadata instead of
renaming: keywords, a shorter self-hosted summary, and one pitch
line in the README.
"""

import tomllib
from pathlib import Path

import roundtable

ROOT = Path(__file__).resolve().parent.parent

KEYWORDS = ["mcp", "ai-agents", "task-board", "multi-agent", "coordination", "llm"]
PITCH = "Roundtable is a shared task board your agents claim work from — self-hosted and private by design."


def test_021_metadata():
    with open(ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    project = pyproject["project"]
    assert project["version"] == "0.2.1"
    assert roundtable.__version__ == "0.2.1"
    assert project["version"] == roundtable.__version__
    assert project["keywords"] == KEYWORDS
    summary = project["description"]
    assert len(summary) < 110
    assert "Self-hosted" in summary
    assert "Private by design" in summary
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert PITCH in readme
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.2.1" in changelog
    assert "metadata" in changelog.lower()
