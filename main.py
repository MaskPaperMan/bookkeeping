import logging
import argparse
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox

from ledger.storage import InstanceLock, Store, data_directory
from ledger.ui import App
from ledger.runtime import check_tk_runtime
from ledger.domain import LedgerError


def main(smoke_result=None):
    try:
        check_tk_runtime(sys.platform, tk.TkVersion)
    except LedgerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    root = tk.Tk()
    root.withdraw()
    lock = None
    store = None
    try:
        folder = data_directory()
        folder.mkdir(parents=True, exist_ok=True)
        lock = InstanceLock(folder / 'ledger.lock')
        logging.basicConfig(level=logging.ERROR,
                            handlers=[RotatingFileHandler(str(folder / 'errors.log'),
                                      maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')],
                            format='%(asctime)s %(levelname)s %(message)s')
        store = Store(folder / 'ledger.sqlite3')
        try:
            store.automatic_backup()
        except Exception as exc:
            logging.exception('自动备份失败')
            messagebox.showwarning('自动备份失败', '账本仍可使用，请尽快手动备份。\n' + str(exc), parent=root)
        def callback_error(kind, value, traceback):
            logging.error('界面异常', exc_info=(kind, value, traceback))
            messagebox.showerror('操作失败', '发生错误，请重新检查操作。\n' + str(value), parent=root)
        root.report_callback_exception = callback_error
        app = App(root, store)
        root.deiconify()
        if smoke_result:
            def finish_smoke_test():
                if not app.clients.winfo_ismapped() or not app.transactions.winfo_ismapped():
                    raise RuntimeError('Application widgets did not become visible')
                Path(smoke_result).write_text('ok', encoding='ascii')
                root.quit()
            root.after(500, finish_smoke_test)
        root.mainloop()
        return 0
    except Exception as exc:
        logging.exception('启动失败')
        messagebox.showerror('无法打开账本', str(exc), parent=root)
        return 1
    finally:
        if store is not None:
            store.close()
        if lock is not None:
            lock.close()
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='客户往来记账')
    parser.add_argument('--smoke-test', help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    sys.exit(main(arguments.smoke_test))
