"""Regenerate the agent skill's recipe catalog from core/studio_core/recipes/*.yaml and copy the Claude skill to the
Codex integration (one SKILL.md to edit: integrations/claude-plugin/skills/asset-studio/SKILL.md).

    cd core && uv run python ../scripts/gen_agent_docs.py          # write
    cd core && uv run python ../scripts/gen_agent_docs.py --check  # CI: exit 1 when stale
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from studio_core.mcp.catalog import main  # noqa: E402

SKILL = ROOT / "integrations" / "claude-plugin" / "skills" / "asset-studio"
CODEX_SKILL = ROOT / "integrations" / "codex" / "skills" / "asset-studio"
TARGETS = [SKILL / "references" / "recipes.md", CODEX_SKILL / "references" / "recipes.md"]


def sync_skill(check: bool) -> int:
    src, dst = SKILL / "SKILL.md", CODEX_SKILL / "SKILL.md"
    text = src.read_text(encoding="utf-8")
    if dst.is_file() and dst.read_text(encoding="utf-8") == text:
        return 0
    if check:
        print(f"out of date: {dst}", file=sys.stderr)
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    rc = main([*map(str, TARGETS), *sys.argv[1:]])
    raise SystemExit(rc | sync_skill(check))
