import re
from pathlib import Path

from studio_core.engine.qa import QA_VERSION


def test_renderer_qa_version_matches_core() -> None:
    ts = Path(__file__).resolve().parents[2] / "app/src/renderer/src/composables/qa.ts"
    m = re.search(r"export const QA_VERSION = (\d+)", ts.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == QA_VERSION
