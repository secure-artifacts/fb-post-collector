import re
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from .audio import transcribe_video
from ..config import DATA_DIR, TEMP_DIR
from .browser_profiles import (
    account_debug_port,
    chrome_executable,
    chrome_using_profile,
    clear_stale_profile_locks,
    close_profile_chrome,
    debug_port_alive,
    profile_lock_files,
    read_devtools_port,
)
from .environment import detect_tools
from .errors import (
    BrowserStartError,
    InvalidLinkError,
    LoginRequiredError,
    PermissionDeniedError,
    RateLimitError,
    VerificationRequiredError,
)
from .facebook_graphql import (
    EDIT_HISTORY_DOC_ID,
    EDIT_HISTORY_FRIENDLY_NAME,
    PROFILE_TIMELINE_DOC_ID,
    PROFILE_TIMELINE_FRIENDLY_NAME,
    SINGLE_POST_DOC_ID,
    SINGLE_POST_FRIENDLY_NAME,
    TAHOE_ROOT_DOC_ID,
    TAHOE_ROOT_FRIENDLY_NAME,
    extract_edit_history_fields,
    extract_reel_fields,
    extract_single_post_fields,
    extract_story_id,
    extract_video_id,
    extract_story_fields,
    graphql_fetch_script,
    merge_non_empty,
    parse_json_payloads,
    profile_timeline_variables,
    single_post_variables,
    timeline_edges_and_page_info,
)
from .gyazo import upload_file
from .ocr import ocr_image
from .proc import run_hidden
from .rate_limit import record_facebook_graphql_request, wait_for_facebook_graphql_slot
from .translator import translate_to_chinese_detail


FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.watch", "fb.me"}
DEBUG_ROOT_DIR = DATA_DIR
DEBUG_VIDEO_DIR = DEBUG_ROOT_DIR / "debug_videos"
DEBUG_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
SUPPORTED_OCR_LANGUAGES = {"eng", "por", "ara", "chi_sim"}


def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def extract_post_id(url, html=""):
    patterns = [
        r"/posts/([^/?#\s]+)",
        r"/videos/([^/?#\s]+)",
        r"/reel/([^/?#\s]+)",
        r"[?&](?:story_fbid|fbid|v)=([^&#]+)",
        r'"post_id"\s*:\s*"([^"]+)"',
        r'"story_fbid"\s*:\s*"([^"]+)"',
    ]
    source = f"{url}\n{html}"
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return ""


def extract_page_id(url, author_id=""):
    parsed = urlparse(url or "")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if str(query.get("id") or "").isdigit():
        return str(query["id"])
    for pattern in (r"/(\d+)/posts/", r"/(\d+)/videos/", r"/(\d+)_\d+(?:/|$)"):
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return str(author_id or "")


def classify_post(url, html, image_url):
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    has_og_video = re.search(r'<meta[^>]+property=["\']og:video(?::url)?["\'][^>]+content=["\']https?://', html, re.IGNORECASE)
    if "/reel/" in path or "/videos/" in path or query.get("v") or has_og_video:
        return "短视频"
    if image_url:
        return "图文贴"
    lowered = html[:20000].lower()
    if "background-color" in lowered or "comet_feed_story_text" in lowered:
        return "彩贴"
    return ""


def validate_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"} or not any(host == item or host.endswith("." + item) for item in FACEBOOK_HOSTS):
        raise InvalidLinkError("链接无效", f"Invalid or non-Facebook URL: {url}")


def canonicalize_facebook_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in {"fb.com", "www.fb.com", "m.fb.com"}:
        host = "www.facebook.com"
    path = parsed.path
    # Facebook 会把 /专页ID_贴文ID 这种复合短链重定向到专页主页。
    # 先展开为标准贴文永久链接，避免被后续的“个人主页保护”误判。
    composite = re.fullmatch(r"/(\d+)_(\d+)/?", path)
    if composite:
        path = f"/{composite.group(1)}/posts/{composite.group(2)}"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    keep = {}
    for key in ("story_fbid", "fbid", "id", "v"):
        if query.get(key):
            keep[key] = query[key]
    if "/reel/" in parsed.path or "/videos/" in parsed.path:
        keep = {}
    return urlunparse((parsed.scheme, host, path, "", urlencode(keep), ""))


def is_obvious_profile_url(url):
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.lower() == "profile.php":
        return True
    if not path or "/" in path:
        return False
    # Facebook also uses /{author_id}_{post_id} as a valid post permalink.
    return re.fullmatch(r"\d+_\d+", path) is None


def reject_non_post_redirect(input_url, final_url):
    if not is_obvious_profile_url(final_url):
        return
    raise InvalidLinkError(
        "链接为个人主页，不是贴文链接",
        f"Facebook share URL redirected to a profile. input_url={input_url}; final_url={final_url}",
    )


def unique_urls(*urls):
    seen = set()
    output = []
    for url in urls:
        if not url:
            continue
        for candidate in (url, canonicalize_facebook_url(url)):
            if candidate and candidate not in seen:
                seen.add(candidate)
                output.append(candidate)
    return output


