from datetime import date
from decimal import Decimal, InvalidOperation


class LedgerError(ValueError):
    pass


ACCOUNTS = ('receivable', 'payable', 'advance_received', 'advance_paid')
ACCOUNT_LABELS = ('客户欠款', '历史应付余额', '预付金余额', '历史预付款余额')
# 旧版四项余额保留用于历史账本校验，新界面只展示客户欠款和预付金。
CUSTOMER_ACCOUNTS = ('receivable', 'advance_received')
CUSTOMER_LABELS = ('客户欠款', '预付金余额')
# 正数增加余额，负数减少余额；每种余额独立核算。
KINDS = {
    'opening_receivable': ('期初应收', (1, 0, 0, 0)),
    'opening_payable': ('期初应付', (0, 1, 0, 0)),
    'receivable': ('新增应收', (1, 0, 0, 0)),
    'payable': ('新增应付', (0, 1, 0, 0)),
    'receive': ('收到客户还款', (-1, 0, 0, 0)),
    'pay': ('偿还客户欠款', (0, -1, 0, 0)),
    'advance_received': ('收到客户预付款', (0, 0, 1, 0)),
    'advance_paid': ('预付客户款', (0, 0, 0, 1)),
    'apply_received': ('抵用客户预付款', (-1, 0, -1, 0)),
    'apply_paid': ('抵用我的预付款', (0, -1, 0, -1)),
    'sale_cash': ('收款（现收）', (0, 0, 0, 0)),
    'sale_credit': ('收款（赊账）', (1, 0, 0, 0)),
    'sale_prepaid': ('收款（预付金抵扣）', (0, 0, -1, 0)),
    'recharge': ('预付充值', (0, 0, 1, 0)),
    'repayment': ('还款', (-1, 0, 0, 0)),
    'sale_split_cash': ('收款（预付抵扣＋现金）', (0, 0, 0, 0)),
    'sale_split_credit': ('收款（预付抵扣＋赊账）', (0, 0, 0, 0)),
}
MAX_CENTS = 99999999999999


def entry_category(kind):
    if kind in ('sale_cash', 'sale_credit', 'sale_prepaid', 'sale_split_cash',
                'sale_split_credit', 'receivable'):
        return '收款'
    if kind in ('recharge', 'advance_received'):
        return '预付充值'
    if kind in ('repayment', 'receive'):
        return '还款'
    return '历史记录'


def prepaid_deduction(entry):
    if entry['kind'] in ('sale_prepaid', 'apply_received'):
        return entry['cents']
    if entry['kind'] in ('sale_split_cash', 'sale_split_credit'):
        return entry['prepaid_cents']
    return 0


def parse_amount(value):
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or amount <= 0:
            raise LedgerError('金额必须大于零。')
        if amount > Decimal(MAX_CENTS) / 100:
            raise LedgerError('金额过大。')
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise LedgerError('金额最多保留两位小数。')
        return int(cents)
    except (InvalidOperation, TypeError):
        raise LedgerError('请输入有效金额，例如 100.50。')


def validate_date(value):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise LedgerError('日期格式应为 YYYY-MM-DD。')
    return value


def money(cents):
    return '{:,}.{:02d}'.format(cents // 100, cents % 100)


def balances(entries):
    """按业务日期、录入顺序重算，同时拒绝任何时点的透支。"""
    result = dict.fromkeys(ACCOUNTS, 0)
    for entry in sorted(entries, key=lambda e: (e['day'], e['id'])):
        if entry['voided']:
            continue
        effects = [factor * entry['cents'] for factor in KINDS[entry['kind']][1]]
        if entry['kind'] in ('sale_split_cash', 'sale_split_credit'):
            prepaid = entry['prepaid_cents']
            if type(prepaid) is not int or not 0 <= prepaid < entry['cents']:
                raise LedgerError('预付抵扣金额无效。')
            effects = [entry['cents'] - prepaid if entry['kind'] == 'sale_split_credit' else 0,
                       0, -prepaid, 0]
        for account, effect in zip(ACCOUNTS, effects):
            result[account] += effect
            if result[account] < 0:
                label = ACCOUNT_LABELS[ACCOUNTS.index(account)]
                raise LedgerError('{} 的{}余额不足；请检查日期、还款和抵用记录。'
                                  .format(entry['day'], label))
            if result[account] > MAX_CENTS:
                raise LedgerError('累计余额超出可记录范围。')
    return result
