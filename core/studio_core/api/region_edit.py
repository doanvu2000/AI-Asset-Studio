"""Sửa cục bộ: POST /api/versions/{id}/region-edit starts the region-edit recipe on one version; the new version
records that version as its parent."""
from __future__ import annotations

import base64
import binascii
import io

from fastapi import APIRouter, Depends, Request
from PIL import Image
from pydantic import BaseModel, Field
from starlette.responses import Response

from studio_core.api.auth import require_token
from studio_core.api.errors import ApiError, OrjsonResponse

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
RECIPE = "region-edit"
MAX_MASK = 20 << 20


class RegionEditBody(BaseModel):
    mask: str = Field(min_length=8, description="base64 PNG, white = edit")
    prompt: str = Field(min_length=1, max_length=1000)
    feather: int = Field(3, ge=0, le=32)


def _decode_mask(data: str) -> bytes:
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > MAX_MASK:
            raise ValueError("mask too large")
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
    except (binascii.Error, ValueError, OSError) as e:
        raise ApiError(422, "bad_mask", f"mask is not a PNG image: {e}") from e
    return raw


@router.post("/versions/{version_id}/region-edit")
async def region_edit(request: Request, version_id: str, body: RegionEditBody) -> Response:
    st = request.app.state
    v = await st.repo.get_version(version_id)
    if v is None:
        raise ApiError(404, "not_found", f"unknown version: {version_id}")
    if not (v.blob.mime or "").startswith("image/"):
        raise ApiError(422, "not_image", "only image versions can be edited by region")
    mask = await st.blobs.put_bytes(_decode_mask(body.mask), name="region_mask.png", mime="image/png")
    try:
        run_id = await st.engine.start_run(
            RECIPE, {"image": v.blob.hash, "mask": mask.hash, "prompt": body.prompt.strip(), "feather": body.feather},
            {"$meta": {"parent_version": v.id}}, project_id=v.project_id, title=v.asset_name)
    except LookupError as e:
        raise ApiError(404, "not_found", str(e.args[0]) if e.args else "not found") from e
    except ValueError as e:
        raise ApiError(400, "invalid", str(e)) from e
    return OrjsonResponse({"run_id": run_id, "parent_version": v.id}, status_code=201)