def ytdlp_candidate_urls(url, values):
    urls = []
    video_id = values.get("video_id")
    if video_id:
        urls.extend(
            [
                f"https://www.facebook.com/watch/?v={video_id}",
                f"https://www.facebook.com/{video_id}",
                f"https://www.facebook.com/reel/{video_id}",
            ]
        )
    urls.append(url)
    return unique_urls(*urls)


def ytdlp_id_from_path(path, prefix):
    stem = Path(path).stem
    marker = f"{prefix}_"
    if not stem.startswith(marker):
        return ""
    return stem[len(marker) :].split("_", 1)[0]


def ytdlp_info_matches_expected(info, expected_video_id):
    if not expected_video_id:
        return True
    actual_id = str((info or {}).get("id") or "")
    return not actual_id or actual_id == str(expected_video_id)


def debug_enabled(project):
    account = project.get("browser_account") or {}
    return bool(account.get("debug_enabled"))


def persist_debug_video(video_path, post_id="", source_url="", enabled=False):
    if not enabled:
        return ""
    try:
        video = Path(video_path)
        if not video.exists():
            return ""
        stamp = int(time.time() * 1000)
        safe_post_id = re.sub(r"[^0-9A-Za-z._-]+", "_", str(post_id or "video"))[:80] or "video"
        safe_source = re.sub(r"[^0-9A-Za-z._-]+", "_", str(urlparse(source_url).path or "source"))[:40] or "source"
        target = DEBUG_VIDEO_DIR / f"{stamp}_{safe_post_id}_{safe_source}{video.suffix or '.mp4'}"
        shutil.copy2(video, target)
        return str(target)
    except Exception:
        return ""


def project_user_data_dir(project):
    browser_account = project.get("browser_account") or {}
    account_dir = browser_account.get("user_data_dir")
    if not account_dir:
        raise BrowserStartError(
            "请先选择抓取浏览器账号",
            "Project has no browser_account_id; old Chrome profiles are no longer supported.",
        )
    return account_dir


def project_account(project):
    account = dict(project.get("browser_account") or {})
    if not account.get("id") and project.get("browser_account_id"):
        account["id"] = project["browser_account_id"]
    if not account.get("user_data_dir"):
        account["user_data_dir"] = project.get("browser_account", {}).get("user_data_dir") or ""
    return account


def attach_chrome_driver(port):
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{int(port)}")
    chrome_path = chrome_executable()
    if chrome_path and Path(chrome_path).exists():
        options.binary_location = chrome_path
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    driver._fb_attached = True
    return driver


def attach_existing_driver(account):
    user_data_dir = Path(account.get("user_data_dir") or "")
    ports = []
    if account.get("id") is not None:
        ports.append(account_debug_port(account["id"]))
    if user_data_dir:
        ports.append(read_devtools_port(user_data_dir))
    seen = set()
    for port in ports:
        if not port or port in seen:
            continue
        seen.add(port)
        if not debug_port_alive(port):
            continue
        try:
            return attach_chrome_driver(port)
        except WebDriverException:
            continue
    return None


def close_driver(driver):
    if not driver or getattr(driver, "_fb_attached", False):
        return
    try:
        driver.quit()
    except Exception:
        pass


