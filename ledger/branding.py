import sys
import os
from pathlib import Path
import tkinter as tk

APP_NAME = '台账'


def asset_path(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'assets' / name


def apply_branding(root):
    root.title(APP_NAME)
    root._app_icon = tk.PhotoImage(master=root, file=str(asset_path('taizhang.png')))
    root.iconphoto(True, root._app_icon)
    if sys.platform == 'win32':
        root.iconbitmap(default=str(asset_path('taizhang.ico')))


def configure_windows_app_id():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Taizhang.Desktop')


def configure_tk_paths():
    """Use ASCII short paths for legacy Tcl's Windows environment lookup."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return
    import ctypes
    function = ctypes.windll.kernel32.GetShortPathNameW
    function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    function.restype = ctypes.c_uint32
    for key in ('TCL_LIBRARY', 'TK_LIBRARY'):
        value = os.environ.get(key, '')
        if not value or value.isascii():
            continue
        length = function(value, None, 0)
        if length:
            buffer = ctypes.create_unicode_buffer(length)
            if function(value, buffer, length) and buffer.value.isascii():
                os.environ[key] = buffer.value
