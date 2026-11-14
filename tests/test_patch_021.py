"""0.2.1 patch: republish with the corrected README.

The 0.2.0 files on PyPI were built from the pre-R1 tree, so their
long description showed the bare `uvx roundtable-mcp` form (which
only prints usage) and "197 tests pass". This test pins the patch:
the version moved to 0.2.1 everywhere it is stated, and the README
on this tree is the one the next upload will carry.
"""

import tomllib
from pathlib import Path

import roundtable

ROOT = Path(__file__).resolve().parent.parent


def test_021_patch_release():
    with open(ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    assert pyproject["project"]["version"] == "0.2.1"
    assert roundtable.__version__ == "0.2.1"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.2.1" in changelog
    assert "v0.2.1" in changelog
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uvx --from roundtable-mcp roundtable" in readme
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "25. (0.2.1)" in spec
    assert "0.2.1" in spec