def make_driver(project, visible=False):
    account = project_account(project)
    user_data_dir = project_user_data_dir(project)
    path = Path(user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    attached = attach_existing_driver(account)
    if attached:
        return attached

    if chrome_using_profile(path):
        close_profile_chrome(path)
    else:
        clear_stale_profile_locks(path)

    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-popup-blocking")
    debug_port = account_debug_port(account["id"]) if account.get("id") is not None else 0
    options.add_argument(f"--remote-debugging-port={debug_port}")
    if not visible and not debug_enabled(project):
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1365,1600")

    options.add_argument(f"--user-data-dir={path}")
    options.add_argument("--remote-allow-origins=*")
    chrome_path = chrome_executable()
    if chrome_path and Path(chrome_path).exists():
        options.binary_location = chrome_path

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        driver._fb_attached = False
        return driver
    except WebDriverException as exc:
        message = str(exc)
        if chrome_using_profile(path):
            close_profile_chrome(path)
        hint = (
            "浏览器启动失败。请到“浏览器账号”重新点“打开登录”，登录后不用关窗口，再重新运行。"
        )
        if "DevToolsActivePort" in message or "session not created" in message or "user data directory is already in use" in message:
            raise BrowserStartError(hint, f"Cannot start Chrome driver: {message}") from exc
        raise BrowserStartError("浏览器启动失败", f"Cannot start Chrome driver: {message}") from exc


def login_check_driver(account):
    user_data_dir = Path(account.get("user_data_dir") or "")
    if not user_data_dir:
        raise BrowserStartError("请先选择抓取浏览器账号", "Missing user_data_dir")
    user_data_dir.mkdir(parents=True, exist_ok=True)
    attached = attach_existing_driver(account)
    if attached:
        return attached, True
    if chrome_using_profile(user_data_dir):
        raise BrowserStartError(
            "这个账号的登录窗口还开着，但还不能直接检测。请先关闭该 Chrome 窗口，再点一次“打开登录”，登录后不用关窗口，直接点“检测登录”。",
            f"Chrome profile is open without a debug port: {user_data_dir}",
        )
    clear_stale_profile_locks(user_data_dir)
    project = {"browser_account": account, "browser_account_id": account["id"]}
    return make_driver(project, visible=True), False


def ensure_logged_in(driver):
    if facebook_logged_in(driver):
        return
    try:
        driver.get("https://www.facebook.com/")
        time.sleep(2)
    except Exception:
        pass
    if facebook_logged_in(driver):
        return
    raise LoginRequiredError(
        "这个抓取账号还没登录 Facebook。请到「浏览器账号」点「打开登录」，在弹出的 Chrome 里登录。登录完成后不用关窗口，再重新运行抓取。"
    )


def facebook_logged_in(driver):
    cookies = []
    try:
        cookies.extend(driver.get_cookies() or [])
    except Exception:
        pass
    # 附加到已经打开的 Chrome 时，Selenium 有时只返回当前标签页域名的 Cookie。
    # CDP 的全域 Cookie 能正确识别同一个 Profile 中真实存在的 Facebook 登录态。
    try:
        cookies.extend((driver.execute_cdp_cmd("Network.getAllCookies", {}) or {}).get("cookies") or [])
    except Exception:
        pass
    names = set()
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lower().lstrip(".")
        if domain == "facebook.com" or domain.endswith(".facebook.com"):
            names.add(cookie.get("name"))
    if "c_user" in names:
        return True
    url = (driver.current_url or "").lower()
    if "/login" in url or "login.php" in url:
        return False
    html = driver.page_source or ""
    if re.search(r'"USER_ID"\s*:\s*"0"', html):
        return False
    if re.search(r'"USER_ID"\s*:\s*"[1-9]\d+"', html):
        return True
    if re.search(r'"(?:ACCOUNT_ID|actorID|__user)"?\s*[:=]\s*"[1-9]\d+"', html):
        return True
    title = (driver.title or "").lower()
    if "log in" in title or "登录" in title:
        return False
    return False


def page_state(url, title, html, driver=None):
    haystack = f"{url}\n{title}\n{html[:50000]}".lower()
    current_url = (url or "").lower()
    login_url = "/login" in current_url or "login.php" in current_url
    login_title = (title or "").strip().lower() in {"log in to facebook", "登录 facebook"}
    login_form = bool(
        re.search(r'<input[^>]+name=["\'](?:email|pass)["\']', html[:100000], re.IGNORECASE)
    )
    has_session = facebook_logged_in(driver) if driver else False
    if not has_session and (login_url or login_title or login_form):
        raise LoginRequiredError(
            "这个抓取账号还没登录 Facebook。请到「浏览器账号」点「打开登录」，在弹出的 Chrome 里登录。登录完成后不用关窗口，再重新运行抓取。"
        )
    if "checkpoint" in url.lower() or "security check" in haystack or "captcha" in haystack:
        raise VerificationRequiredError()
    unavailable = [
        "content isn't available",
        "this content isn't available",
        "this page isn't available",
        "page isn't available",
        "内容不可用",
        "此内容目前无法显示",
        "此页面无法显示",
        "帖子可能已被删除",
    ]
    if any(text in haystack for text in unavailable):
        raise InvalidLinkError("链接无效或贴文不可见")
    denied = ["you can't use this feature", "not allowed to see", "not available to you", "没有权限"]
    if any(text in haystack for text in denied):
        raise PermissionDeniedError()


def meta_content(soup, key):
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return tag.get("content", "").strip() if tag else ""


def download_temp_file(url, suffix=".jpg"):
    resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    elif "mp4" in content_type:
        suffix = ".mp4"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(resp.content)
    handle.close()
    return Path(handle.name)


def write_temp_bytes(content, suffix):
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(content)
    handle.close()
    return Path(handle.name)


def is_video_values(values, url=""):
    lowered = (url or "").lower()
    return values.get("post_type") == "短视频" or "/reel/" in lowered or "/videos/" in lowered or bool(values.get("video_url"))


def cookies_from_driver(driver):
    try:
        cdp = driver.execute_cdp_cmd("Network.getAllCookies", {})
        cookies = cdp.get("cookies") or []
        if cookies:
            return cookies
    except Exception:
        pass
    return driver.get_cookies()


def cookie_file_from_driver(driver):
    path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".cookies.txt").name)
    lines = ["# Netscape HTTP Cookie File"]
    seen = set()
    for cookie in cookies_from_driver(driver):
        domain = cookie.get("domain") or ".facebook.com"
        if "facebook.com" not in domain and "fbcdn.net" not in domain and "fb.com" not in domain:
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        cookie_path = cookie.get("path") or "/"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires_value = cookie.get("expiry") or cookie.get("expires") or (time.time() + 86400)
        if expires_value == -1:
            expires_value = time.time() + 86400
        expires = str(int(expires_value))
        name = cookie.get("name") or ""
        value = cookie.get("value") or ""
        key = (domain, cookie_path, name)
        if name and key not in seen:
            seen.add(key)
            lines.append("\t".join([domain, include_subdomains, cookie_path, secure, expires, name, value]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def capture_video_frame(video_path):
    tools = detect_tools()
    ffmpeg = tools["ffmpeg"]
    if not ffmpeg["available"]:
        return None
    frame_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name)
    try:
        run_hidden(
            [
                ffmpeg["path"],
                "-y",
                "-ss",
                "00:00:01",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return frame_path if frame_path.exists() and frame_path.stat().st_size > 0 else None
    except Exception:
        try:
            frame_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def strip_caption_text(text):
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        if line.upper().startswith("WEBVTT"):
            continue
        lines.append(re.sub(r"<[^>]+>", "", line))
    return " ".join(lines).strip()


def caption_url_from_ytdlp_info(info, language=""):
    caption_sources = []
    for source in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        if isinstance(source, dict):
            caption_sources.append(source)
    if not caption_sources:
        return ""

    preferred = []
    language = (language or "").lower()
    if language:
        preferred.append(language)
    preferred.extend(["en", "en-us", "en-orig", "pt", "pt-br", "por"])

    def language_rank(key):
        lowered = key.lower()
        for index, prefix in enumerate(preferred):
            if lowered == prefix or lowered.startswith(prefix + "-"):
                return index
        return len(preferred)

    candidates = []
    for source in caption_sources:
        for key, entries in source.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("url"):
                    ext = (entry.get("ext") or "").lower()
                    ext_rank = 0 if ext in {"vtt", "srt"} else 1
                    candidates.append((language_rank(key), ext_rank, entry["url"]))
    if not candidates:
        return ""
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]


def normalize_page_time(value):
    if not value:
        return ""
    if str(value).isdigit():
        try:
            text = str(value)
            if len(text) == 8:
                return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
            return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)
    return str(value).replace("T", " ").replace("+00:00", "")


def normalize_ocr_language(value):
    value = str(value or "").strip()
    if value in SUPPORTED_OCR_LANGUAGES:
        return value
    if "+" in value:
        parts = [part for part in value.split("+") if part in SUPPORTED_OCR_LANGUAGES]
        if "por" in parts:
            return "por"
        if parts:
            return parts[0]
    return "por"


def clean_facebook_image_description(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""

    quoted = re.findall(r"[“\"]([^”\"]{8,})[”\"]", text)
    if quoted:
        return max((item.strip() for item in quoted), key=len)

    markers = [
        "上面的文字是",
        "图片中的文字是",
        "image may contain",
        "text that says",
        "a imagem pode conter",
        "texto que diz",
    ]
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker.lower())
        if index >= 0:
            text = text[index + len(marker) :].strip(" ：:。.,，")
            break

    leading_patterns = [
        r"^可能是包含下列内容的图片[:：]?",
        r"^可能包含[:：]?",
        r"^image may contain[:：]?",
        r"^this image may contain[:：]?",
        r"^a imagem pode conter[:：]?",
    ]
    for pattern in leading_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" ：:。.,，")

    if re.match(r"^(可能是包含|可能包含|image may contain|this image may contain|a imagem pode conter)", text, re.IGNORECASE):
        return ""
    return text.strip(" “”?\"'")


