import ctypes
import ctypes.wintypes as wintypes
import datetime
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw

from .config import APP_NAME, APP_VERSION, BASE_DIR, DATA_DIR
from .services.proc import run_hidden


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


def can_bind(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_port(host, start=5088, count=12):
    for port in range(int(start), int(start) + count):
        if http_alive(f"http://{host}:{port}/api/ping"):
            return port, True
        if can_bind(host, port):
            return port, False
    return int(start), False


def http_alive(url, timeout=0.4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def pids_on_port(port):
    pids = []
    try:
        completed = run_hidden(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except Exception:
        return pids
    needle = f":{int(port)} "
    for line in (completed.stdout or "").splitlines():
        if needle not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pid = int(parts[-1])
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids


def kill_pids(pids):
    me = os.getpid()
    for pid in pids:
        if int(pid) == me:
            continue
        try:
            run_hidden(["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True, timeout=8)
        except Exception:
            pass


def ensure_port_free(host, port):
    if http_alive(f"http://{host}:{port}/api/ping"):
        return False
    me = os.getpid()
    pids = [pid for pid in pids_on_port(port) if pid != me]
    if pids:
        kill_pids(pids)
    for _ in range(10):
        leftover = [pid for pid in pids_on_port(port) if pid != me]
        if not leftover:
            return True
        time.sleep(0.3)
    return not http_alive(f"http://{host}:{port}/api/ping")


def already_running(host, port):
    global _MUTEX_HANDLE
    if http_alive(f"http://{host}:{port}/api/ping"):
        return True
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return False


def load_tray_image():
    candidates = [
        resource_path("static", "app.ico"),
        resource_path("fb_collector", "static", "app.ico"),
        BASE_DIR / "fb_collector" / "static" / "app.ico",
    ]
    for path in candidates:
        if path.exists():
            image = Image.open(path).convert("RGBA")
            return image.resize((64, 64))
    image = Image.new("RGBA", (64, 64), (23, 32, 51, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(13, 110, 253, 255))
    draw.text((18, 14), "FB", fill="white")
    return image


_TRAY_HWND = None
_TRAY_HICON = None
_TRAY_WNDPROC = None
_QUIT_CALLBACK = None
_OPEN_URL = ""
_CTRL_HANDLER = None
_TASKBAR_CREATED = 0

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_NULL = 0x0000
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIF_SHOWTIP = 0x00000080
NIIF_INFO = 0x00000001
NIIF_NOSOUND = 0x00000010
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE = 0
MSGFLT_ALLOW = 1
MF_STRING = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
ID_OPEN = 1001
ID_QUIT = 1002
FLASHW_ALL = 3
FLASHW_TIMERNOFG = 12


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def _log(message):
    try:
        path = DATA_DIR / "desktop.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:
        pass


def _set_app_id():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FBPostCollector.App")
    except Exception:
        pass


def _set_console_title():
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(f"FB贴文数据采集 v{APP_VERSION}")
    except Exception:
        pass


def _detach_console():
    global _CTRL_HANDLER
    if os.name != "nt":
        return
    try:
        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        _CTRL_HANDLER = handler_type(lambda _event: True)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER, True)
        ctypes.windll.kernel32.FreeConsole()
        _log("console detached")
    except Exception as exc:
        _log(f"detach console failed: {exc}")


def _promote_win11_tray_icon():
    if os.name != "nt":
        return
    try:
        import winreg

        exe = os.path.normcase(os.path.abspath(sys.executable))
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings")
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            key = winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
            try:
                path, _ = winreg.QueryValueEx(key, "ExecutablePath")
            except OSError:
                winreg.CloseKey(key)
                continue
            if os.path.normcase(os.path.abspath(str(path))) == exe:
                winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                _log(f"promoted tray icon {name}")
            winreg.CloseKey(key)
        winreg.CloseKey(root)
    except Exception as exc:
        _log(f"promote tray skipped: {exc}")


def _icon_path():
    for path in (
        resource_path("static", "app.ico"),
        resource_path("fb_collector", "static", "app.ico"),
        BASE_DIR / "fb_collector" / "static" / "app.ico",
    ):
        if path.exists():
            return path
    return None


def _load_hicon():
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.wintypes.HANDLE
    path = _icon_path()
    if path:
        handle = user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if handle:
            return handle
        handle = user32.LoadImageW(None, str(path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if handle:
            return handle
    return user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, ctypes.wintypes.LPCWSTR))


def _nid(hwnd, hicon, with_balloon=False):
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = hicon
    nid.szTip = f"FB贴文数据采集 v{APP_VERSION}"
    if with_balloon:
        nid.uFlags |= NIF_INFO
        nid.szInfoTitle = "FB贴文数据采集"
        nid.szInfo = "软件正在运行。托盘图标可能在右下角时钟旁边的 ^ 隐藏图标里。"
        nid.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND
    return nid


def _destroy_tray_icon():
    global _TRAY_HWND, _TRAY_HICON
    if os.name != "nt" or not _TRAY_HWND:
        return
    try:
        nid = _nid(_TRAY_HWND, _TRAY_HICON or 0)
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    except Exception:
        pass
    if _TRAY_HICON:
        try:
            ctypes.windll.user32.DestroyIcon(_TRAY_HICON)
        except Exception:
            pass
    _TRAY_HWND = None
    _TRAY_HICON = None


def _show_tray_menu(hwnd):
    user32 = ctypes.windll.user32
    point = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    menu = user32.CreatePopupMenu()
    user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "打开管理页面")
    user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出软件")
    user32.SetForegroundWindow(hwnd)
    cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    user32.DestroyMenu(menu)
    if cmd == ID_OPEN:
        webbrowser.open(_OPEN_URL)
    elif cmd == ID_QUIT:
        _request_quit("tray-menu")


def _request_quit(reason):
    _log(f"quit requested: {reason}")
    callback = _QUIT_CALLBACK
    if callback:
        threading.Thread(target=callback, name="fb-collector-quit", daemon=True).start()


def _readd_tray_icon():
    if not _TRAY_HWND or not _TRAY_HICON:
        return
    nid = _nid(_TRAY_HWND, _TRAY_HICON, with_balloon=False)
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    _promote_win11_tray_icon()
    _log("tray icon re-added")


def _tray_wndproc(hwnd, msg, wparam, lparam):
    try:
        if _TASKBAR_CREATED and msg == _TASKBAR_CREATED:
            _readd_tray_icon()
            return 0
        if msg == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                webbrowser.open(_OPEN_URL)
                return 0
            if event == WM_RBUTTONUP:
                _show_tray_menu(hwnd)
                return 0
        if msg == WM_COMMAND:
            if wparam & 0xFFFF == ID_OPEN:
                webbrowser.open(_OPEN_URL)
                return 0
            if wparam & 0xFFFF == ID_QUIT:
                _request_quit("tray-command")
                return 0
        if msg == WM_DESTROY:
            _log("tray hwnd destroyed")
            _destroy_tray_icon()
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except Exception as exc:
        _log(f"wndproc msg={msg} error: {exc}")
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def run_win32_tray(url, on_quit):
    global _TRAY_HWND, _TRAY_HICON, _TRAY_WNDPROC, _QUIT_CALLBACK, _OPEN_URL, _TASKBAR_CREATED
    if os.name != "nt":
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreateWindowExW.restype = wintypes.HWND
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    _OPEN_URL = url
    _QUIT_CALLBACK = on_quit
    _TRAY_WNDPROC = WNDPROC(_tray_wndproc)
    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "FBPostCollectorTrayWnd"
    wndclass = WNDCLASSW()
    wndclass.lpfnWndProc = _TRAY_WNDPROC
    wndclass.hInstance = hinstance
    wndclass.lpszClassName = class_name
    atom = user32.RegisterClassW(ctypes.byref(wndclass))
    if not atom:
        err = kernel32.GetLastError()
        if err not in (1410, 0):
            _log(f"RegisterClassW failed {err}")
            return False

    hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        class_name,
        "FBPostCollectorTray",
        WS_POPUP,
        0,
        0,
        1,
        1,
        None,
        None,
        hinstance,
        None,
    )
    if not hwnd:
        _log(f"CreateWindowExW failed {kernel32.GetLastError()}")
        return False
    user32.ShowWindow(hwnd, SW_HIDE)
    _TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")
    try:
        user32.ChangeWindowMessageFilterEx(hwnd, _TASKBAR_CREATED, MSGFLT_ALLOW, None)
    except Exception:
        pass

    hicon = _load_hicon()
    _TRAY_HWND = hwnd
    _TRAY_HICON = hicon
    nid = _nid(hwnd, hicon, with_balloon=True)
    added = False
    for attempt in range(8):
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            added = True
            break
        _log(f"NIM_ADD retry {attempt + 1} err={kernel32.GetLastError()}")
        time.sleep(0.4)
        nid = _nid(hwnd, hicon, with_balloon=True)
    if not added:
        _log("NIM_ADD failed")
        return False

    _log("native tray icon added")
    _promote_win11_tray_icon()

    msg = ctypes.wintypes.MSG()
    while True:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return True


def make_tray_icon(url, on_quit):
    import pystray

    def open_ui(icon=None, item=None):
        webbrowser.open(url)

    def quit_app(icon, item):
        try:
            icon.visible = False
            icon.stop()
        except Exception:
            pass
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
    return icon


def run_tray(url, on_quit):
    icon = make_tray_icon(url, on_quit)

    def setup(icon):
        icon.visible = True
        try:
            icon.notify("软件正在运行。托盘图标可能在右下角 ^ 隐藏图标里。", "FB贴文数据采集")
        except Exception:
            pass

    icon.run(setup=setup)


def _flash_window(root):
    if os.name != "nt":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        info.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG
        info.uCount = 8
        info.dwTimeout = 0
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def run_status_window(url, on_quit):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"FB贴文数据采集 v{APP_VERSION}")
    root.geometry("460x230")
    root.resizable(False, False)
    try:
        icon_path = _icon_path()
        if icon_path:
            root.iconbitmap(str(icon_path))
    except Exception:
        pass
    try:
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
    except Exception:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="软件正在运行", font=("Microsoft YaHei", 14, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            f"管理页面：{url}\n"
            "托盘图标在屏幕右下角时钟旁边；如果看不到，请点向上的 ^ 查看隐藏图标。\n"
            "关掉这个窗口不会退出，只会缩到任务栏。要退出请点“退出软件”。"
        ),
        justify="left",
        wraplength=420,
    ).pack(anchor="w", pady=(8, 16))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    ttk.Button(buttons, text="打开管理页面", command=lambda: webbrowser.open(url)).pack(side="left")
    ttk.Button(buttons, text="退出软件", command=on_quit).pack(side="left", padx=(8, 0))

    def hide_to_taskbar():
        root.iconify()

    def drop_topmost():
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", hide_to_taskbar)
    root.after(80, lambda: _flash_window(root))
    root.after(2500, drop_topmost)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    root.mainloop()


def _run_tray_thread(url, on_quit):
    try:
        if run_win32_tray(url, on_quit):
            return
        _log("falling back to pystray")
        run_tray(url, on_quit)
    except Exception as exc:
        _log(f"tray thread failed: {exc}")


def start_desktop_shell(url, start_server, on_quit):
    _set_app_id()
    _set_console_title()
    _log(f"starting desktop shell {url}")
    _detach_console()

    def wrapped_quit():
        _destroy_tray_icon()
        on_quit()

    server_thread = threading.Thread(target=start_server, name="fb-collector-server", daemon=True)
    server_thread.start()
    tray_thread = threading.Thread(target=lambda: _run_tray_thread(url, wrapped_quit), name="fb-collector-tray", daemon=True)
    tray_thread.start()
    try:
        run_status_window(url, wrapped_quit)
    except Exception as exc:
        _log(f"status window failed: {exc}")
    _log("status window ended, keeping server")
    server_thread.join()
