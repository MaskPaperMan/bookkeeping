import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, simpledialog, ttk

from .domain import LedgerError, money
from .ui import Form, guarded, make_table


class ExpensePage:
    def __init__(self, root, parent, store):
        self.root = root
        self.store = store
        self.rows = []
        self.category_map = {}
        ttk.Label(parent, text='支出记录', style='Title.TLabel').pack(anchor='w', pady=(8, 0))
        self.summary = ttk.Label(parent, padding=(0, 12), wraplength=850)
        self.summary.pack(fill='x')
        actions = ttk.Frame(parent)
        actions.pack(fill='x', pady=(0, 10))
        for text, callback in [('新增支出', self.new_expense), ('管理类别', self.manage_categories),
                               ('作废选中支出', self.void_expense), ('导出当前支出', self.export)]:
            ttk.Button(actions, text=text, command=callback).pack(side='left', padx=(0, 6))
        filters = ttk.Frame(parent)
        filters.pack(fill='x', pady=(0, 6))
        self.start, self.end = tk.StringVar(), tk.StringVar()
        self.category = tk.StringVar(value='全部类别')
        self.query = tk.StringVar()
        for text, variable in [('起日', self.start), ('止日', self.end)]:
            ttk.Label(filters, text=text).pack(side='left', padx=(0, 4))
            ttk.Entry(filters, textvariable=variable, width=12).pack(side='left', padx=(0, 8))
        ttk.Label(filters, text='类别').pack(side='left', padx=(0, 4))
        self.category_box = ttk.Combobox(filters, textvariable=self.category, state='readonly', width=18)
        self.category_box.pack(side='left', padx=(0, 8))
        ttk.Button(filters, text='筛选', command=self.refresh).pack(side='left', padx=(0, 6))
        ttk.Button(filters, text='重置', command=self.reset).pack(side='left')
        search = ttk.Frame(parent)
        search.pack(fill='x', pady=(0, 10))
        ttk.Label(search, text='收款方 / 备注').pack(side='left', padx=(0, 5))
        query = ttk.Entry(search, textvariable=self.query, width=32)
        query.pack(side='left')
        query.bind('<Return>', lambda event: self.refresh())
        ttk.Button(search, text='搜索', command=self.refresh).pack(side='left', padx=6)
        ttk.Label(search, text='日期 YYYY-MM-DD；留空表示不限', foreground='#666666').pack(side='left', padx=8)
        self.table = make_table(parent, [('id', '支出编号', 80), ('day', '日期', 105),
            ('category', '类别', 100), ('amount', '金额（元）', 115), ('payee', '收款方', 165),
            ('note', '备注', 210), ('status', '状态', 75), ('reason', '作废原因', 180)])
        self.category_summary = ttk.Label(parent, wraplength=850, padding=(0, 10))
        self.category_summary.pack(fill='x')
        self.refresh()

    def filtered_rows(self):
        return self.store.expenses(self.start.get().strip(), self.end.get().strip(),
            self.category_map.get(self.category.get()), self.query.get())

    @guarded
    def refresh(self):
        self.category_map = {row['name']: row['id']
                             for row in self.store.expense_categories(include_inactive=True)}
        self.category_box.configure(values=['全部类别'] + list(self.category_map))
        if self.category.get() != '全部类别' and self.category.get() not in self.category_map:
            self.category.set('全部类别')
        rows = self.filtered_rows()
        summary = self.store.expense_summary(rows)
        self.rows = rows
        self.summary.configure(text='本月支出：¥ {}    当前筛选合计：¥ {}    有效支出：{} 笔'.format(
            money(summary['month']), money(summary['filtered']), summary['count']))
        self.category_summary.configure(text='分类合计（当前筛选，不含作废）：' +
            ('    '.join(name + ' ¥ ' + money(total) for name, total in summary['categories'].items())
             or '暂无有效支出'))
        self.table.delete(*self.table.get_children())
        for row in rows:
            self.table.insert('', 'end', iid=str(row['id']), values=(row['id'], row['day'],
                row['category'], money(row['cents']), row['payee'], row['note'],
                '已作废' if row['voided'] else '有效', row['void_reason']),
                tags=('inactive',) if row['voided'] else ())

    def reset(self):
        self.start.set('')
        self.end.set('')
        self.category.set('全部类别')
        self.query.set('')
        self.refresh()

    @guarded
    def new_expense(self):
        categories = {row['name']: row['id'] for row in self.store.expense_categories()}
        if not categories:
            raise LedgerError('请先点击“管理类别”添加支出类别。')
        def save(values):
            category_id = categories.get(values['category'])
            self.store.add_expense(values['day'].strip(), category_id, values['amount'],
                                   values['payee'], values['note'])
            self.reset()
        selected = self.category.get() if self.category.get() in categories else next(iter(categories))
        Form(self.root, '新增支出', [
            ('day', '付款日期 *', date.today().isoformat(), None),
            ('amount', '金额（元）*', '', None),
            ('category', '支出类别 *', selected, list(categories)),
            ('payee', '收款方', '', self.store.expense_payees()),
            ('note', '备注', '', None)], save, editable_fields=('payee',),
            helper='只记录已实际支付的金额。收款方可直接填写或选择历史记录。\n日期格式：YYYY-MM-DD。')

    @guarded
    def void_expense(self):
        selection = self.table.selection()
        if not selection:
            raise LedgerError('请选择要作废的支出。')
        reason = simpledialog.askstring('作废支出', '请输入作废原因（记录会保留）：', parent=self.root)
        if reason is not None:
            self.store.void_expense(int(selection[0]), reason)
            self.refresh()

    @guarded
    def export(self):
        rows = self.filtered_rows()  # 导出前重新校验筛选，不导出过期缓存。
        target = filedialog.asksaveasfilename(parent=self.root, title='导出当前筛选的支出',
            defaultextension='.csv', initialfile='支出流水.csv', filetypes=[('CSV 表格', '*.csv')])
        if target:
            self.store.export_expenses(target, rows)
            messagebox.showinfo('导出成功', '已导出当前筛选的支出（包含作废记录）。', parent=self.root)

    def manage_categories(self):
        CategoryDialog(self.root, self.store, self.refresh)