def translate_field(values, source_key, target_key, error_key):
    result = translate_to_chinese_detail(values.get(source_key, ""), None)
    values[target_key] = result["text"]
    if result["error"]:
        values[error_key] = result["error"]


def parse_metric_count(value):
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)(万|億|亿|k|K|m|M)?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or ""
    if unit == "万":
        number *= 10000
    elif unit in {"亿", "億"}:
        number *= 100000000
    elif unit in {"k", "K"}:
        number *= 1000
    elif unit in {"m", "M"}:
        number *= 1000000
    return int(number)


def should_skip_audio_by_like_count(values, project):
    threshold = parse_metric_count(project.get("audio_min_like_count"))
    if not threshold:
        return False
    like_count = parse_metric_count(values.get("like_count"))
    return like_count is not None and like_count < threshold


def base_result():
    return {
        "post_url": "",
        "page_id": "",
        "author_id": "",
        "author_name": "",
        "author_avatar": "",
        "author_url": "",
        "media_preview_formula": "",
        "media_url": "",
        "ocr_text": "",
        "ocr_text_zh": "",
        "audio_text": "",
        "audio_text_zh": "",
        "post_text": "",
        "post_text_zh": "",
        "post_time": "",
        "like_count": "",
        "comment_count": "",
        "share_count": "",
        "post_id": "",
        "post_type": "",
        "video_view_count": "",
        "repost_or_original": "原创",
        "original_post_url": "",
        "is_edited": "",
        "status": "成功",
        "error_message": "",
        "scraped_at": now_text(),
    }


