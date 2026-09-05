import re


DEFAULT_FIELDS = [
    ("post_id", "贴文ID", True),
    ("post_text", "贴文信息", True),
    ("post_text_zh", "贴文信息翻译", True),
    ("post_url", "贴文链接", True),
    ("original_post_url", "贴文原始链接", True),
    ("post_time", "贴文时间", True),
    ("media_preview_formula", "贴文多媒体", True),
    ("like_count", "贴文点赞量", True),
    ("comment_count", "贴文评论量", True),
    ("share_count", "贴文分享量", True),
    ("post_type", "贴文类型", True),
    ("repost_or_original", "转贴/原创", True),
    ("page_id", "专页ID-链接里的ID", True),
    ("ocr_text", "贴文OCR", True),
    ("ocr_text_zh", "贴文OCR文本翻译", True),
    ("is_edited", "是否为改贴", True),
    ("media_url", "图片链接", True),
    ("audio_text", "音频文本", True),
    ("audio_text_zh", "文本翻译", True),
    ("author_id", "作者ID", False),
    ("author_name", "作者名字", False),
    ("author_avatar", "作者头像", False),
    ("author_url", "作者链接", False),
    ("video_view_count", "视频播放量", False),
    ("status", "状态", False),
    ("error_message", "错误", False),
    ("scraped_at", "抓取时间", False),
]


FIELD_LABELS = {key: label for key, label, _ in DEFAULT_FIELDS}


def column_to_index(col: str) -> int:
    total = 0
    for ch in str(col or "").strip().upper():
        if not ("A" <= ch <= "Z"):
            continue
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total


def index_to_column(index: int) -> str:
    if not index or index <= 0:
        return ""
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def normalize_column(value, default=""):
    column = str(value or "").strip().upper()
    return column if re.fullmatch(r"[A-Z]{1,3}", column) else default


def assign_default_write_columns(fields, write_start_column="B", force=False):
    start = column_to_index(write_start_column) or column_to_index("B")
    offset = 0
    for field in fields:
        current = normalize_column(field.get("write_column"))
        if field.get("enabled"):
            if force or not current:
                field["write_column"] = index_to_column(start + offset)
            else:
                field["write_column"] = current
            offset += 1
        else:
            field["write_column"] = current
    return fields


def field_write_pairs(fields, values_map):
    pairs = []
    for field in fields:
        column = normalize_column(field.get("write_column"))
        if not column:
            continue
        pairs.append((column, values_map.get(field.get("field_key"), "")))
    return pairs
