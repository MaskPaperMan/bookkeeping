from .domain import LedgerError


def check_tk_runtime(platform, tk_version):
    """Avoid Apple's obsolete Tk before it can open an unusable blank window."""
    if platform == 'darwin' and tk_version < 8.6:
        raise LedgerError(
            '当前 Python 使用 macOS 旧版 Tk 8.5，可能显示空白窗口。\n'
            '请在项目目录执行 uv sync --python 3.12，再用 uv run main.py 启动。')