def assert_usable_post_data(values, raw):
    has_graphql = bool(values.get("graphql_source"))
    has_identity = bool(values.get("author_id") or values.get("author_name"))
    has_content = bool(values.get("post_text") or values.get("media_url") or values.get("video_url"))
    if has_graphql and has_identity and has_content:
        return
    url = (raw.get("url") or "").lower()
    is_video_page = "/reel/" in url or "/videos/" in url or values.get("post_type") == "短视频"
    if is_video_page and has_identity and (values.get("post_id") or values.get("media_url") or values.get("post_text")):
        return
    raise InvalidLinkError(
        "链接无效或无权限查看",
        f"Facebook post detail was not readable. raw={raw}; values={values}",
    )


class FacebookScraper:
    def scrape(self, url, project):
        validate_url(url)
        started_at = now_text()
        driver = None
        temp_files = []
        raw = {}
        try:
            driver = make_driver(project)
            ensure_logged_in(driver)
            request_url = canonicalize_facebook_url(url)
            driver.get(request_url)
            time.sleep(5)
            html = driver.page_source or ""
            page_state(driver.current_url, driver.title or "", html, driver)
            final_url = canonicalize_facebook_url(driver.current_url)
            reject_non_post_redirect(url, final_url)

            soup = BeautifulSoup(html, "html.parser")
            description = meta_content(soup, "og:description") or meta_content(soup, "description")
            title = meta_content(soup, "og:title")
            image_url = meta_content(soup, "og:image")
            fallback = {
                "author_name": title.split("|")[0].strip() if title else "",
                "media_url": image_url,
                "post_text": description,
                "post_id": extract_post_id(final_url, html),
                "post_type": classify_post(final_url, html, image_url),
            }

            values = merge_non_empty(base_result(), fallback)
            graphql_values, graphql_errors = self.fetch_graphql_values(driver, final_url, html, project)
            values = merge_non_empty(values, graphql_values)
            values["page_id"] = values.get("page_id") or extract_page_id(final_url, values.get("author_id"))
            self.enrich_from_open_page(driver, values, title, description)
            raw.update(
                {
                    "input_url": url,
                    "request_url": request_url,
                    "url": driver.current_url,
                    "canonical_url": final_url,
                    "title": title,
                    "meta_image_url": image_url,
                    "graphql_source": graphql_values.get("graphql_source", ""),
                    "graphql_confidence": graphql_values.get("graphql_confidence", {}),
                    "graphql_errors": graphql_errors,
                }
            )
            assert_usable_post_data(values, raw)

            if values.get("author_avatar"):
                self.process_author_avatar(values, temp_files)

            if is_video_values(values, final_url):
                self.enrich_video_with_ytdlp(ytdlp_candidate_urls(url, values) + unique_urls(driver.current_url, final_url), driver, values, temp_files, project)

            media_url = values.get("media_url", "")
            if values.get("video_url"):
                self.process_video_frame_media(values["video_url"], project, values, temp_files)
            elif is_video_values(values, final_url):
                self.process_video_dom_media(driver, project, values, temp_files)
            elif media_url:
                self.process_image_media(media_url, project, values, temp_files)

            self.process_audio_media(values, project, temp_files)

            translate_field(values, "post_text", "post_text_zh", "post_translate_error")
            translate_field(values, "audio_text", "audio_text_zh", "audio_translate_error")
            values["scraped_at"] = now_text()
            raw["yt_dlp_error"] = values.get("yt_dlp_error", "")
            raw["yt_dlp_path"] = values.get("yt_dlp_path", "")
            raw["yt_dlp_version"] = values.get("yt_dlp_version", "")
            raw["video_id"] = values.get("video_id", "")
            raw["debug_video_path"] = values.get("debug_video_path", "")
            raw["audio_error"] = values.get("audio_error", "")
            raw["ocr_status"] = values.get("ocr_status", "")
            raw["ocr_language"] = values.get("ocr_language", "")
            raw["ocr_error"] = values.get("ocr_error", "")
            raw["ocr_translate_error"] = values.get("ocr_translate_error", "")
            raw["post_translate_error"] = values.get("post_translate_error", "")
            raw["audio_translate_error"] = values.get("audio_translate_error", "")
            return {
                "status": "success",
                "post_id": values.get("post_id", ""),
                "started_at": started_at,
                "finished_at": now_text(),
                "values": values,
                "raw": raw,
            }
        finally:
            close_driver(driver)

    def enrich_prefetched(self, values, post_url, driver, project):
        started_at = now_text()
        temp_files = []
        raw = {"input_url": post_url, "url": post_url, "graphql_source": values.get("graphql_source", "")}
        try:
            values = merge_non_empty(base_result(), values)
            values["post_url"] = values.get("post_url") or post_url
            values["page_id"] = values.get("page_id") or extract_page_id(post_url, values.get("author_id"))
            if values.get("author_avatar"):
                self.process_author_avatar(values, temp_files)

            if is_video_values(values, post_url):
                self.enrich_video_with_ytdlp(ytdlp_candidate_urls(post_url, values), driver, values, temp_files, project)

            media_url = values.get("media_url", "")
            if values.get("video_url"):
                self.process_video_frame_media(values["video_url"], project, values, temp_files)
            elif is_video_values(values, post_url):
                self.process_video_dom_media(driver, project, values, temp_files)
            elif media_url:
                self.process_image_media(media_url, project, values, temp_files)

            self.process_audio_media(values, project, temp_files)
            translate_field(values, "post_text", "post_text_zh", "post_translate_error")
            translate_field(values, "audio_text", "audio_text_zh", "audio_translate_error")
            values["scraped_at"] = now_text()
            raw["yt_dlp_error"] = values.get("yt_dlp_error", "")
            raw["yt_dlp_path"] = values.get("yt_dlp_path", "")
            raw["yt_dlp_version"] = values.get("yt_dlp_version", "")
            raw["video_id"] = values.get("video_id", "")
            raw["debug_video_path"] = values.get("debug_video_path", "")
            raw["audio_error"] = values.get("audio_error", "")
            raw["ocr_status"] = values.get("ocr_status", "")
            raw["ocr_language"] = values.get("ocr_language", "")
            raw["ocr_error"] = values.get("ocr_error", "")
            raw["ocr_translate_error"] = values.get("ocr_translate_error", "")
            raw["post_translate_error"] = values.get("post_translate_error", "")
            raw["audio_translate_error"] = values.get("audio_translate_error", "")
            return {
                "status": "success",
                "post_id": values.get("post_id", ""),
                "started_at": started_at,
                "finished_at": now_text(),
                "values": values,
                "raw": raw,
            }
        finally:
            for path in temp_files:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            for path in temp_files:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def fetch_graphql_values(self, driver, url, html, project):
        values = {}
        errors = []
        video_id = extract_video_id(url, html)
        if video_id:
            try:
                response = self.graphql_fetch(
                    driver,
                    project,
                    TAHOE_ROOT_FRIENDLY_NAME,
                    TAHOE_ROOT_DOC_ID,
                    {"videoID": video_id, "scale": 1, "useDefaultActor": False},
                )
                payloads = parse_json_payloads(response)
                values = merge_non_empty(values, extract_reel_fields(payloads))
            except RateLimitError:
                raise
            except Exception as exc:
                errors.append(f"{TAHOE_ROOT_FRIENDLY_NAME}: {exc!r}")

        story_id = extract_story_id(url, html)
        if story_id:
            try:
                response = self.graphql_fetch(
                    driver,
                    project,
                    SINGLE_POST_FRIENDLY_NAME,
                    SINGLE_POST_DOC_ID,
                    single_post_variables(story_id),
                )
                payloads = parse_json_payloads(response)
                values = merge_non_empty(values, extract_single_post_fields(payloads))
            except RateLimitError:
                raise
            except Exception as exc:
                errors.append(f"{SINGLE_POST_FRIENDLY_NAME}: {exc!r}")

            try:
                response = self.graphql_fetch(
                    driver,
                    project,
                    EDIT_HISTORY_FRIENDLY_NAME,
                    EDIT_HISTORY_DOC_ID,
                    {"count": 10, "scale": 3, "storyID": story_id},
                )
                payloads = parse_json_payloads(response)
                values = merge_non_empty(extract_edit_history_fields(payloads), values)
            except RateLimitError:
                raise
            except Exception as exc:
                errors.append(f"{EDIT_HISTORY_FRIENDLY_NAME}: {exc!r}")
        return values, errors

    def graphql_fetch(self, driver, project, friendly_name, doc_id, variables):
        account_id = project.get("browser_account_id")
        lock, usage_date = wait_for_facebook_graphql_slot(account_id)
        try:
            record_facebook_graphql_request(account_id, usage_date)
            result = driver.execute_async_script(graphql_fetch_script(), friendly_name, doc_id, variables)
        finally:
            lock.release()
        if not result or not result.get("ok"):
            raise RuntimeError(f"Facebook GraphQL failed: {friendly_name}; {result}")
        return result.get("text", "")

    def fetch_profile_timeline(self, driver, project, profile_id, after_time, before_time, cursor=None):
        response = self.graphql_fetch(
            driver,
            project,
            PROFILE_TIMELINE_FRIENDLY_NAME,
            PROFILE_TIMELINE_DOC_ID,
            profile_timeline_variables(profile_id, int(after_time), int(before_time), cursor),
        )
        payloads = parse_json_payloads(response)
        edges, page_info = timeline_edges_and_page_info(payloads)
        return [extract_story_fields(edge.get("node") or {}) for edge in edges], page_info

    def enrich_from_open_page(self, driver, values, title, description):
        try:
            data = driver.execute_script(
                """
                const pickText = (selectors) => {
                  for (const selector of selectors) {
                    const node = document.querySelector(selector);
                    const text = node && (node.innerText || node.textContent || '').trim();
                    if (text) return text;
                  }
                  return '';
                };
                const timeNode = document.querySelector('time[datetime], abbr[data-utime], [data-utime]');
                const video = document.querySelector('video');
                return {
                  text: pickText([
                    '[data-ad-preview="message"]',
                    '[data-ad-comet-preview="message"]',
                    'div[dir="auto"][style*="text-align"]'
                  ]),
                  time: timeNode ? (timeNode.getAttribute('datetime') || timeNode.getAttribute('data-utime') || '') : '',
                  hasVideo: Boolean(video),
                  videoSrc: video ? (video.currentSrc || video.src || '') : '',
                  metaImage: document.querySelector('meta[property="og:image"]')?.content || '',
                  metaDescription: document.querySelector('meta[property="og:description"]')?.content || ''
                };
                """
            )
        except Exception:
            data = {}
        if not values.get("post_text"):
            values["post_text"] = data.get("text") or description or ""
        if not values.get("post_time"):
            values["post_time"] = normalize_page_time(data.get("time") or "")
        video_src = data.get("videoSrc") or ""
        if not values.get("video_url") and video_src.startswith(("http://", "https://")):
            values["video_url"] = data["videoSrc"]
        if not values.get("media_url"):
            values["media_url"] = data.get("metaImage") or ""
        if video_src.startswith(("http://", "https://")):
            values["post_type"] = "短视频"

    def process_author_avatar(self, values, temp_files):
        avatar_url = values.get("author_avatar", "")
        try:
            temp = download_temp_file(avatar_url)
            temp_files.append(temp)
            values["author_avatar"] = upload_file(temp) or avatar_url
        except Exception:
            values["author_avatar"] = avatar_url

    def enrich_video_with_ytdlp(self, urls, driver, values, temp_files, project):
        tools = detect_tools()
        ytdlp = tools.get("yt_dlp", {})
        if not ytdlp.get("available"):
            values["yt_dlp_error"] = "yt-dlp is not installed or not in PATH"
            return
        values["yt_dlp_path"] = ytdlp.get("path", "")
        values["yt_dlp_version"] = ytdlp.get("version", "")
        cookie_path = None
        errors = []
        try:
            cookie_path = cookie_file_from_driver(driver)
            temp_files.append(cookie_path)
            for candidate in urls if isinstance(urls, list) else [urls]:
                info_loaded = False
                try:
                    info = self.ytdlp_info(ytdlp["path"], candidate, cookie_path)
                    info_loaded = True
                    if not ytdlp_info_matches_expected(info, values.get("video_id", "")):
                        raise RuntimeError(f"wrong video info id: expected={values.get('video_id', '')}, actual={info.get('id')}")
                    if not values.get("post_text"):
                        values["post_text"] = info.get("description") or info.get("title") or ""
                    if not values.get("post_time"):
                        values["post_time"] = normalize_page_time(info.get("timestamp") or info.get("upload_date") or "")
                    if not values.get("video_view_count"):
                        values["video_view_count"] = info.get("view_count") or ""
                    if not values.get("media_url"):
                        values["media_url"] = info.get("thumbnail") or ""
                    if not values.get("captions_url"):
                        values["captions_url"] = caption_url_from_ytdlp_info(info, project.get("whisper_language") or "")
                except Exception as exc:
                    errors.append(f"{candidate} info: {exc!r}")
                try:
                    if not values.get("local_video_path"):
                        video_path = self.ytdlp_download(ytdlp["path"], candidate, cookie_path, values.get("video_id", ""))
                        if video_path:
                            temp_files.append(video_path)
                            values["local_video_path"] = str(video_path)
                            values["debug_video_path"] = persist_debug_video(
                                video_path,
                                values.get("post_id", ""),
                                candidate,
                                debug_enabled(project),
                            )
                    values["yt_dlp_error"] = ""
                    return
                except Exception as exc:
                    label = "download after info" if info_loaded else "download without info"
                    errors.append(f"{candidate} {label}: {exc!r}")
        except Exception as exc:
            errors.append(f"cookie export: {exc!r}")
        values["yt_dlp_error"] = f"path={values.get('yt_dlp_path', '')}; version={values.get('yt_dlp_version', '')}; " + " | ".join(errors)

    def ytdlp_info(self, executable, url, cookie_path):
        proc = run_hidden(
            [executable, "--cookies", str(cookie_path), "--dump-single-json", "--no-playlist", url],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip())
        return json.loads(proc.stdout or "{}")

    def ytdlp_download(self, executable, url, cookie_path, expected_video_id=""):
        prefix = f"fb_video_{int(time.time() * 1000)}"
        output_template = str(TEMP_DIR / f"{prefix}_%(id)s.%(ext)s")
        commands = [
            [
                executable,
                "--cookies",
                str(cookie_path),
                "--no-playlist",
                "-f",
                "bv*+ba/best",
                "--merge-output-format",
                "mp4",
                "-o",
                output_template,
                url,
            ],
            [
                executable,
                "--cookies",
                str(cookie_path),
                "--merge-output-format",
                "mp4",
                "-o",
                output_template,
                url,
            ],
            [
                executable,
                "--merge-output-format",
                "mp4",
                "-o",
                output_template,
                url,
            ],
        ]
        errors = []
        for cmd in commands:
            proc = run_hidden(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if proc.returncode == 0:
                break
            errors.append((proc.stderr or proc.stdout or "").strip())
        else:
            raise RuntimeError(" || ".join(error for error in errors if error))
        created = [path for path in TEMP_DIR.glob(f"{prefix}_*") if path.is_file()]
        if not created:
            return None
        video_path = max(created, key=lambda item: item.stat().st_mtime)
        actual_id = ytdlp_id_from_path(video_path, prefix)
        if expected_video_id and actual_id and actual_id != str(expected_video_id):
            try:
                video_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(f"downloaded wrong video id: expected={expected_video_id}, actual={actual_id}, url={url}")
        return video_path

    def process_video_frame_media(self, video_url, project, values, temp_files):
        try:
            if values.get("local_video_path"):
                frame = capture_video_frame(values["local_video_path"])
                if frame:
                    temp_files.append(frame)
                    gyazo_url = upload_file(frame)
                    values["media_url"] = gyazo_url or values.get("media_url", "")
                    values["media_preview_formula"] = f'=IMAGE("{values["media_url"]}")' if values["media_url"] else ""
                    values["ocr_text"] = ""
                    values["ocr_text_zh"] = ""
                    return
            video = download_temp_file(video_url, ".mp4")
            temp_files.append(video)
            frame = capture_video_frame(video)
            if frame:
                temp_files.append(frame)
                gyazo_url = upload_file(frame)
                values["media_url"] = gyazo_url or values.get("media_url", "")
                values["media_preview_formula"] = f'=IMAGE("{values["media_url"]}")' if values["media_url"] else ""
                values["ocr_text"] = ""
                values["ocr_text_zh"] = ""
                return
        except Exception:
            pass
        media_url = values.get("media_url", "")
        if media_url:
            self.process_image_media(media_url, project, values, temp_files)

    def process_video_dom_media(self, driver, project, values, temp_files):
        try:
            video = driver.execute_script("return document.querySelector('video')")
            if video:
                screenshot = video.screenshot_as_png
                frame = write_temp_bytes(screenshot, ".png")
                temp_files.append(frame)
                gyazo_url = upload_file(frame)
                values["media_url"] = gyazo_url or values.get("media_url", "")
                values["media_preview_formula"] = f'=IMAGE("{values["media_url"]}")' if values["media_url"] else ""
                values["ocr_text"] = ""
                values["ocr_text_zh"] = ""
                return
        except Exception:
            pass
        media_url = values.get("media_url", "")
        if media_url:
            self.process_image_media(media_url, project, values, temp_files)

    def process_image_media(self, media_url, project, values, temp_files):
        try:
            temp = download_temp_file(media_url)
            temp_files.append(temp)
            gyazo_url = upload_file(temp)
            values["media_url"] = gyazo_url or media_url
            values["media_preview_formula"] = f'=IMAGE("{values["media_url"]}")' if values["media_url"] else ""
            values["ocr_language"] = normalize_ocr_language(project.get("ocr_languages"))
            values["ocr_status"] = ""
            values["ocr_error"] = ""
            try:
                values["ocr_text"] = ocr_image(temp, values["ocr_language"])
                values["ocr_status"] = "success" if values["ocr_text"] else "success_empty"
            except Exception as exc:
                values["ocr_text"] = ""
                values["ocr_status"] = "failed"
                values["ocr_error"] = repr(exc)
            if not values["ocr_text"]:
                values["ocr_text"] = clean_facebook_image_description(values.get("image_accessibility_text", ""))
                if values["ocr_text"]:
                    values["ocr_status"] = f"{values['ocr_status']}_fallback"
            translate_field(values, "ocr_text", "ocr_text_zh", "ocr_translate_error")
        except Exception:
            values["media_url"] = media_url
            values["media_preview_formula"] = f'=IMAGE("{media_url}")' if media_url else ""

    def process_audio_media(self, values, project, temp_files):
        if should_skip_audio_by_like_count(values, project):
            values["audio_text"] = ""
            values["audio_error"] = (
                f"skipped audio transcription: like_count={values.get('like_count', '')}, "
                f"threshold={project.get('audio_min_like_count', 0)}"
            )
            return

        captions_url = values.get("captions_url")
        if captions_url:
            try:
                response = requests.get(captions_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                values["audio_text"] = strip_caption_text(response.text)
                if not values["audio_text"]:
                    values["audio_error"] = "caption file was found but contained no readable text"
                return
            except Exception as exc:
                values["audio_error"] = f"caption download failed: {exc!r}"

        if values.get("local_video_path") and not values.get("audio_text"):
            try:
                values["audio_text"] = transcribe_video(
                    values["local_video_path"],
                    project.get("whisper_language") or "",
                    debug_enabled(project),
                )
                if not values["audio_text"]:
                    values["audio_error"] = "whisper finished but returned empty text"
                return
            except Exception as exc:
                values["audio_error"] = f"whisper local video failed: {exc!r}"
                values["audio_text"] = ""

        video_url = values.get("video_url")
        if video_url and not values.get("audio_text"):
            try:
                temp = download_temp_file(video_url, ".mp4")
                temp_files.append(temp)
                values["audio_text"] = transcribe_video(
                    temp,
                    project.get("whisper_language") or "",
                    debug_enabled(project),
                )
                if not values["audio_text"]:
                    values["audio_error"] = "whisper finished but returned empty text"
            except Exception as exc:
                values["audio_error"] = f"whisper video url failed: {exc!r}"
                values["audio_text"] = ""
