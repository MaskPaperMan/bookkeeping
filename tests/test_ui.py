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
        self.assertEqual(self.root.title(), '台账')
        self.assertEqual((self.root._app_icon.width(), self.root._app_icon.height()), (256, 256))
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

    def test_expense_tab_entry_filters_and_history_payee(self):
        from tkinter import ttk
        self.app.notebook.select(self.app.expense_tab)
        self.settle()
        page = self.app.expense_page
        self.assertTrue(page.table.winfo_ismapped())
        self.assertFalse(self.app.clients.winfo_ismapped())
        page.new_expense()
        form = self.form()
        form.values['amount'].set('20.50')
        form.values['payee'].set('供应商甲')
        form.values['note'].set('采购')
        form.save()
        self.settle()
        self.assertEqual(len(page.table.get_children()), 1)
        self.assertIn('20.50', page.summary.cget('text'))
        page.new_expense()
        form = self.form()
        def descend(widget):
            for child in widget.winfo_children():
                yield child
                yield from descend(child)
        payee_box = next(widget for widget in descend(form)
                         if isinstance(widget, ttk.Combobox) and tuple(widget['values']) == ('供应商甲',))
        self.assertEqual(str(payee_box['state']), 'normal')
        form.values['amount'].set('10')
        form.values['payee'].set('供应商乙')
        form.save()
        self.settle()
        page.query.set('甲')
        page.refresh()
        self.assertEqual(len(page.table.get_children()), 1)
        page.reset()
        self.assertEqual(len(page.table.get_children()), 2)
        self.app.notebook.select(self.app.income_tab)
        self.settle()
        self.assertTrue(self.app.clients.winfo_ismapped())
        self.assertEqual(len(self.store.customers()), 0)
        self.errors.assert_not_called()

    def test_expense_void_updates_totals_and_category_management(self):
        from ledger.expense_ui import CategoryDialog
        self.app.notebook.select(self.app.expense_tab)
        self.settle()
        page = self.app.expense_page
        page.manage_categories()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, CategoryDialog))
        dialog.name.set('测试类别')
        dialog.add()
        category = next(row for row in self.store.expense_categories() if row['name'] == '测试类别')
        dialog.destroy()
        page.category.set('测试类别')
        page.new_expense()
        form = self.form()
        form.values['amount'].set('100')
        form.save()
        self.settle()
        expense = self.store.expenses()[0]
        page.table.selection_set(str(expense['id']))
        with patch('ledger.expense_ui.simpledialog.askstring', return_value='误记'):
            page.void_expense()
        self.assertIn('0.00', page.summary.cget('text'))
        self.assertEqual(self.store.expenses()[0]['void_reason'], '误记')
        page.manage_categories()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, CategoryDialog))
        dialog.table.selection_set(str(category['id']))
        with patch('ledger.expense_ui.messagebox.askyesno', return_value=True):
            dialog.remove()
        dialog.destroy()
        self.assertNotIn(category['id'], [row['id'] for row in self.store.expense_categories()])
        self.assertEqual(self.store.expenses()[0]['category'], '测试类别')
        self.errors.assert_not_called()

    def test_restore_refreshes_expense_tab(self):
        category = self.store.expense_categories()[0]['id']
        self.store.add_expense('2026-10-27', category, '20')
        backup = self.store.path.parent / 'backup.sqlite3'
        self.store.backup(backup)
        self.store.add_expense('2026-10-27', category, '30')
        self.app.notebook.select(self.app.expense_tab)
        self.settle()
        self.assertEqual(len(self.app.expense_page.table.get_children()), 2)
        with patch('ledger.ui.filedialog.askopenfilename', return_value=str(backup)), \
                patch('ledger.ui.messagebox.askyesno', return_value=True), \
                patch('ledger.ui.messagebox.showinfo'):
            self.app.restore()
        self.settle()
        self.assertEqual(len(self.app.expense_page.table.get_children()), 1)
        self.errors.assert_not_called()

    def test_category_dialog_controls_fit_small_and_large_windows(self):
        from ledger.expense_ui import CategoryDialog
        self.app.notebook.select(self.app.expense_tab)
        self.settle()
        for index in range(20):
            self.store.add_expense_category('自定义类别{}'.format(index))
        self.app.expense_page.manage_categories()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, CategoryDialog))
        for geometry in ('480x460', '400x340', '640x550'):
            with self.subTest(geometry=geometry):
                dialog.geometry(geometry)
                self.settle()
                left, top = dialog.winfo_rootx(), dialog.winfo_rooty()
                right, bottom = left + dialog.winfo_width(), top + dialog.winfo_height()
                for widget in (dialog.name_entry, dialog.add_button, dialog.delete_button, dialog.close_button):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                    self.assertGreaterEqual(widget.winfo_rootx(), left)
                    self.assertGreaterEqual(widget.winfo_rooty(), top)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom)
                for button in (dialog.add_button, dialog.delete_button, dialog.close_button):
                    self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
                self.assertGreater(dialog.table.winfo_height(), 40)
        dialog.destroy()
        self.errors.assert_not_called()
