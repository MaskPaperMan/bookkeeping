import functools
import logging
import sqlite3
import sys
import tkinter as tk
from datetime import date, datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

from .domain import (CUSTOMER_ACCOUNTS, CUSTOMER_LABELS, KINDS, LedgerError,
                     entry_category, money, validate_date)
from .storage import sale_breakdown
from .branding import APP_NAME, apply_branding


def guarded(function):
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except (LedgerError, sqlite3.Error, OSError) as exc:
            logging.exception('操作失败')
            messagebox.showerror('操作未完成', str(exc), parent=self.root)
    return wrapped


class Form(tk.Toplevel):
    def __init__(self, parent, title, fields, on_save, extra_builder=None,
                 helper='金额单位：元；日期格式：YYYY-MM-DD', editable_fields=()):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.on_save = on_save
        self.values = {}
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        first = None
        for index, (key, label, initial, options) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky='w', pady=7, padx=(0, 15))
            var = tk.StringVar(value=initial)
            self.values[key] = var
            if options:
                widget = ttk.Combobox(body, textvariable=var, values=options,
                                      state='normal' if key in editable_fields else 'readonly', width=36)
            else:
                widget = ttk.Entry(body, textvariable=var, width=39)
            widget.grid(row=index, column=1, sticky='ew', pady=7)
            if first is None:
                first = widget
        extra = ttk.Frame(body)
        extra.grid(row=len(fields), column=0, columnspan=2, sticky='ew')
        if extra_builder:
            extra_builder(extra)
        ttk.Label(body, text=helper, foreground='#666666', wraplength=430).grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky='w', pady=10)
        buttons = ttk.Frame(body)
        buttons.grid(row=len(fields) + 2, column=0, columnspan=2, sticky='e')
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='left', padx=6)
        ttk.Button(buttons, text='保存', command=self.save).pack(side='left')
        self.bind('<Escape>', lambda event: self.destroy())
        self.bind('<Return>', lambda event: self.save())
        self.grab_set()
        if first:
            first.focus_set()

    def save(self):
        try:
            self.on_save({key: value.get() for key, value in self.values.items()})
        except (LedgerError, sqlite3.Error, OSError) as exc:
            messagebox.showerror('无法保存', str(exc), parent=self)
            return
        self.destroy()


class SaleForm(Form):
    def __init__(self, parent, customer, balance, on_save, preview):
        self.credit = tk.BooleanVar(master=parent)
        self.use_prepaid = tk.BooleanVar(master=parent)
        self.shortfall = tk.StringVar(master=parent)
        self.preview = preview

        def options(frame):
            ttk.Label(frame, text='客户欠款：¥ {}    预付金：¥ {}'.format(
                money(balance['receivable']), money(balance['advance_received']))).pack(anchor='w', pady=6)
            self.credit_box = ttk.Checkbutton(frame, text='赊账（记为客户欠款）', variable=self.credit)
            self.credit_box.pack(anchor='w', pady=3)
            self.prepaid_box = ttk.Checkbutton(frame, text='预付金抵扣', variable=self.use_prepaid)
            self.prepaid_box.pack(anchor='w', pady=3)
            self.preview_label = ttk.Label(frame, wraplength=430)
            self.preview_label.pack(anchor='w', pady=6)
            self.shortfall_frame = ttk.Frame(frame)
            ttk.Label(self.shortfall_frame, text='剩余金额如何处理？').pack(anchor='w')
            ttk.Radiobutton(self.shortfall_frame, text='现金收款', variable=self.shortfall,
                            value='cash').pack(side='left', padx=(0, 12))
            ttk.Radiobutton(self.shortfall_frame, text='记为欠款', variable=self.shortfall,
                            value='credit').pack(side='left')

        def save(values):
            remainder = None
            if self.use_prepaid.get():
                split = self.preview(values['day'].strip(), values['amount'])
                if split['remainder']:
                    remainder = self.shortfall.get()
                    if remainder not in ('cash', 'credit'):
                        raise LedgerError('请选择剩余金额用现金收款还是记为欠款。')
            on_save(values, self.credit.get() if not self.use_prepaid.get() else False,
                    self.use_prepaid.get(), remainder)

        super().__init__(parent, '收款 — ' + customer['name'], [
            ('day', '日期 *', date.today().isoformat(), None),
            ('amount', '金额（元）*', '', None), ('note', '备注', '', None)], save,
            extra_builder=options,
            helper='不勾选：现金收款。勾选赊账：全额记为欠款。\n'
                   '预付金先抵扣，不足部分再选择现金收款或记为欠款。')
        for key in ('day', 'amount'):
            self.values[key].trace_add('write', lambda *args: self.update_preview())
        self.use_prepaid.trace_add('write', lambda *args: self.toggle_prepaid())
        self.update_preview()

    def toggle_prepaid(self):
        if self.use_prepaid.get():
            self.credit.set(False)
            self.credit_box.pack_forget()
        else:
            self.credit_box.pack(before=self.prepaid_box, anchor='w', pady=3)
        self.shortfall.set('')
        self.update_preview()

    def update_preview(self):
        self.shortfall_frame.pack_forget()
        if not self.use_prepaid.get():
            self.preview_label.configure(text='')
            return
        try:
            split = self.preview(self.values['day'].get().strip(), self.values['amount'].get())
        except LedgerError:
            self.preview_label.configure(text='输入有效日期和金额后，显示抵扣明细。')
            return
        self.preview_label.configure(text='预付金抵扣：¥ {}    剩余：¥ {}'.format(
            money(split['prepaid']), money(split['remainder'])))
        if split['remainder']:
            self.shortfall_frame.pack(anchor='w', pady=4)


