import ctypes
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw

from .config import APP_NAME, APP_VERSION, BASE_DIR


MUTEX_NAME = "FBPostCollectorSingleton"
_MUTEX_HANDLE = None


def resource_path(*parts):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def already_running():
    global _MUTEX_HANDLE
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() == 183


def load_tray_image():
    candidates = [
        resource_path("static", "app.ico"),
        resource_path("fb_collector", "static", "app.ico"),
        BASE_DIR / "fb_collector" / "static" / "app.ico",
    ]
    for path in candidates:
        if path.exists():
            return Image.open(path)
    image = Image.new("RGBA", (64, 64), (23, 32, 51, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(13, 110, 253, 255))
    draw.text((18, 14), "FB", fill="white")
    return image


def run_tray(url, on_quit):
    import pystray

    def open_ui(icon=None, item=None):
        webbrowser.open(url)

    def quit_app(icon, item):
        icon.visible = False
        icon.stop()
        on_quit()

    icon = pystray.Icon(
        APP_NAME,
        load_tray_image(),
        f"FB贴文数据采集 v{APP_VERSION}",
        menu=pystray.Menu(
            pystray.MenuItem("打开管理页面", open_ui, default=True),
            pystray.MenuItem("退出软件", quit_app),
        ),
    )
    try:
        icon.notify("软件已在后台运行。关闭网页不会退出，请从任务栏图标打开或退出。", "FB贴文数据采集")
    except Exception:
        pass
    icon.run()