class CategoryDialog(tk.Toplevel):
    def __init__(self, parent, store, on_change):
        super().__init__(parent)
        self.root = self
        self.store = store
        self.on_change = on_change
        self.title('管理支出类别')
        self.transient(parent)
        self.geometry('480x460')
        self.minsize(400, 340)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1, minsize=90)
        # Only the list expands; fixed control rows always retain their requested size.
        list_area = ttk.Frame(body)
        list_area.grid(row=0, column=0, sticky='nsew')
        self.table = make_table(list_area, [('name', '可用支出类别', 300)])
        entry = ttk.Frame(body)
        entry.grid(row=1, column=0, sticky='ew', pady=10)
        self.name = tk.StringVar()
        self.name_entry = ttk.Entry(entry, textvariable=self.name, width=28)
        self.name_entry.pack(side='left', fill='x', expand=True)
        self.add_button = ttk.Button(entry, text='添加', command=self.add)
        self.add_button.pack(side='left', padx=(8, 0))
        ttk.Label(body, text='已有支出的类别删除后将停用，历史记录保留。\n重新添加同名类别可恢复使用。',
                  wraplength=340).grid(row=2, column=0, sticky='w', pady=(0, 10))
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky='ew')
        self.delete_button = ttk.Button(buttons, text='删除选中类别', command=self.remove)
        self.delete_button.pack(side='left')
        self.close_button = ttk.Button(buttons, text='关闭', command=self.destroy)
        self.close_button.pack(side='right')
        self.bind('<Escape>', lambda event: self.destroy())
        self.refresh()
        self.grab_set()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for row in self.store.expense_categories():
            self.table.insert('', 'end', iid=str(row['id']), values=(row['name'],))

    @guarded
    def add(self):
        self.store.add_expense_category(self.name.get())
        self.name.set('')
        self.refresh()
        self.on_change()

    @guarded
    def remove(self):
        selection = self.table.selection()
        if not selection:
            raise LedgerError('请选择要删除的类别。')
        if messagebox.askyesno('删除类别', '删除选中的类别？已有支出的类别会停用，历史记录保留。', parent=self):
            self.store.remove_expense_category(int(selection[0]))
            self.refresh()
            self.on_change()
