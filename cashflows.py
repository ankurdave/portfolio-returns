"""Extract cashflows from transactions.

"""
import datetime
import logging
import re
import dataclasses
import collections

from dateutil.relativedelta import relativedelta
from decimal import Decimal
from typing import List, Optional, Set

import beancount
import beancount.core
import beancount.core.realization

from beancount.core.data import Account, Amount, Currency, Transaction
from beancount.core.prices import PriceMap

@dataclasses.dataclass
class Cashflow:
    date: datetime.date
    amount: Decimal
    kind: str
    inflow_accounts: Set[Account] = dataclasses.field(default_factory=set)
    outflow_accounts: Set[Account] = dataclasses.field(default_factory=set)
    entry: Optional[Transaction] = None

def get_asset_account(account: Account, asset_account_map: dict[re.Pattern, str]):
    for pattern, replacement in asset_account_map.items():
        asset_account, num_matches = pattern.subn(replacement, account)
        if num_matches > 0:
            return asset_account
    return None

def get_number(amount: Amount, currency: Currency):
    if amount.currency != currency:
        raise AssertionError(
            f'Could not convert posting {converted} from ' +
            f'{entry.date} on line {posting.meta["lineno"]} to {currency}.')
    return amount.number

def get_market_values_by_asset_account(
        entries: List[Transaction], asset_account_map: dict[str, str],
        date: Optional[datetime.date], price_map: PriceMap,
        currency: Currency) -> dict[Account, Decimal]:
    """Return the market value of each asset account as of the beginning of 'date'."""
    if date is None:
        realized = beancount.core.realization.realize(entries)
    else:
        realized = beancount.core.realization.realize(
            [e for e in entries if e.date < date])
    market_value_by_asset_account = collections.defaultdict(lambda: Decimal(0))
    for real_account in beancount.core.realization.iter_children(realized):
        if not beancount.core.account_types.is_account_type(
                beancount.core.account_types.DEFAULT_ACCOUNT_TYPES.assets,
                real_account.account):
            continue
        balance_asset_account = get_asset_account(real_account.account, asset_account_map)
        if balance_asset_account is None: continue
        inventory = beancount.core.realization.compute_balance(real_account)
        market_value_inventory = inventory.reduce(
            beancount.core.convert.convert_position, currency, price_map)
        if market_value_inventory.is_empty(): continue
        market_value = get_number(market_value_inventory.get_only_position().units, currency)
        market_value_by_asset_account[balance_asset_account] += market_value
    return market_value_by_asset_account

