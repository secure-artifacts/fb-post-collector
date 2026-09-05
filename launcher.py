import sys
import traceback

from fb_collector.app import main
from fb_collector.config import DATA_DIR


def _show_crash(text):
    try:
        path = DATA_DIR / "desktop.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\nlauncher crash:\n")
            handle.write(text)
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text[:800], "FB贴文数据采集 启动失败", 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _show_crash(traceback.format_exc())
        raise
