import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import date, timedelta
from pathlib import Path

from ledger.domain import LedgerError, MAX_CENTS, money, parse_amount
from ledger.storage import InstanceLock, Store
from ledger.runtime import check_tk_runtime


class AmountTests(unittest.TestCase):
    def test_failed_connection_setup_closes_database(self):
        connection = Mock()
        connection.execute.side_effect = [None, sqlite3.DatabaseError('file is not a database')]
        with patch('ledger.storage.sqlite3.connect', return_value=connection):
            with self.assertRaises(sqlite3.DatabaseError):
                Store._connect('broken.sqlite3')
        connection.close.assert_called_once()

    def test_reject_legacy_macos_tk_before_window_creation(self):
        with self.assertRaises(LedgerError):
            check_tk_runtime('darwin', 8.5)
        for platform, version in [('darwin', 8.6), ('darwin', 9.0), ('win32', 8.6)]:
            check_tk_runtime(platform, version)

    def test_exact_cents(self):
        self.assertEqual(parse_amount('0.29'), 29)
        self.assertEqual(parse_amount(' 100.50 '), 10050)
        self.assertEqual(money(123456), '1,234.56')

    def test_invalid_amounts(self):
        for value in ('', 'abc', '0', '-1', '0.001', 'NaN', 'Infinity',
                      '1e999999', str(MAX_CENTS)):
            with self.subTest(value=value), self.assertRaises(LedgerError):
                parse_amount(value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'ledger.sqlite3'
        self.store = Store(self.path)
        self.customer = self.store.save_customer('张先生', '13800138000', '老客户')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, kind, amount, day='2026-10-27'):
        return self.store.add_entry(self.customer, day, kind, amount, '测试备注')

    def test_independent_debts_and_partial_repayment(self):
        self.add('receivable', '1000')
        self.add('receive', '400')
        self.add('payable', '300')
        result = self.store.balance(self.customer)
        self.assertEqual(result['receivable'], 60000)
        self.assertEqual(result['payable'], 30000)
        self.add('pay', '100')
        self.assertEqual(self.store.balance(self.customer)['payable'], 20000)

    def record(self, action, amount, **options):
        return self.store.record_transaction(self.customer, '2026-10-27', action, amount, **options)

    def test_cash_sale_is_a_record_without_debt_or_prepaid_change(self):
        self.record('sale', '100')
        self.assertEqual(self.store.balance(self.customer), dict(
            receivable=0, payable=0, advance_received=0, advance_paid=0))
        self.assertEqual(self.store.entries(self.customer)[0]['kind'], 'sale_cash')

    def test_credit_sale_then_repayment(self):
        self.record('sale', '100', credit=True)
        self.record('repayment', '40')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 6000)
        with self.assertRaises(LedgerError):
            self.record('repayment', '61')
        self.assertEqual(len(self.store.entries(self.customer)), 2)

    def test_prepaid_sale_only_reduces_prepaid_not_existing_debt(self):
        self.record('sale', '80', credit=True)
        self.record('recharge', '100')
        self.record('sale', '60', use_prepaid=True)
        result = self.store.balance(self.customer)
        self.assertEqual(result['receivable'], 8000)
        self.assertEqual(result['advance_received'], 4000)

    def test_insufficient_prepaid_never_creates_partial_record(self):
        self.record('recharge', '50')
        with self.assertRaises(LedgerError):
            self.record('sale', '51', use_prepaid=True)
        self.assertEqual(len(self.store.entries(self.customer)), 1)
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 5000)

    def test_prepaid_shortfall_can_be_cash(self):
        self.record('recharge', '50')
        sale = self.record('sale', '80', use_prepaid=True, remainder='cash')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 0)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 0)
        row = self.store.entries(self.customer)[1]
        self.assertEqual(row['id'], sale)
        self.assertEqual(row['prepaid_cents'], 5000)
        self.assertEqual(row['cents'], 8000)
        self.assertEqual(row['kind'], 'sale_split_cash')
        self.store.void_entry(sale, '整笔作废')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 5000)

    def test_prepaid_shortfall_can_be_debt_and_repaid(self):
        self.record('recharge', '50')
        self.record('sale', '80', use_prepaid=True, remainder='credit')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 0)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 3000)
        self.record('repayment', '30')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 0)

    def test_preview_uses_balance_at_business_date(self):
        self.record('recharge', '100')
        split = self.store.preview_sale(self.customer, '2026-10-01', '80')
        self.assertEqual(split, {'prepaid': 0, 'remainder': 8000})
        self.store.record_transaction(self.customer, '2026-10-01', 'sale', '80',
                                      use_prepaid=True, remainder='credit')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 10000)

    def test_void_split_credit_reverses_both_balances(self):
        self.record('recharge', '50')
        sale = self.record('sale', '80', use_prepaid=True, remainder='credit')
        self.store.void_entry(sale, '错误收款')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 0)
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 5000)

    def test_split_backup_and_csv_breakdown(self):
        self.record('recharge', '50')
        self.record('sale', '80', use_prepaid=True, remainder='credit')
        backup = self.folder / 'split.sqlite3'
        self.store.backup(backup)
        self.store.restore(backup)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 3000)
        target = self.folder / 'split.csv'
        self.store.export_entries(self.customer, target)
        with target.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[-1][-3:], ['50.00', '0.00', '30.00'])

    def test_upgrade_old_book_and_restore_old_backup(self):
        legacy = self.folder / 'legacy.sqlite3'
        db = sqlite3.connect(str(legacy))
        db.executescript('''
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, phone TEXT,
                                   note TEXT, active INTEGER);
            CREATE TABLE entries(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                day TEXT, kind TEXT, cents INTEGER, note TEXT, created_at TEXT,
                voided INTEGER, void_reason TEXT, voided_at TEXT);
            INSERT INTO customers VALUES(1,'旧客户','','',1);
            INSERT INTO entries VALUES(1,1,'2026-10-01','receivable',10000,'原始备注','',0,'','');
            PRAGMA user_version=1;
        ''')
        db.close()
        untouched = self.folder / 'old-backup.sqlite3'
        untouched.write_bytes(legacy.read_bytes())
        upgraded = Store(legacy)
        try:
            self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(upgraded.balance(1)['receivable'], 10000)
            self.assertEqual(upgraded.entries(1)[0]['note'], '原始备注')
            upgraded.record_transaction(1, '2026-10-27', 'repayment', '40')
        finally:
            upgraded.close()
        self.assertEqual(len(list((self.folder / 'backups').glob('before-upgrade-*.sqlite3'))), 1)
        self.store.restore(untouched)
        self.assertEqual(self.store.balance(1)['receivable'], 10000)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_recharge_does_not_repay_existing_debt(self):
        self.record('sale', '100', credit=True)
        self.record('recharge', '50')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 10000)
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 5000)

    def test_invalid_payment_options_are_rejected(self):
        for action, options in [('sale', dict(credit=True, use_prepaid=True)),
                                ('recharge', dict(credit=True)), ('repayment', dict(use_prepaid=True)),
                                ('pay', {})]:
            with self.subTest(action=action), self.assertRaises(LedgerError):
                self.record(action, '10', **options)
        self.assertEqual(self.store.entries(self.customer), [])

    def test_void_prepaid_sale_restores_prepaid_balance(self):
        recharge = self.record('recharge', '100')
        sale = self.record('sale', '40', use_prepaid=True)
        with self.assertRaises(LedgerError):
            self.store.void_entry(recharge, '充值错误')
        self.store.void_entry(sale, '收款错误')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 10000)

    def test_backup_restore_preserves_new_payment_types(self):
        self.record('sale', '80', credit=True)
        self.record('recharge', '100')
        self.record('sale', '60', use_prepaid=True)
        self.record('repayment', '20')
        self.record('sale', '10')
        target = self.folder / 'simple.sqlite3'
        self.store.backup(target)
        self.store.restore(target)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 6000)
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 4000)

    def test_customer_export_only_contains_customer_debt_and_prepaid(self):
        target = self.folder / 'summary.csv'
        self.store.export_customers(target)
        with target.open(encoding='utf-8-sig', newline='') as f:
            header = next(csv.reader(f))
        self.assertEqual(header[-2:], ['客户欠款', '预付金余额'])
        self.assertEqual(len(header), 7)

    def test_opening_balances_and_both_advances(self):
        self.add('opening_receivable', '1000')
        self.add('opening_payable', '300')
        self.add('advance_received', '200')
        self.add('advance_paid', '50')
        self.assertEqual(self.store.balance(self.customer), dict(
            receivable=100000, payable=30000, advance_received=20000, advance_paid=5000))
        self.add('apply_received', '100')
        self.add('apply_paid', '50')
        self.assertEqual(self.store.balance(self.customer), dict(
            receivable=90000, payable=25000, advance_received=10000, advance_paid=0))

    def test_overpayment_rolls_back(self):
        self.add('receivable', '10')
        with self.assertRaises(LedgerError):
            self.add('receive', '10.01')
        self.assertEqual(len(self.store.entries(self.customer)), 1)
        self.add('receive', '10')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 0)

    def test_apply_needs_debt_and_advance(self):
        self.add('receivable', '100')
        with self.assertRaises(LedgerError):
            self.add('apply_received', '1')
        self.add('advance_received', '200')
        with self.assertRaises(LedgerError):
            self.add('apply_received', '101')
        self.add('apply_received', '100')
        self.assertEqual(self.store.balance(self.customer)['advance_received'], 10000)

    def test_pay_and_apply_paid_limits(self):
        self.add('payable', '100')
        self.add('advance_paid', '50')
        for kind, amount in [('pay', '101'), ('apply_paid', '51')]:
            with self.assertRaises(LedgerError):
                self.add(kind, amount)
        self.add('apply_paid', '50')
        self.add('pay', '50')
        self.assertEqual(self.store.balance(self.customer)['payable'], 0)

    def test_void_retains_audit_and_restores_balance(self):
        self.add('receivable', '10')
        repayment = self.add('receive', '5')
        self.store.void_entry(repayment, '误录金额')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 1000)
        row = self.store.entries(self.customer)[1]
        self.assertEqual(row['void_reason'], '误录金额')
        self.assertTrue(row['voided_at'])
        with self.assertRaises(LedgerError):
            self.store.void_entry(repayment, '再次作废')

    def test_void_cannot_invalidate_later_repayment(self):
        debt = self.add('receivable', '100', '2026-10-01')
        self.add('receive', '100', '2026-10-02')
        # 后来的应收不能填补历史透支。
        self.add('receivable', '100', '2026-10-03')
        with self.assertRaises(LedgerError):
            self.store.void_entry(debt, '作废')
        self.assertEqual(self.store.entries(self.customer)[0]['voided'], 0)

    def test_void_advance_requires_voiding_application_first(self):
        self.add('receivable', '100')
        advance = self.add('advance_received', '100')
        applied = self.add('apply_received', '100')
        with self.assertRaises(LedgerError):
            self.store.void_entry(advance, '错误')
        self.store.void_entry(applied, '先撤销抵用')
        self.store.void_entry(advance, '再撤销预收')
        self.assertEqual(self.store.balance(self.customer)['receivable'], 10000)

    def test_void_reason_required(self):
        entry = self.add('receivable', '10')
        with self.assertRaises(LedgerError):
            self.store.void_entry(entry, '  ')

    def test_backdated_repayment_is_rejected(self):
        self.add('receivable', '100', '2026-10-02')
        with self.assertRaises(LedgerError):
            self.add('receive', '50', '2026-10-01')

    def test_invalid_dates_and_kind(self):
        for day in ('2026-02-30', '2026-1-01', '27/10/2026'):
            with self.assertRaises(LedgerError):
                self.add('receivable', '1', day)
        with self.assertRaises(LedgerError):
            self.add('unknown', '1')

    def test_customer_lifecycle(self):
        with self.assertRaises(LedgerError):
            self.store.save_customer(' ')
        self.store.save_customer('张女士', '123', '已更名', False, self.customer)
        with self.assertRaises(LedgerError):
            self.add('receivable', '1')
        self.store.save_customer('张女士', active=True, customer_id=self.customer)
        entry = self.add('receivable', '1')
        self.store.void_entry(entry, '错了')
        with self.assertRaises(LedgerError):
            self.store.delete_customer(self.customer)
        empty = self.store.save_customer('空客户')
        self.store.delete_customer(empty)
        with self.assertRaises(LedgerError):
            self.store.customer(empty)

    def test_balances_isolated_by_customer(self):
        other = self.store.save_customer('李先生')
        self.add('receivable', '100')
        with self.assertRaises(LedgerError):
            self.store.add_entry(other, '2026-10-27', 'receive', '1')

    def test_customer_numbers_are_independent_and_stable(self):
        other = self.store.save_customer('其他客户')
        first = self.record('sale', '10')
        self.store.record_transaction(other, '2026-10-27', 'sale', '20')
        second = self.record('sale', '30')
        self.store.record_transaction(other, '2026-10-27', 'sale', '40')
        backdated = self.store.record_transaction(self.customer, '2026-10-01', 'sale', '50')
        self.store.void_entry(second, '误录')
        rows = self.store.entries(self.customer)
        self.assertEqual({row['id']: row['customer_number'] for row in rows},
                         {first: 1, second: 2, backdated: 3})
        self.assertEqual([row['customer_number'] for row in self.store.entries(other)], [1, 2])
        target = self.folder / 'numbers.csv'
        self.store.export_entries(self.customer, target, [rows[0]])
        with target.open(encoding='utf-8-sig', newline='') as f:
            exported = list(csv.reader(f))
        self.assertEqual(exported[1][0], '3')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual({row['id']: row['customer_number'] for row in self.store.entries(self.customer)},
                         {first: 1, second: 2, backdated: 3})

    def test_aggregate_overflow_is_rejected(self):
        self.add('receivable', str(MAX_CENTS // 100))
        with self.assertRaises(LedgerError):
            self.add('receivable', '1')

    def test_reopen_retains_data(self):
        self.add('receivable', '0.29')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 29)

    def test_backup_restore_and_safety_backup(self):
        self.add('receivable', '100')
        backup = self.folder / 'export.sqlite3'
        self.store.backup(backup)
        self.add('receive', '50')
        safety = self.store.restore(backup)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 10000)
        other = Store(safety)
        try:
            self.assertEqual(other.balance(self.customer)['receivable'], 5000)
        finally:
            other.close()

    def test_invalid_restore_preserves_current_data(self):
        self.add('receivable', '100')
        bad = self.folder / 'bad.sqlite3'
        for content in (b'not a database', b''):
            bad.write_bytes(content)
            with self.assertRaises((LedgerError, sqlite3.Error)):
                self.store.restore(bad)
            self.assertEqual(self.store.balance(self.customer)['receivable'], 10000)
        with self.assertRaises(LedgerError):
            self.store.restore(self.folder / 'missing.sqlite3')
        with self.assertRaises(LedgerError):
            self.store.restore(self.path)

    def test_logically_invalid_restore_is_rejected(self):
        target = self.folder / 'bad.sqlite3'
        self.store.backup(target)
        db = sqlite3.connect(str(target))
        db.execute("INSERT INTO entries(customer_id,day,kind,cents,created_at) VALUES(?, '2026-10-27', 'receive', 100, '')", (self.customer,))
        db.commit()
        db.close()
        with self.assertRaises(LedgerError):
            self.store.restore(target)

    def test_self_backup_is_rejected(self):
        with self.assertRaises(LedgerError):
            self.store.backup(self.path)

    def test_export_cannot_overwrite_ledger_or_backup(self):
        self.add('receivable', '100')
        for target in (self.path, self.folder / 'backup.sqlite3'):
            with self.assertRaises(LedgerError):
                self.store.export_customers(target)
            with self.assertRaises(LedgerError):
                self.store.export_entries(self.customer, target)
        self.assertEqual(self.store.balance(self.customer)['receivable'], 10000)

    def test_incomplete_schema_restore_is_rejected(self):
        target = self.folder / 'incomplete.sqlite3'
        db = sqlite3.connect(str(target))
        db.executescript('CREATE TABLE customers(id INTEGER, name TEXT, phone TEXT, note TEXT, active INTEGER); '
                         'CREATE TABLE entries(id INTEGER); PRAGMA user_version=1;')
        db.close()
        with self.assertRaises(LedgerError):
            self.store.restore(target)

    def test_daily_backup_retention(self):
        self.store.automatic_backup()
        target = self.folder / 'backups' / ('auto-' + date.today().isoformat() + '.sqlite3')
        before = target.stat().st_mtime_ns
        self.add('receivable', '100')
        self.store.automatic_backup()
        self.assertEqual(target.stat().st_mtime_ns, before)
        for offset in range(1, 35):
            old = self.folder / 'backups' / ('auto-' + (date.today() - timedelta(days=offset)).isoformat() + '.sqlite3')
            self.store.backup(old)
        self.store.automatic_backup()
        self.assertEqual(len(list(target.parent.glob('auto-*.sqlite3'))), 30)

    def test_csv_chinese_and_audit(self):
        entry = self.add('receivable', '10.29')
        self.store.void_entry(entry, '误记，已核实')
        target = self.folder / '客户.csv'
        self.store.export_customers(target)
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))
        with target.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][1], '张先生')
        self.store.export_entries(self.customer, target)
        with target.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][4], '10.29')
        self.assertEqual(rows[1][6:8], ['已作废', '误记，已核实'])

    def test_csv_formula_safety_and_filtered_rows(self):
        self.store.save_customer('=1+1', '+123', '@name', customer_id=self.customer)
        target = self.folder / 'export.csv'
        self.store.export_customers(target)
        with target.open(encoding='utf-8-sig', newline='') as f:
            row = list(csv.reader(f))[1]
        self.assertEqual(row[1:4], ["'=1+1", "'+123", "'@name"])
        self.store.export_customers(target, [])
        self.assertEqual(len(target.read_text(encoding='utf-8-sig').splitlines()), 1)


class LockTests(unittest.TestCase):
    def test_second_process_blocked_and_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.lock'
            lock = InstanceLock(path)
            code = ('from ledger.storage import InstanceLock; import sys; '
                    'lock=InstanceLock(sys.argv[1]); lock.close()')
            first = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
            self.assertNotEqual(first.returncode, 0)
            lock.close()
            second = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)


if __name__ == '__main__':
    unittest.main()