def make_table(parent, columns):
    frame = ttk.Frame(parent)
    frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(frame, columns=[column[0] for column in columns], show='headings', selectmode='browse')
    for key, title, width in columns:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=65, anchor='w')
    vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree.tag_configure('inactive', foreground='#777777')
    return tree


class App:
    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.current_id = None
        self.customer_rows = []
        self.entry_rows = []
        apply_branding(root)
        root.geometry('1200x780')
        root.minsize(900, 620)
        style = ttk.Style(root)
        theme = 'aqua' if sys.platform == 'darwin' else 'clam'
        if theme in style.theme_names():
            style.theme_use(theme)
        style.configure('Treeview', rowheight=30)
        style.configure('TButton', padding=(9, 5))
        default_font = tkfont.nametofont('TkDefaultFont')
        style.configure('Title.TLabel', font=(default_font.actual('family'), 15, 'bold'))
        outer = ttk.Frame(root, padding=14)
        outer.pack(fill='both', expand=True)
        top = ttk.Frame(outer)
        top.pack(fill='x')
        ttk.Label(top, text=APP_NAME, style='Title.TLabel').pack(side='left')
        for title, callback in [('恢复账本', self.restore), ('备份账本', self.backup)]:
            ttk.Button(top, text=title, command=callback).pack(side='right', padx=3)
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both', expand=True, pady=(12, 0))
        self.income_tab = ttk.Frame(self.notebook, padding=8)
        self.expense_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.income_tab, text='收入')
        self.notebook.add(self.expense_tab, text='支出')
        self.summary = ttk.Label(self.income_tab, padding=(0, 12), wraplength=850)
        self.summary.pack(fill='x')
        search = ttk.Frame(self.income_tab)
        search.pack(fill='x', pady=(0, 8))
        ttk.Label(search, text='客户搜索').pack(side='left')
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query, width=24).pack(side='left', padx=6)
        self.only_debt = tk.BooleanVar()
        self.show_inactive = tk.BooleanVar(value=True)
        ttk.Checkbutton(search, text='只看有欠款', variable=self.only_debt, command=self.refresh).pack(side='left')
        ttk.Checkbutton(search, text='包含停用客户', variable=self.show_inactive, command=self.refresh).pack(side='left', padx=8)
        customer_actions = ttk.Frame(self.income_tab)
        customer_actions.pack(fill='x', pady=(0, 8))
        for title, callback in [('新增客户', self.new_customer), ('编辑客户', self.edit_customer),
                                ('停用 / 启用', self.toggle_customer), ('删除空客户', self.delete_customer),
                                ('导出客户汇总', self.export_customers)]:
            ttk.Button(customer_actions, text=title, command=callback).pack(side='left', padx=2)
        pane = ttk.Panedwindow(self.income_tab, orient='vertical')
        pane.pack(fill='both', expand=True)
        clients = ttk.Frame(pane)
        pane.add(clients, weight=2)
        self.clients = make_table(clients, [('name', '客户名称', 180), ('phone', '电话', 130),
                                  ('active', '状态', 65)] +
                                  [(key, label + '（元）', 180) for key, label in zip(CUSTOMER_ACCOUNTS, CUSTOMER_LABELS)])
        self.clients.bind('<<TreeviewSelect>>', self.select_customer)
        detail = ttk.Frame(pane, padding=(0, 10, 0, 0))
        pane.add(detail, weight=3)
        self.detail_title = ttk.Label(detail, text='选择客户查看账本', style='Title.TLabel')
        self.detail_title.pack(anchor='w')
        self.detail_meta = ttk.Label(detail, text='选择客户后，可收款、预付充值或还款。', wraplength=850)
        self.detail_meta.pack(anchor='w', pady=(4, 8))
        actions = ttk.Frame(detail)
        actions.pack(fill='x', pady=(0, 7))
        for title, callback in [('收款', lambda: self.new_entry('sale')),
                                ('还款', lambda: self.new_entry('repayment')),
                                ('预付充值', lambda: self.new_entry('recharge')),
                                ('作废选中流水', self.void_entry),
                                ('导出当前流水', self.export_entries)]:
            ttk.Button(actions, text=title, command=callback).pack(side='left', padx=(0, 5))
        self.start = tk.StringVar()
        self.end = tk.StringVar()
        self.kind = tk.StringVar(value='全部类型')
        filters = ttk.Frame(detail)
        filters.pack(fill='x', pady=(0, 7))
        ttk.Label(filters, text='起日').pack(side='left', padx=(0, 3))
        ttk.Entry(filters, textvariable=self.start, width=11).pack(side='left')
        ttk.Label(filters, text='止日').pack(side='left', padx=3)
        ttk.Entry(filters, textvariable=self.end, width=11).pack(side='left')
        ttk.Combobox(filters, textvariable=self.kind, values=['全部类型', '收款', '预付充值', '还款', '历史记录'],
                     state='readonly', width=17).pack(side='left', padx=5)
        ttk.Button(filters, text='筛选', command=self.refresh_detail).pack(side='left')
        ttk.Button(filters, text='重置', command=self.reset_filters).pack(side='left', padx=3)
        self.transactions = make_table(detail, [('id', '编号', 65), ('day', '日期', 100),
            ('kind', '类型', 180), ('amount', '金额（元）', 110),
            ('settlement', '结算明细（元）', 260), ('note', '备注', 180),
            ('status', '状态', 70), ('reason', '作废原因', 150)])
        ttk.Label(outer, text='本地账本：' + str(store.path), foreground='#666666').pack(anchor='w', pady=(8, 0))
        from .expense_ui import ExpensePage
        self.expense_page = ExpensePage(root, self.expense_tab, store)
        self.notebook.bind('<<NotebookTabChanged>>', lambda event: self.expense_page.refresh()
                           if self.notebook.select() == str(self.expense_tab) else None)
        self.query.trace_add('write', lambda *args: self.refresh())
        self.refresh()

    def require_customer(self):
        if self.current_id is None:
            raise LedgerError('请先选择一个客户。')
        return self.store.customer(self.current_id)

    @guarded
    def refresh(self):
        rows = self.store.customers()
        totals = [sum(row[key] for row in rows) for key in CUSTOMER_ACCOUNTS]
        self.summary.configure(text='全账本（含停用客户）  |  ' + '    '.join(
            label + '：¥ ' + money(total) for label, total in zip(CUSTOMER_LABELS, totals)))
        query = self.query.get().strip().casefold()
        self.customer_rows = [row for row in rows
            if (self.show_inactive.get() or row['active'])
            and (not self.only_debt.get() or row['receivable'])
            and (not query or query in (row['name'] + ' ' + row['phone'] + ' ' + row['note']).casefold())]
        self.clients.delete(*self.clients.get_children())
        for row in self.customer_rows:
            self.clients.insert('', 'end', iid=str(row['id']), values=[row['name'], row['phone'],
                '启用' if row['active'] else '停用'] + [money(row[key]) for key in CUSTOMER_ACCOUNTS],
                tags=() if row['active'] else ('inactive',))
        if self.current_id is not None and self.clients.exists(str(self.current_id)):
            self.clients.selection_set(str(self.current_id))
        else:
            self.current_id = None
        self.refresh_detail()

    @guarded
    def select_customer(self, event=None):
        selected = self.clients.selection()
        new_id = int(selected[0]) if selected else None
        if new_id != self.current_id:
            self.current_id = new_id
            self.start.set('')
            self.end.set('')
            self.kind.set('全部类型')
        self.refresh_detail()

    @guarded
    def refresh_detail(self):
        start, end = self.start.get().strip(), self.end.get().strip()
        if start:
            validate_date(start)
        if end:
            validate_date(end)
        if start and end and start > end:
            raise LedgerError('起日不能晚于止日。')
        self.transactions.delete(*self.transactions.get_children())
        self.entry_rows = []
        if self.current_id is None:
            self.detail_title.configure(text='选择客户查看账本')
            self.detail_meta.configure(text='选择客户后，可收款、预付充值或还款。')
            return
        row = self.require_customer()
        self.detail_title.configure(text=row['name'] + ('' if row['active'] else '（已停用）'))
        balance = self.store.balance(self.current_id)
        self.detail_meta.configure(text='  |  '.join(label + '：¥ ' + money(balance[key])
            for key, label in zip(CUSTOMER_ACCOUNTS, CUSTOMER_LABELS)) +
            ('  |  备注：' + row['note'] if row['note'] else ''))
        self.entry_rows = [entry for entry in self.store.entries(self.current_id)
            if (not start or entry['day'] >= start) and (not end or entry['day'] <= end)
            and (self.kind.get() == '全部类型' or entry_category(entry['kind']) == self.kind.get())]
        for entry in reversed(self.entry_rows):
            prepaid, cash, credit = sale_breakdown(entry)
            settlement = '，'.join(label + ' ' + money(value) for label, value in
                (('预付抵扣', prepaid), ('现金', cash), ('欠款', credit)) if value)
            self.transactions.insert('', 'end', iid=str(entry['id']), values=(entry['customer_number'], entry['day'],
                KINDS[entry['kind']][0], money(entry['cents']), settlement, entry['note'],
                '已作废' if entry['voided'] else '有效', entry['void_reason']),
                tags=('inactive',) if entry['voided'] else ())

    def reset_filters(self):
        self.start.set('')
        self.end.set('')
        self.kind.set('全部类型')
        self.refresh_detail()

    def customer_form(self, customer=None):
        customer = customer or {}
        def save(values):
            customer_id = self.store.save_customer(values['name'], values['phone'], values['note'],
                bool(customer.get('active', True)), customer.get('id'))
            self.only_debt.set(False)
            self.show_inactive.set(True)
            self.query.set('')
            self.current_id = customer_id
            self.refresh()
        Form(self.root, '编辑客户' if customer else '新增客户', [
            ('name', '客户名称 *', customer.get('name', ''), None),
            ('phone', '电话', customer.get('phone', ''), None),
            ('note', '备注', customer.get('note', ''), None)], save, helper='客户名称必填，电话和备注选填。')

    def new_customer(self):
        self.customer_form()

    @guarded
    def edit_customer(self):
        self.customer_form(self.require_customer())

    @guarded
    def toggle_customer(self):
        row = self.require_customer()
        self.store.save_customer(row['name'], row['phone'], row['note'], not row['active'], row['id'])
        self.refresh()

    @guarded
    def delete_customer(self):
        row = self.require_customer()
        if messagebox.askyesno('删除客户', '删除“{}”？已有流水的客户无法删除。'.format(row['name']), parent=self.root):
            self.store.delete_customer(row['id'])
            self.current_id = None
            self.refresh()

    @guarded
    def new_entry(self, action='sale'):
        row = self.require_customer()
        if not row['active']:
            raise LedgerError('客户已停用，请先启用。')
        def save(values, credit=False, use_prepaid=False, remainder=None):
            self.store.record_transaction(row['id'], values['day'].strip(), action,
                values['amount'], values['note'], credit=credit, use_prepaid=use_prepaid,
                remainder=remainder)
            self.reset_filters()
            self.refresh()
        if action == 'sale':
            SaleForm(self.root, row, self.store.balance(row['id']), save,
                     lambda day, amount: self.store.preview_sale(row['id'], day, amount))
            return
        title = '预付充值' if action == 'recharge' else '还款'
        balance = self.store.balance(row['id'])
        helper = ('充值金额增加客户预付金。当前预付金：¥ ' + money(balance['advance_received'])
                  if action == 'recharge' else
                  '还款金额减少客户欠款。当前欠款：¥ ' + money(balance['receivable']))
        Form(self.root, title + ' — ' + row['name'], [
            ('day', '日期 *', date.today().isoformat(), None),
            ('amount', '金额（元）*', '', None), ('note', '备注', '', None)], save, helper=helper)

    @guarded
    def void_entry(self):
        selected = self.transactions.selection()
        if not selected:
            raise LedgerError('请选择要作废的流水。')
        entry_id = int(selected[0])
        reason = simpledialog.askstring('作废流水', '请输入作废原因（记录会保留）：', parent=self.root)
        if reason is not None:
            self.store.void_entry(entry_id, reason)
            self.refresh()

    @guarded
    def export_customers(self):
        target = filedialog.asksaveasfilename(parent=self.root, title='导出当前筛选的客户',
            defaultextension='.csv', initialfile='客户汇总.csv', filetypes=[('CSV 表格', '*.csv')])
        if target:
            self.store.export_customers(target, self.customer_rows)
            messagebox.showinfo('导出成功', '已导出当前筛选的客户汇总。', parent=self.root)

    @guarded
    def export_entries(self):
        self.require_customer()
        # 筛选无效时不可导出此前缓存的结果。
        if self.start.get().strip():
            validate_date(self.start.get().strip())
        if self.end.get().strip():
            validate_date(self.end.get().strip())
        if self.start.get().strip() and self.end.get().strip() and self.start.get().strip() > self.end.get().strip():
            raise LedgerError('起日不能晚于止日。')
        self.refresh_detail()
        target = filedialog.asksaveasfilename(parent=self.root, title='导出当前筛选的流水',
            defaultextension='.csv', initialfile='客户流水.csv', filetypes=[('CSV 表格', '*.csv')])
        if target:
            self.store.export_entries(self.current_id, target, self.entry_rows)
            messagebox.showinfo('导出成功', '已导出当前筛选的流水，包含作废记录。', parent=self.root)

    @guarded
    def backup(self):
        target = filedialog.asksaveasfilename(parent=self.root, title='备份完整账本',
            defaultextension='.sqlite3', initialfile='账本备份-' + date.today().isoformat() + '.sqlite3',
            filetypes=[('账本备份', '*.sqlite3')])
        if target:
            self.store.backup(target)
            messagebox.showinfo('备份成功', '账本已完整备份。', parent=self.root)

    @guarded
    def restore(self):
        source = filedialog.askopenfilename(parent=self.root, title='选择账本备份',
            filetypes=[('账本备份', '*.sqlite3')])
        if source and messagebox.askyesno('恢复完整账本', '恢复会替换收入、支出和类别等全部账本数据。\n'
                '程序会先备份当前账本，是否继续？', parent=self.root):
            safety = self.store.restore(source)
            self.current_id = None
            self.reset_filters()
            self.refresh()
            self.expense_page.refresh()
            messagebox.showinfo('恢复成功', '已恢复账本。恢复前备份：\n' + str(safety), parent=self.root)
