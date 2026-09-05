import os
import sys
from pathlib import Path


APP_NAME = "FBPostCollector"
APP_VERSION = "1.3.4"
BASE_DIR = Path(__file__).resolve().parent.parent
APP_ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR


def user_data_dir() -> Path:
    override = os.environ.get("FB_COLLECTOR_DATA_DIR")
    if override:
        path = Path(override)
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    else:
        path = Path.home() / ".fb_post_collector"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except OSError:
        fallback = BASE_DIR / ".data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = user_data_dir()
DB_PATH = DATA_DIR / "collector.sqlite3"
TOKEN_DIR = DATA_DIR / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)
TOOLS_DIR = BASE_DIR / "tools"
TEMP_DIR = DATA_DIR / "tmp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 发布版本默认 Gyazo Token。用户在设置页填写自己的 Token 时会优先使用用户 Token。
# 需要写死时，把下面的空字符串改成你的 Gyazo Access Token。
DEFAULT_GYAZO_ACCESS_TOKEN = os.environ.get("FB_COLLECTOR_DEFAULT_GYAZO_TOKEN", "")
