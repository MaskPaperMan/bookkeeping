"""Real Tk widget smoke tests; opt in on non-Windows with LEDGER_GUI_TESTS=1."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32' or os.environ.get('LEDGER_GUI_TESTS') == '1',
                     '真实 Tk 窗口测试需设置 LEDGER_GUI_TESTS=1，或在 Windows 运行')
class DesktopUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from ledger.storage import Store
        from ledger.ui import App
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.temp.name) / 'ledger.sqlite3')
        self.app = App(self.root, self.store)
        self.root.update()
        self.errors = patch('ledger.ui.messagebox.showerror').start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.store.close()
        self.root.destroy()
        self.temp.cleanup()

    def form(self):
        from ledger.ui import Form
        return next(child for child in self.root.winfo_children() if isinstance(child, Form))

    def settle(self):
        self.root.update_idletasks()
        self.root.update()

    def test_customer_and_entry_forms(self):
        self.app.new_customer()
        form = self.form()
        form.values['name'].set('界面客户')
        form.save()
        self.settle()
        customer_id = self.app.current_id
        self.assertIsNotNone(customer_id)
        for action, amount in [('sale', '1000'), ('repayment', '400'), ('recharge', '300')]:
            self.app.new_entry(action)
            form = self.form()
            if action == 'sale':
                form.credit.set(True)
            form.values['amount'].set(amount)
            form.save()
            self.settle()
        self.assertEqual(len(self.app.transactions.get_children()), 3)
        self.assertEqual(self.store.balance(customer_id)['receivable'], 60000)
        self.assertEqual(self.store.balance(customer_id)['advance_received'], 30000)
        self.assertEqual(tuple(self.app.clients['columns']),
                         ('name', 'phone', 'active', 'receivable', 'advance_received'))
        self.errors.assert_not_called()

    def test_search_debt_filter_and_create_customer_selection(self):
        customer_id = self.store.save_customer('有欠款客户')
        self.store.add_entry(customer_id, '2026-10-27', 'receivable', '100')
        self.app.only_debt.set(True)
        self.app.query.set('有欠款')
        self.settle()
        self.assertEqual(len(self.app.clients.get_children()), 1)
        self.app.new_customer()
        form = self.form()
        form.values['name'].set('新客户')
        form.save()
        self.settle()
        self.assertEqual(self.store.customer(self.app.current_id)['name'], '新客户')
        self.assertFalse(self.app.only_debt.get())
        self.assertEqual(self.app.query.get(), '')
        self.errors.assert_not_called()

    def test_kind_filter_and_reset(self):
        customer_id = self.store.save_customer('筛选客户')
        self.store.add_entry(customer_id, '2026-10-01', 'receivable', '100')
        self.store.add_entry(customer_id, '2026-10-02', 'receive', '50')
        self.app.current_id = customer_id
        self.app.refresh()
        self.settle()
        self.app.kind.set('还款')
        self.app.refresh_detail()
        self.assertEqual(len(self.app.transactions.get_children()), 1)
        self.app.reset_filters()
        self.assertEqual(len(self.app.transactions.get_children()), 2)
        self.errors.assert_not_called()

    def test_main_controls_are_visible(self):
        from tkinter import ttk
        self.settle()
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        controls = list(children(self.root))
        titles = {widget.cget('text') for widget in controls
                  if isinstance(widget, ttk.Button) and widget.winfo_ismapped()}
        self.assertTrue({'新增客户', '收款', '预付充值', '还款', '备份账本', '恢复账本'}.issubset(titles))
        self.assertNotIn('新增往来', titles)
        self.assertTrue(self.app.clients.winfo_ismapped())
        self.assertTrue(self.app.transactions.winfo_ismapped())
        self.assertGreater(self.app.clients.winfo_height(), 40)
        self.assertGreater(self.app.transactions.winfo_height(), 40)
        self.errors.assert_not_called()

    def test_prepaid_sale_and_mutually_exclusive_checkboxes(self):
        from tkinter import ttk
        customer_id = self.store.save_customer('预付客户')
        self.store.record_transaction(customer_id, '2026-10-01', 'recharge', '100')
        self.app.current_id = customer_id
        self.app.refresh()
        self.settle()
        self.app.new_entry('sale')
        form = self.form()
        def descend(widget):
            for child in widget.winfo_children():
                yield child
                yield from descend(child)
        boxes = [w for w in descend(form) if isinstance(w, ttk.Checkbutton)]
        boxes[0].invoke()
        self.assertTrue(form.credit.get())
        boxes[1].invoke()
        self.assertTrue(form.use_prepaid.get())
        self.assertFalse(form.credit.get())
        self.assertNotIn('kind', form.values)
        form.values['amount'].set('60')
        form.save()
        self.settle()
        result = self.store.balance(customer_id)
        self.assertEqual(result['advance_received'], 4000)
        self.assertEqual(result['receivable'], 0)
        self.errors.assert_not_called()

    def test_prepaid_shortfall_requires_choice_then_creates_one_sale(self):
        customer_id = self.store.save_customer('差额客户')
        self.store.record_transaction(customer_id, '2026-10-01', 'recharge', '50')
        self.app.current_id = customer_id
        self.app.refresh()
        self.settle()
        self.app.new_entry('sale')
        form = self.form()
        form.values['amount'].set('80')
        form.use_prepaid.set(True)
        self.settle()
        self.assertTrue(form.shortfall_frame.winfo_ismapped())
        self.assertIn('50.00', form.preview_label.cget('text'))
        self.assertIn('30.00', form.preview_label.cget('text'))
        form.save()
        self.errors.assert_called_once()
        self.assertTrue(form.winfo_exists())
        self.assertEqual(len(self.store.entries(customer_id)), 1)
        self.errors.reset_mock()
        form.shortfall.set('credit')
        form.save()
        self.settle()
        self.assertEqual(len(self.store.entries(customer_id)), 2)
        self.assertEqual(self.store.balance(customer_id)['receivable'], 3000)
        self.assertEqual(self.store.balance(customer_id)['advance_received'], 0)
        self.errors.assert_not_called()
