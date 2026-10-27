import sys
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
