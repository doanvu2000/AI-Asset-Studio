from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from ..contracts import BlobRef

AUTO_PICK_KEY = "auto_pick_qa"

WARN_NO_SCORE = "Không ảnh nào có điểm QA, đã lấy ảnh đầu tiên."
WARN_PARTIAL = "Một số ảnh chưa có điểm QA, chỉ so sánh các ảnh đã chấm."

Scorer = Callable[[BlobRef], Awaitable[float | None]]


async def pick_by_qa(candidates: Sequence[BlobRef], k: int, score: Scorer) -> tuple[list[str], dict[str, float], str | None]:
    """Top-k candidates by QA score -> (hashes, scores, warning). The sort is stable, so ties (and the
    no-score case) keep the order the gate shows, i.e. the first candidates win."""
    k = max(1, k)
    scores: dict[str, float] = {}
    for b in candidates:
        s = await score(b)
        if s is not None:
            scores[b.hash] = s
    order = sorted(candidates, key=lambda b: -scores.get(b.hash, float("-inf")))
    picked = [b.hash for b in order[:k]]
    if not scores:
        return picked, scores, WARN_NO_SCORE
    return picked, scores, WARN_PARTIAL if len(scores) < len(candidates) else None
