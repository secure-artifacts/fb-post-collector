import mimetypes
from pathlib import Path

import requests

from .. import db
from ..config import DEFAULT_GYAZO_ACCESS_TOKEN
from .errors import UploadError


GYAZO_UPLOAD_URL = "https://upload.gyazo.com/api/upload"


def upload_file(path):
    token = db.setting_get("gyazo_access_token") or DEFAULT_GYAZO_ACCESS_TOKEN
    if not token:
        raise UploadError("Gyazo未授权", "Missing gyazo_access_token or DEFAULT_GYAZO_ACCESS_TOKEN")
    file_path = Path(path)
    if not file_path.exists():
        raise UploadError("图片上传失败", f"Missing upload file: {file_path}")
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    try:
        with file_path.open("rb") as handle:
            resp = requests.post(
                GYAZO_UPLOAD_URL,
                data={"access_token": token},
                files={"imagedata": (file_path.name, handle, mime)},
                timeout=60,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise UploadError("图片上传失败", repr(exc)) from exc
    return data.get("url") or data.get("thumb_url") or data.get("permalink_url") or ""
