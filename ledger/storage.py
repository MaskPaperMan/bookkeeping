import csv
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .domain import (CUSTOMER_ACCOUNTS, CUSTOMER_LABELS,
                     KINDS, LedgerError, balances,
                     money, parse_amount, prepaid_deduction, validate_date)

SCHEMA_VERSION = 2
SCHEMA = '''
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE entries (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    day TEXT NOT NULL, kind TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents > 0),
    note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    prepaid_cents INTEGER NOT NULL DEFAULT 0 CHECK(prepaid_cents >= 0),
    voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1)),
    void_reason TEXT NOT NULL DEFAULT '', voided_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX entries_customer_day ON entries(customer_id, day, id);
PRAGMA user_version=2;
'''


def data_directory():
    if os.name == 'nt':
        root = os.environ.get('LOCALAPPDATA')
        if not root:
            raise LedgerError('无法找到 LOCALAPPDATA 用户数据目录。')
        return Path(root) / '客户往来记账'
    return Path.home() / '.customer-ledger'


class InstanceLock:
    """由操作系统持有的文件锁，进程退出自动释放，锁文件不删除。"""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(str(path), 'a+b')
        try:
            self.file.seek(0, 2)
            if self.file.tell() == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise LedgerError('账本已经打开，请先关闭另一个程序窗口。')

    def close(self):
        if not self.file.closed:
            self.file.close()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self.db = self._connect(self.path)
        try:
            if new:
                self.db.executescript(SCHEMA)
            elif self.db.execute('PRAGMA user_version').fetchone()[0] == 1:
                self._validate(self.db, allow_legacy=True)
                self.backup(self.path.parent / 'backups' / (
                    'before-upgrade-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3'))
                self._upgrade_schema(self.db)
            self._validate(self.db)
        except Exception:
            self.db.close()
            raise

    @staticmethod
    def _connect(path):
        db = sqlite3.connect(str(path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _upgrade_schema(db):
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('ALTER TABLE entries ADD COLUMN prepaid_cents INTEGER NOT NULL '
                       'DEFAULT 0 CHECK(prepaid_cents >= 0)')
            db.execute('PRAGMA user_version=2')

    @staticmethod
    def _validate(db, allow_legacy=False):
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version != SCHEMA_VERSION and not (allow_legacy and version == 1):
            raise LedgerError('账本版本不兼容，无法打开或恢复。')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise LedgerError('账本文件损坏。')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise LedgerError('账本客户关联损坏。')
        required = {
            'customers': {'id', 'name', 'phone', 'note', 'active'},
            'entries': {'id', 'customer_id', 'day', 'kind', 'cents', 'note',
                        'created_at', 'voided', 'void_reason', 'voided_at'},
        }
        if version == 2:
            required['entries'].add('prepaid_cents')
        for table, columns in required.items():
            actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
            if not columns.issubset(actual):
                raise LedgerError('账本结构不完整，无法打开或恢复。')
        if db.execute('SELECT 1 FROM entries e LEFT JOIN customers c ON c.id=e.customer_id '
                      'WHERE c.id IS NULL LIMIT 1').fetchone():
            raise LedgerError('流水关联的客户不存在。')
        customers = db.execute('SELECT id,name,phone,note,active FROM customers').fetchall()
        for customer in customers:
            if not customer['name'].strip() or customer['active'] not in (0, 1):
                raise LedgerError('客户数据无效。')
            rows = db.execute('SELECT * FROM entries WHERE customer_id=?',
                              (customer['id'],)).fetchall()
            for row in rows:
                validate_date(row['day'])
                if (row['kind'] not in KINDS or type(row['cents']) is not int
                        or row['cents'] <= 0 or row['voided'] not in (0, 1)
                        or (row['voided'] and not row['void_reason'].strip())):
                    raise LedgerError('流水数据无效。')
                if version == 1 and row['kind'] in ('sale_split_cash', 'sale_split_credit'):
                    raise LedgerError('旧版账本含不兼容的抵扣记录。')
                if version == 2:
                    prepaid = row['prepaid_cents']
                    if (type(prepaid) is not int or prepaid < 0 or prepaid > row['cents']
                            or (row['kind'] not in ('sale_split_cash', 'sale_split_credit') and prepaid)
                            or (row['kind'] in ('sale_split_cash', 'sale_split_credit') and prepaid == row['cents'])):
                        raise LedgerError('流水抵扣数据无效。')
            balances(rows)

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def customer(self, customer_id):
        row = self.db.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
        if row is None:
            raise LedgerError('客户不存在。')
        return dict(row)

    def save_customer(self, name, phone='', note='', active=True, customer_id=None):
        name = name.strip()
        if not name:
            raise LedgerError('客户名称不能为空。')
        with self.transaction():
            if customer_id is None:
                return self.db.execute(
                    'INSERT INTO customers(name,phone,note,active) VALUES(?,?,?,?)',
                    (name, phone.strip(), note.strip(), int(active))).lastrowid
            self.customer(customer_id)
            self.db.execute('UPDATE customers SET name=?,phone=?,note=?,active=? WHERE id=?',
                            (name, phone.strip(), note.strip(), int(active), customer_id))
            return customer_id

    def delete_customer(self, customer_id):
        with self.transaction():
            self.customer(customer_id)
            if self.db.execute('SELECT 1 FROM entries WHERE customer_id=?',
                               (customer_id,)).fetchone():
                raise LedgerError('客户已有流水（包括作废流水），只能停用，不能删除。')
            self.db.execute('DELETE FROM customers WHERE id=?', (customer_id,))

    def entries(self, customer_id):
        rows = [dict(row) for row in self.db.execute(
            'SELECT * FROM entries WHERE customer_id=? ORDER BY day,id', (customer_id,))]
        # 按客户录入顺序编号；补录旧日期、筛选和作废均不改变已有编号。
        for number, row in enumerate(sorted(rows, key=lambda item: item['id']), 1):
            row['customer_number'] = number
        return rows

    def balance(self, customer_id):
        return balances(self.entries(customer_id))

    def customers(self):
        result = []
        for row in self.db.execute('SELECT * FROM customers ORDER BY name,id'):
            item = dict(row)
            item.update(self.balance(item['id']))
            result.append(item)
        return result

    def add_entry(self, customer_id, day, kind, amount, note=''):
        validate_date(day)
        if kind not in KINDS:
            raise LedgerError('无效的记账类型。')
        cents = parse_amount(amount)
        with self.transaction():
            return self._insert_entry(customer_id, day, kind, cents, note)

    def _insert_entry(self, customer_id, day, kind, cents, note, prepaid_cents=0):
        if not self.customer(customer_id)['active']:
            raise LedgerError('客户已停用，请先启用后再记账。')
        entry_id = self.db.execute('''INSERT INTO entries
            (customer_id,day,kind,cents,note,created_at,prepaid_cents) VALUES(?,?,?,?,?,?,?)''',
            (customer_id, day, kind, cents, note.strip(),
             datetime.now().isoformat(timespec='seconds'), prepaid_cents)).lastrowid
        self.balance(customer_id)
        return entry_id

    def preview_sale(self, customer_id, day, amount):
        validate_date(day)
        cents = parse_amount(amount)
        self.customer(customer_id)
        available = balances([entry for entry in self.entries(customer_id) if entry['day'] <= day])['advance_received']
        prepaid = min(cents, available)
        return {'prepaid': prepaid, 'remainder': cents - prepaid}

    def record_transaction(self, customer_id, day, action, amount, note='',
                           credit=False, use_prepaid=False, remainder=None):
        """日常录入仅提供收款、充值、还款；旧类型只用于读取历史记录。"""
        if action not in ('sale', 'recharge', 'repayment'):
            raise LedgerError('请选择收款、预付充值或还款。')
        if action != 'sale' and (credit or use_prepaid):
            raise LedgerError('只有收款可以选择赊账或预付金抵扣。')
        if credit and use_prepaid:
            raise LedgerError('赊账与预付金抵扣只能选择一项。')
        if remainder is not None and (action != 'sale' or not use_prepaid):
            raise LedgerError('只有使用预付金抵扣时才需指定差额处理方式。')
        if use_prepaid:
            with self.transaction():
                split = self.preview_sale(customer_id, day, amount)
                if split['remainder'] and remainder not in ('cash', 'credit'):
                    raise LedgerError('请选择差额用现金收款还是记为欠款。')
                kind = ('sale_prepaid' if not split['remainder'] else
                        'sale_split_credit' if remainder == 'credit' else 'sale_split_cash')
                return self._insert_entry(customer_id, day, kind, parse_amount(amount), note,
                    split['prepaid'] if split['remainder'] else 0)
        kind = action
        if action == 'sale':
            kind = 'sale_credit' if credit else 'sale_cash'
        return self.add_entry(customer_id, day, kind, amount, note)

    def void_entry(self, entry_id, reason):
        if not reason.strip():
            raise LedgerError('请填写作废原因。')
        with self.transaction():
            row = self.db.execute('SELECT * FROM entries WHERE id=?', (entry_id,)).fetchone()
            if row is None or row['voided']:
                raise LedgerError('该流水不存在或已经作废。')
            self.db.execute('UPDATE entries SET voided=1,void_reason=?,voided_at=? WHERE id=?',
                            (reason.strip(), datetime.now().isoformat(timespec='seconds'), entry_id))
            # 作废早期应收或预付款可能使后续还款透支，必须整体校验。
            self.balance(row['customer_id'])

    def export_customers(self, target, rows=None):
        self._check_export_target(target)
        with open(str(target), 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['客户编号', '客户名称', '电话', '备注', '状态'] + list(CUSTOMER_LABELS))
            for row in self.customers() if rows is None else rows:
                writer.writerow([row['id'], safe_csv(row['name']), safe_csv(row['phone']),
                                 safe_csv(row['note']), '启用' if row['active'] else '停用'] +
                                [money(row[key]).replace(',', '') for key in CUSTOMER_ACCOUNTS])

    def export_entries(self, customer_id, target, rows=None):
        self._check_export_target(target)
        customer = self.customer(customer_id)
        with open(str(target), 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['流水编号', '客户', '日期', '类型', '金额', '备注', '状态',
                             '作废原因', '录入时间', '作废时间', '预付金抵扣', '现金收款', '新增欠款'])
            for row in self.entries(customer_id) if rows is None else rows:
                prepaid, cash, credit = sale_breakdown(row)
                writer.writerow([row['customer_number'], safe_csv(customer['name']), row['day'],
                                 KINDS[row['kind']][0], money(row['cents']).replace(',', ''),
                                 safe_csv(row['note']), '已作废' if row['voided'] else '有效',
                                 safe_csv(row['void_reason']), row['created_at'], row['voided_at']] +
                                [money(value).replace(',', '') for value in (prepaid, cash, credit)])

    def _check_export_target(self, target):
        if Path(target).suffix.lower() != '.csv':
            raise LedgerError('表格导出文件必须使用 .csv 扩展名。')
        if Path(target).resolve() == self.path.resolve():
            raise LedgerError('导出位置不能是当前账本。')

    def backup(self, target):
        target = Path(target)
        if target.resolve() == self.path.resolve():
            raise LedgerError('备份位置不能是正在使用的账本。')
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='backup-', suffix='.tmp', dir=str(target.parent))
        os.close(fd)
        try:
            copy = sqlite3.connect(name)
            try:
                self.db.backup(copy)
            finally:
                copy.close()
            os.replace(name, str(target))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def automatic_backup(self):
        folder = self.path.parent / 'backups'
        target = folder / ('auto-' + date.today().isoformat() + '.sqlite3')
        if not target.exists():
            self.backup(target)
        for old in sorted(folder.glob('auto-????-??-??.sqlite3'), reverse=True)[30:]:
            old.unlink()

    def restore(self, source):
        source = Path(source)
        if source.resolve() == self.path.resolve():
            raise LedgerError('请选择备份文件，而不是当前账本。')
        # 先复制、校验，禁止直接将未经验证的文件覆盖当前数据库。
        fd, name = tempfile.mkstemp(prefix='restore-', suffix='.sqlite3', dir=str(self.path.parent))
        os.close(fd)
        try:
            if not source.is_file():
                raise LedgerError('备份文件不存在。')
            shutil.copyfile(str(source), name)
            candidate = self._connect(name)
            try:
                self._validate(candidate, allow_legacy=True)
                if candidate.execute('PRAGMA user_version').fetchone()[0] == 1:
                    self._upgrade_schema(candidate)
                    self._validate(candidate)
            finally:
                candidate.close()
            safety = self.path.parent / 'backups' / (
                'before-restore-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
            self.backup(safety)
            self.db.close()
            try:
                os.replace(name, str(self.path))
            finally:
                self.db = self._connect(self.path)
            return safety
        finally:
            if os.path.exists(name):
                os.unlink(name)


def safe_csv(value):
    # 避免客户名称、电话和备注在 Excel 中被解释为公式。
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def sale_breakdown(entry):
    prepaid = prepaid_deduction(entry)
    kind, cents = entry['kind'], entry['cents']
    cash = cents - prepaid if kind in ('sale_cash', 'sale_split_cash', 'recharge',
                                       'repayment', 'receive', 'advance_received') else 0
    credit = cents - prepaid if kind in ('sale_credit', 'sale_split_credit', 'receivable') else 0
    return prepaid, cash, credit
