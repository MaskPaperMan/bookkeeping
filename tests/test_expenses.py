import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ledger.domain import LedgerError
from ledger.expenses import DEFAULT_CATEGORIES
from ledger.storage import Store, SCHEMA_VERSION


class ExpenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'ledger.sqlite3'
        self.store = Store(self.path)
        self.category = self.store.expense_categories()[0]['id']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, amount='10.29', day='2026-10-27', payee='张供应商', note='进货支出', category=None):
        return self.store.add_expense(day, self.category if category is None else category, amount, payee, note)

    def test_defaults_and_independent_expense_numbers(self):
        self.assertEqual([row['name'] for row in self.store.expense_categories()], list(DEFAULT_CATEGORIES))
        customer = self.store.save_customer('收入客户')
        self.store.record_transaction(customer, '2026-10-27', 'sale', '100', credit=True)
        first = self.add()
        self.store.record_transaction(customer, '2026-10-27', 'recharge', '80')
        second = self.add('20')
        self.assertEqual((first, second), (1, 2))
        balance = self.store.balance(customer)
        self.assertEqual(balance['receivable'], 10000)
        self.assertEqual(balance['advance_received'], 8000)
        self.assertEqual(len(self.store.entries(customer)), 2)

    def test_add_and_reopen_preserves_amount_and_fields(self):
        self.add('0.29', payee='  供货商甲  ', note='  明细  ')
        self.store.close()
        self.store = Store(self.path)
        row = self.store.expenses()[0]
        self.assertEqual(row['cents'], 29)
        self.assertEqual(row['payee'], '供货商甲')
        self.assertEqual(row['note'], '明细')
        self.assertEqual(row['category'], '进货')

    def test_invalid_inputs_do_not_create_expenses(self):
        for value in ('0', '-1', '0.001', 'NaN', ''):
            with self.assertRaises(LedgerError):
                self.add(value)
        with self.assertRaises(LedgerError):
            self.add(day='2026-02-30')
        with self.assertRaises(LedgerError):
            self.add(category=999)
        self.assertEqual(self.store.expenses(), [])

    def test_category_add_remove_and_reactivate_keeps_history(self):
        category = self.store.add_expense_category('  办公用品  ')
        self.add(category=category)
        self.store.remove_expense_category(category)
        self.assertNotIn(category, [r['id'] for r in self.store.expense_categories()])
        self.assertEqual(self.store.expenses()[0]['category'], '办公用品')
        with self.assertRaises(LedgerError):
            self.add(category=category)
        restored = self.store.add_expense_category('办公用品')
        self.assertEqual(restored, category)
        self.add(category=restored)
        self.assertEqual(len(self.store.expenses(category_id=category)), 2)

    def test_unused_categories_can_be_deleted_and_do_not_reappear(self):
        for row in self.store.expense_categories():
            self.store.remove_expense_category(row['id'])
        self.assertEqual(self.store.expense_categories(), [])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.expense_categories(), [])
        self.store.add_expense_category('自定义')
        self.assertEqual(len(self.store.expense_categories()), 1)

    def test_category_validation(self):
        for name in (' ', '全部类别', '进货'):
            with self.assertRaises(LedgerError):
                self.store.add_expense_category(name)
        with self.assertRaises(LedgerError):
            self.store.remove_expense_category(999)

    def test_filters_are_inclusive_and_search_literal(self):
        other = self.store.add_expense_category('办公')
        self.add('10', '2026-10-01', payee='王先生', category=other)
        self.add('20', '2026-10-31', payee='王先生', category=other)
        self.add('30', '2026-11-01', payee='王先生', category=other)
        self.add('40', '2026-10-15', payee='李先生')
        self.add('50', '2026-10-15', payee='ABC%_')
        rows = self.store.expenses('2026-10-01', '2026-10-31', other, '王')
        self.assertEqual([row['cents'] for row in rows], [2000, 1000])
        self.assertEqual(len(self.store.expenses(query='%_')), 1)
        self.assertEqual(len(self.store.expenses(query='abc')), 1)
        for start, end in [('bad', ''), ('2026-10-31', '2026-10-01')]:
            with self.assertRaises(LedgerError):
                self.store.expenses(start, end)

    def test_void_and_monthly_filtered_category_totals(self):
        self.add('10', '2026-09-30')
        self.add('20', '2026-10-01')
        second = self.store.expense_categories()[1]['id']
        self.add('30', '2026-10-31', category=second)
        voided = self.add('40', '2026-10-15')
        self.add('50', '2026-11-01')
        self.store.void_expense(voided, '重复录入')
        rows = self.store.expenses(category_id=second)
        summary = self.store.expense_summary(rows, today=date(2026, 10, 27))
        self.assertEqual(summary, dict(month=5000, filtered=3000, count=1, categories={'运费': 3000}))
        row = next(row for row in self.store.expenses() if row['id'] == voided)
        self.assertEqual(row['void_reason'], '重复录入')
        self.assertTrue(row['voided_at'])
        with self.assertRaises(LedgerError):
            self.store.void_expense(voided, '再次作废')
        valid = self.add()
        with self.assertRaises(LedgerError):
            self.store.void_expense(valid, ' ')

    def test_payee_history_is_distinct_recent_and_excludes_voided(self):
        self.add(payee='甲')
        self.add(payee='乙')
        self.add(payee='甲')
        self.add(payee='')
        voided = self.add(payee='丙')
        self.store.void_expense(voided, '错录')
        self.assertEqual(self.store.expense_payees(), ['甲', '乙'])

    def test_csv_includes_void_audit_and_escapes_text_formulas(self):
        category = self.store.add_expense_category('=类别')
        expense = self.add(category=category, payee='+张先生', note='@备注')
        self.store.void_expense(expense, '误记')
        target = self.folder / '支出.csv'
        self.store.export_expenses(target)
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))
        with target.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][2:8], ["'=类别", '10.29', "'+张先生", "'@备注", '已作废', '误记'])
        self.store.export_expenses(target, [])
        self.assertEqual(len(target.read_text(encoding='utf-8-sig').splitlines()), 1)
        with self.assertRaises(LedgerError):
            self.store.export_expenses(self.path)

    def test_backup_restore_includes_categories_and_expenses(self):
        category = self.store.add_expense_category('自定义类别')
        self.add('10', category=category)
        target = self.folder / 'backup.sqlite3'
        self.store.backup(target)
        self.add('20')
        self.store.remove_expense_category(category)
        self.store.restore(target)
        self.assertEqual(len(self.store.expenses()), 1)
        self.assertIn(category, [row['id'] for row in self.store.expense_categories()])

    def test_income_only_v2_upgrade_and_restore(self):
        customer = self.store.save_customer('老客户')
        self.store.record_transaction(customer, '2026-10-27', 'sale', '100', credit=True)
        self.store.close()
        db = sqlite3.connect(str(self.path))
        db.executescript('DROP TABLE expenses; DROP TABLE expense_categories; PRAGMA user_version=2;')
        db.close()
        old = self.folder / 'v2.sqlite3'
        old.write_bytes(self.path.read_bytes())
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(self.store.balance(customer)['receivable'], 10000)
        self.assertEqual(self.store.expenses(), [])
        self.assertEqual(len(self.store.expense_categories()), 6)
        self.assertEqual(len(list((self.folder / 'backups').glob('before-upgrade-*.sqlite3'))), 1)
        self.add()
        self.store.restore(old)
        self.assertEqual(self.store.expenses(), [])
        self.assertEqual(self.store.balance(customer)['receivable'], 10000)

    def test_invalid_expense_backup_does_not_replace_current_book(self):
        self.add()
        target = self.folder / 'bad.sqlite3'
        self.store.backup(target)
        db = sqlite3.connect(str(target))
        db.execute('UPDATE expenses SET category_id=999')
        db.commit()
        db.close()
        with self.assertRaises(LedgerError):
            self.store.restore(target)
        self.assertEqual(len(self.store.expenses()), 1)
