from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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
    tessdata_config = ""
    tessdata = tessdata_dir_for(tools, languages)
    if tessdata:
        tessdata_config = f'--tessdata-dir "{tessdata}"'
    try:
        with Image.open(Path(image_path)) as image:
            # Facebook 图片经常是低分辨率缩略图或视频帧。先纠正 EXIF 方向，
            # 原图识别不到足够文字时，再用放大、灰度、高对比度版本补跑一次。
            original = ImageOps.exif_transpose(image).convert("RGB")
            results = [
                pytesseract.image_to_string(
                    original,
                    lang=languages,
                    config=f"{tessdata_config} --psm 6".strip(),
                ).strip()
            ]
            if meaningful_text_length(results[0]) < 12:
                width, height = original.size
                scale = max(1, min(3, int(1800 / max(1, max(width, height))) + 1))
                enhanced = original.resize(
                    (width * scale, height * scale),
                    Image.Resampling.LANCZOS,
                )
                enhanced = ImageOps.grayscale(enhanced)
                enhanced = ImageOps.autocontrast(enhanced, cutoff=1)
                enhanced = ImageEnhance.Contrast(enhanced).enhance(1.6)
                enhanced = enhanced.filter(ImageFilter.SHARPEN)
                results.append(
                    pytesseract.image_to_string(
                        enhanced,
                        lang=languages,
                        config=f"{tessdata_config} --psm 11".strip(),
                    ).strip()
                )
            return max(results, key=meaningful_text_length, default="").strip()
    except Exception as exc:
        raise OcrError("OCR失败", repr(exc)) from exc


def meaningful_text_length(text):
    return sum(1 for char in str(text or "") if char.isalnum())
