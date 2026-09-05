import os
import subprocess


def hidden_popen_kwargs():
    kwargs = {}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def run_hidden(command, **kwargs):
    merged = hidden_popen_kwargs()
    merged.update(kwargs)
    if os.name == "nt":
        merged["creationflags"] = subprocess.CREATE_NO_WINDOW | int(merged.get("creationflags") or 0)
        if "startupinfo" not in kwargs:
            merged["startupinfo"] = hidden_popen_kwargs()["startupinfo"]
    return subprocess.run(command, **merged)
