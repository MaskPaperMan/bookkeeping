"""Actual cash expenses, independent of customer income and balances."""
import csv
from datetime import date, datetime

from .domain import LedgerError, MAX_CENTS, money, parse_amount, validate_date

DEFAULT_CATEGORIES = ('进货', '运费', '房租', '水电', '工资', '其他')
EXPENSE_DDL = (
    '''CREATE TABLE expense_categories (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) > 0),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)))''',
    '''CREATE TABLE expenses (
        id INTEGER PRIMARY KEY, day TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES expense_categories(id),
        cents INTEGER NOT NULL CHECK(cents > 0), payee TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1)),
        void_reason TEXT NOT NULL DEFAULT '', voided_at TEXT NOT NULL DEFAULT '')''',
    'CREATE INDEX expenses_day ON expenses(day,id)',
)


def seed_categories(db):
    db.executemany('INSERT INTO expense_categories(name) VALUES(?)',
                   [(name,) for name in DEFAULT_CATEGORIES])


def validate_expenses(db):
    required = {
        'expense_categories': {'id', 'name', 'active'},
        'expenses': {'id', 'day', 'category_id', 'cents', 'payee', 'note',
                     'created_at', 'voided', 'void_reason', 'voided_at'},
    }
    for table, columns in required.items():
        actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
        if not columns.issubset(actual):
            raise LedgerError('支出账本结构不完整。')
    for row in db.execute('SELECT * FROM expense_categories'):
        if not isinstance(row['name'], str) or not row['name'].strip() or row['active'] not in (0, 1):
            raise LedgerError('支出类别数据无效。')
    if db.execute('SELECT 1 FROM expenses e LEFT JOIN expense_categories c ON c.id=e.category_id '
                  'WHERE c.id IS NULL LIMIT 1').fetchone():
        raise LedgerError('支出流水关联的类别不存在。')
    for row in db.execute('SELECT * FROM expenses'):
        validate_date(row['day'])
        if (type(row['cents']) is not int or not 0 < row['cents'] <= MAX_CENTS
                or row['voided'] not in (0, 1)
                or any(not isinstance(row[key], str) for key in
                       ('payee', 'note', 'created_at', 'void_reason', 'voided_at'))
                or (row['voided'] and not row['void_reason'].strip())):
            raise LedgerError('支出流水数据无效。')


class ExpenseStore:
    def expense_categories(self, include_inactive=False):
        sql = 'SELECT * FROM expense_categories'
        if not include_inactive:
            sql += ' WHERE active=1'
        return [dict(row) for row in self.db.execute(sql + ' ORDER BY id')]

    def add_expense_category(self, name):
        name = name.strip()
        if not name:
            raise LedgerError('类别名称不能为空。')
        if name == '全部类别':
            raise LedgerError('“全部类别”用于筛选，请使用其他类别名称。')
        with self.transaction():
            existing = self.db.execute('SELECT * FROM expense_categories WHERE name=?', (name,)).fetchone()
            if existing:
                if existing['active']:
                    raise LedgerError('该类别已经存在。')
                self.db.execute('UPDATE expense_categories SET active=1 WHERE id=?', (existing['id'],))
                return existing['id']
            return self.db.execute('INSERT INTO expense_categories(name) VALUES(?)', (name,)).lastrowid

    def remove_expense_category(self, category_id):
        with self.transaction():
            if not self.db.execute('SELECT 1 FROM expense_categories WHERE id=?', (category_id,)).fetchone():
                raise LedgerError('类别不存在。')
            if self.db.execute('SELECT 1 FROM expenses WHERE category_id=?', (category_id,)).fetchone():
                self.db.execute('UPDATE expense_categories SET active=0 WHERE id=?', (category_id,))
            else:
                self.db.execute('DELETE FROM expense_categories WHERE id=?', (category_id,))

    def add_expense(self, day, category_id, amount, payee='', note=''):
        validate_date(day)
        cents = parse_amount(amount)
        with self.transaction():
            if not self.db.execute('SELECT 1 FROM expense_categories WHERE id=? AND active=1',
                                   (category_id,)).fetchone():
                raise LedgerError('请选择可用的支出类别。')
            return self.db.execute('''INSERT INTO expenses
                (day,category_id,cents,payee,note,created_at) VALUES(?,?,?,?,?,?)''',
                (day, category_id, cents, payee.strip(), note.strip(),
                 datetime.now().isoformat(timespec='seconds'))).lastrowid

    def expenses(self, start='', end='', category_id=None, query=''):
        if start:
            validate_date(start)
        if end:
            validate_date(end)
        if start and end and start > end:
            raise LedgerError('起日不能晚于止日。')
        clauses, params = [], []
        if start:
            clauses.append('e.day>=?')
            params.append(start)
        if end:
            clauses.append('e.day<=?')
            params.append(end)
        if category_id is not None:
            clauses.append('e.category_id=?')
            params.append(category_id)
        sql = 'SELECT e.*,c.name AS category FROM expenses e JOIN expense_categories c ON c.id=e.category_id'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        rows = [dict(row) for row in self.db.execute(sql + ' ORDER BY e.day DESC,e.id DESC', params)]
        text = query.strip().casefold()
        return [row for row in rows if not text or text in (row['payee'] + ' ' + row['note']).casefold()]

    def expense_payees(self):
        return [row[0] for row in self.db.execute(
            "SELECT payee FROM expenses WHERE payee<>'' AND voided=0 GROUP BY payee ORDER BY MAX(id) DESC")]

    def void_expense(self, expense_id, reason):
        if not reason.strip():
            raise LedgerError('请填写作废原因。')
        with self.transaction():
            row = self.db.execute('SELECT * FROM expenses WHERE id=?', (expense_id,)).fetchone()
            if row is None or row['voided']:
                raise LedgerError('该支出不存在或已经作废。')
            self.db.execute('UPDATE expenses SET voided=1,void_reason=?,voided_at=? WHERE id=?',
                            (reason.strip(), datetime.now().isoformat(timespec='seconds'), expense_id))

    def expense_summary(self, rows, today=None):
        today = today or date.today()
        month = today.strftime('%Y-%m')
        month_total = sum(row['cents'] for row in self.expenses() if not row['voided'] and row['day'][:7] == month)
        by_category = {}
        count = 0
        for row in rows:
            if not row['voided']:
                by_category[row['category']] = by_category.get(row['category'], 0) + row['cents']
                count += 1
        return {'month': month_total, 'filtered': sum(by_category.values()),
                'count': count, 'categories': by_category}

    def export_expenses(self, target, rows=None):
        # Late import avoids a cycle with Store's income export helpers.
        from .storage import safe_csv
        self._check_export_target(target)
        with open(str(target), 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['支出编号', '日期', '类别', '金额', '收款方', '备注',
                             '状态', '作废原因', '录入时间', '作废时间'])
            for row in self.expenses() if rows is None else rows:
                writer.writerow([row['id'], row['day'], safe_csv(row['category']),
                    money(row['cents']).replace(',', ''), safe_csv(row['payee']), safe_csv(row['note']),
                    '已作废' if row['voided'] else '有效', safe_csv(row['void_reason']),
                    row['created_at'], row['voided_at']])
