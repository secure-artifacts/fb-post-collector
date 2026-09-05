from pathlib import Path

import pytesseract
from PIL import Image

from .environment import detect_tools
from .errors import OcrError


def tessdata_dir_for(tools, languages="por"):
    langs = [part for part in str(languages or "por").split("+") if part]
    for directory in tools.get("tessdata_dirs") or []:
        path = Path(directory)
        if langs and all((path / f"{lang}.traineddata").exists() for lang in langs):
            return path
    for directory in tools.get("tessdata_dirs") or []:
        if Path(directory).exists():
            return Path(directory)
    return None


def ocr_image(image_path, languages="por"):
    tools = detect_tools()
    tesseract = tools["tesseract"]
    if not tesseract["available"]:
        raise OcrError("OCR失败", "Tesseract is not installed or bundled")
    pytesseract.pytesseract.tesseract_cmd = tesseract["path"]
    config = ""
    tessdata = tessdata_dir_for(tools, languages)
    if tessdata:
        config = f'--tessdata-dir "{tessdata}"'
    try:
        with Image.open(Path(image_path)) as image:
            return pytesseract.image_to_string(image, lang=languages, config=config).strip()
    except Exception as exc:
        raise OcrError("OCR失败", repr(exc)) from exc