def get_cashflows_by_asset_account(
        entries: List[Transaction], asset_account_map: dict[str, str],
        start_date_inclusive: Optional[datetime.date], end_date_inclusive: Optional[datetime.date],
        currency: Currency) -> dict[Account, List[Cashflow]]:
    """For each asset account, extract a series of cashflows affecting that account.

    A cashflow to/from an asset account is represented by any transaction involving (1) an account
    that maps to that asset account (using 'asset_account_map' as the mapping) and (2) an account
    that maps to some other asset account. Positive cashflows indicate inflows, and negative
    cashflows indicate outflows.

    'asset_account_map' is a dict of regular expression patterns that must match at the beginning of
    an account name, and the corresponding replacements. It should map each account to its
    corresponding asset account. For example, it might map Income:Brokerage:Dividends:BND to
    Assets:Brokerage:BND.

    For each asset account, return a list of cashflows that occurred between 'start_date_inclusive'
    and 'end_date_inclusive'. If the asset account had a balance at the beginning of
    'start_date_inclusive', the first cashflow will represent the market value of that balance as an
    inflow. If it had a balance at the end of 'end_date_inclusive', the last cashflow will represent
    the market value of that balance as an outflow. The cashflows will be denominated in units of
    'currency'.

    """
    asset_account_map = dict([(re.compile(pattern), replacement)
                             for pattern, replacement in asset_account_map.items()])
    price_map = beancount.core.prices.build_price_map(entries)
    only_txns = list(beancount.core.data.filter_txns(entries))

    cashflows_by_asset_account: dict(str, List[Cashflow]) = collections.defaultdict(lambda: [])

    # Extract cashflows from transactions.
    for entry in only_txns:
        if start_date_inclusive is not None and not start_date_inclusive <= entry.date: continue
        if end_date_inclusive is not None and not entry.date <= end_date_inclusive: continue

        pending_cashflow_by_asset_account = collections.defaultdict(
            lambda: Cashflow(date=entry.date, amount=Decimal(0), kind='txn', entry=entry))

        for posting in entry.postings:
            value = get_number(beancount.core.convert.convert_amount(
                beancount.core.convert.get_weight(posting), currency, price_map, entry.date), currency)
            asset_account = get_asset_account(posting.account, asset_account_map)
            if asset_account is None: continue
            for a in beancount.core.account.parents(asset_account):
                pending_cashflow_by_asset_account[a].amount += value

        for asset_account, cashflow in pending_cashflow_by_asset_account.items():
            if cashflow.amount.quantize(Decimal('.01')) != 0:
                for posting in entry.postings:
                    posting_asset_account = get_asset_account(posting.account, asset_account_map)
                    if (posting_asset_account is None or
                        asset_account not in beancount.core.account.parents(posting_asset_account)):
                        # This posting does not belong to 'asset_account', meaning it contributes to
                        # an inflow or outflow. Record the account for ease of debugging.
                        if beancount.core.convert.get_weight(posting).number > 0:
                            cashflow.outflow_accounts.add(posting.account)
                        else:
                            cashflow.inflow_accounts.add(posting.account)
                cashflows_by_asset_account[asset_account].append(cashflow)

    # For each account, insert an initial inflow representing its market value at the beginning of
    # 'start_date_inclusive'.
    if start_date_inclusive is not None:
        market_value_by_asset_account = get_market_values_by_asset_account(
            entries, asset_account_map, start_date_inclusive, price_map, currency)
        for asset_account, market_value in market_value_by_asset_account.items():
            cashflow = Cashflow(date=start_date_inclusive, amount=market_value,
                                kind='starting balance')
            cashflows_by_asset_account[asset_account].insert(0, cashflow)

    # For each account, insert a final outflow representing its market value at the end of
    # 'end_date_inclusive'.
    market_value_by_asset_account = get_market_values_by_asset_account(
        entries, asset_account_map,
        end_date_inclusive + relativedelta(days=1) if end_date_inclusive is not None else None,
        price_map, currency)
    for asset_account, market_value in market_value_by_asset_account.items():
        cashflow = Cashflow(date=end_date_inclusive, amount=-market_value, kind='ending balance')
        cashflows_by_asset_account[asset_account].append(cashflow)

    # Convert the 'defaultdict' to a 'dict' to avoid causing unexpected behavior for the caller.
    return dict(cashflows_by_asset_account)

def get_cashflows(entries: List[Transaction], interesting_accounts: List[str], internal_accounts:
                  List[str], date_from: Optional[datetime.date], date_to: datetime.date,
                  currency: Currency) -> List[Cashflow]:
    """Extract a series of cashflows affecting 'interesting_accounts'.

    A cashflow is represented by any transaction involving (1) an account in 'interesting_accounts'
    and (2) an account not in 'interesting_accounts' or 'internal_accounts'. Positive cashflows
    indicate inflows, and negative cashflows indicate outflows.

    'interesting_accounts' and 'internal_accounts' are regular expressions that must match at the
    beginning of account names.

    Return a list of cashflows that occurred between 'date_from' and 'date_to', inclusive. If
    'interesting_accounts' had a balance at the beginning of 'date_from', the first cashflow will
    represent the market value of that balance as an inflow. The cashflows will be denominated in
    units of 'currency'.

    """

    placeholder_asset_account = r'Assets:_AssetAccountForCashflows'
    asset_account_map = dict((pattern, placeholder_asset_account)
                            for pattern in interesting_accounts + internal_accounts)
    return get_cashflows_by_asset_account(
        entries=entries, asset_account_map=asset_account_map,
        start_date_inclusive=date_from,
        end_date_inclusive=date_to,
        currency=currency)[placeholder_asset_account]
