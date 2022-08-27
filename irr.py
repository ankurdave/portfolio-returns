#!/usr/bin/env python

import logging
import sys
import itertools
import functools
import operator
import math
import collections
import datetime
import re
from ast import literal_eval
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from pprint import pprint
from scipy import optimize
from typing import Any, Dict, List, NamedTuple, Optional, Set, Union
import beancount.loader
import beancount.utils
import beancount.utils.table
import beancount.core
import beancount.core.getters
import beancount.core.data
import beancount.core.convert
import beancount.parser
from pprint import pprint
from cashflows import get_cashflows, get_cashflows_by_asset_account

# https://github.com/peliot/XIRR-and-XNPV/blob/master/financial.py

def xnpv(rate,cashflows):
    """
    Calculate the net present value of a series of cashflows at irregular intervals.
    Arguments
    ---------
    * rate: the discount rate to be applied to the cash flows
    * cashflows: a list object in which each element is a tuple of the form (date, amount), where date is a python datetime.date object and amount is an integer or floating point number. Cash outflows (investments) are represented with negative amounts, and cash inflows (returns) are positive amounts.
    
    Returns
    -------
    * returns a single value which is the NPV of the given cash flows.
    Notes
    ---------------
    * The Net Present Value is the sum of each of cash flows discounted back to the date of the first cash flow. The discounted value of a given cash flow is A/(1+r)**(t-t0), where A is the amount, r is the discout rate, and (t-t0) is the time in years from the date of the first cash flow in the series (t0) to the date of the cash flow being added to the sum (t).  
    * This function is equivalent to the Microsoft Excel function of the same name. 
    """

    chron_order = sorted(cashflows, key = lambda x: x[0])
    t0 = chron_order[0][0] #t0 is the date of the first cash flow

    return sum([cf/(1+rate)**((t-t0).days/365.0) for (t,cf) in chron_order])

def xirr(cashflows,guess=0.1):
    """
    Calculate the Internal Rate of Return of a series of cashflows at irregular intervals.
    Arguments
    ---------
    * cashflows: a list object in which each element is a tuple of the form (date, amount), where date is a python datetime.date object and amount is an integer or floating point number. Cash outflows (investments) are represented with negative amounts, and cash inflows (returns) are positive amounts.
    * guess (optional, default = 0.1): a guess at the solution to be used as a starting point for the numerical solution. 
    Returns
    --------
    * Returns the IRR as a single value
    
    Notes
    ----------------
    * The Internal Rate of Return (IRR) is the discount rate at which the Net Present Value (NPV) of a series of cash flows is equal to zero. The NPV of the series of cash flows is determined using the xnpv function in this module. The discount rate at which NPV equals zero is found using the secant method of numerical solution. 
    * This function is equivalent to the Microsoft Excel function of the same name.
    * For users that do not have the scipy module installed, there is an alternate version (commented out) that uses the secant_method function defined in the module rather than the scipy.optimize module's numerical solver. Both use the same method of calculation so there should be no difference in performance, but the secant_method function does not fail gracefully in cases where there is no solution, so the scipy.optimize.newton version is preferred.
    """
    return optimize.newton(lambda r: xnpv(r,cashflows),guess)

def fmt_d(n):
    if n is None: return None
    return '${:,.2f}'.format(n)

def fmt_pct(n):
    return '{0:.2f}%'.format(n*100)

if __name__ == '__main__':
    logging.basicConfig(format='%(levelname)s: %(message)s')
    import argparse
    parser = argparse.ArgumentParser(
        description="Calculate return data."
    )
    parser.add_argument('bean', help='Path to the beancount file.')
    parser.add_argument('--currency', default='USD', help='Currency to use for calculating returns.')
    parser.add_argument('--account', action='append', default=[], help='Regex pattern of accounts to include when calculating returns. Can be specified multiple times.')
    parser.add_argument('--internal', action='append', default=[], help='Regex pattern of accounts that represent internal cashflows (i.e. dividends or interest)')

    parser.add_argument('--asset-account-map',
                        help='Dict[pattern, replacement] that maps each account to its ' +
                        'corresponding asset account.')
    parser.add_argument('--debug-asset-account-cashflows',
                        help='Asset account for which to print all cashflow transactions.')

    parser.add_argument('--from', dest='date_from', type=lambda d: datetime.datetime.strptime(d, '%Y-%m-%d').date(), help='Start date: YYYY-MM-DD, 2016-12-31')
    parser.add_argument('--to', dest='date_to', type=lambda d: datetime.datetime.strptime(d, '%Y-%m-%d').date(), help='End date YYYY-MM-DD, 2016-12-31')

    date_range = parser.add_mutually_exclusive_group()
    date_range.add_argument('--year', default=False, type=int, help='Year. Shorthand for --from/--to.')    
    date_range.add_argument('--ytd', action='store_true')
    date_range.add_argument('--1year', action='store_true')
    date_range.add_argument('--2year', action='store_true')
    date_range.add_argument('--3year', action='store_true')
    date_range.add_argument('--5year', action='store_true')
    date_range.add_argument('--10year', action='store_true')

    parser.add_argument('--debug-inflows', action='store_true', help='Print list of all inflow accounts in transactions.')
    parser.add_argument('--debug-outflows', action='store_true', help='Print list of all outflow accounts in transactions.')
    parser.add_argument('--debug-cashflows', action='store_true', help='Print list of all cashflows used for the IRR calculation.')

    args = parser.parse_args()

    shortcuts = ['year', 'ytd', '1year', '2year', '3year', '5year', '10year']
    shortcut_used = functools.reduce(operator.__or__, [getattr(args, x) for x in shortcuts])
    if shortcut_used and (args.date_from or args.date_to):
        raise(parser.error('Date shortcut options mutually exclusive with --to/--from options'))

    if args.year:
        args.date_from = datetime.date(args.year, 1, 1)
        args.date_to = datetime.date(args.year, 12, 31)

    if args.ytd:
        today = datetime.date.today()
        args.date_from = datetime.date(today.year, 1, 1)
        args.date_to = today

    if getattr(args, '1year'):
        today = datetime.date.today()
        args.date_from = today + relativedelta(years=-1)
        args.date_to = today

    if getattr(args, '2year'):
        today = datetime.date.today()
        args.date_from = today + relativedelta(years=-2)
        args.date_to = today

    if getattr(args, '3year'):
        today = datetime.date.today()
        args.date_from = today + relativedelta(years=-3)
        args.date_to = today

    if getattr(args, '5year'):
        today = datetime.date.today()
        args.date_from = today + relativedelta(years=-5)
        args.date_to = today

    if getattr(args, '10year'):
        today = datetime.date.today()
        args.date_from = today + relativedelta(years=-10)
        args.date_to = today

    entries, errors, options = beancount.loader.load_file(args.bean, logging.info, log_errors=sys.stderr)

    if not args.date_to:
        args.date_to = datetime.date.today()

    if args.asset_account_map:
        asset_account_map: Dict[str, str] = literal_eval(args.asset_account_map)
        cashflows_by_asset_account = get_cashflows_by_asset_account(
            entries=entries, asset_account_map=asset_account_map,
            start_date_inclusive=args.date_from, end_date_inclusive=args.date_to,
            currency=args.currency)

        if args.debug_asset_account_cashflows:
            cashflows = cashflows_by_asset_account[args.debug_asset_account_cashflows]
            for cashflow in cashflows:
                print(f'{cashflow.date}: {cashflow.amount}')
                if cashflow.kind == 'starting balance':
                    print('(Starting balance at market value)')
                elif cashflow.kind == 'ending balance':
                    print('(Ending balance at market value)')
                else:
                    print(beancount.parser.printer.format_entry(cashflow.entry))

        field_spec = [(0, 'Asset Account'), (1, 'Net Inflows'), (2, 'Market Value'), (3, 'IRR')]
        rows = []
        for asset_account, cashflows in sorted(cashflows_by_asset_account.items()):
            net_inflows = sum([f.amount for f in cashflows if f.kind != 'ending balance'])
            market_value = next((-f.amount for f in cashflows if f.kind == 'ending balance'), None)
            try:
                irr = fmt_pct(xirr([(f.date, float(f.amount)) for f in cashflows]))
            except OverflowError:
                irr = '(overflow)'
            except RuntimeError:
                irr = '(diverged)'
            rows.append((asset_account, fmt_d(net_inflows), fmt_d(market_value), irr))
        table = beancount.utils.table.create_table(rows, field_spec)
        beancount.utils.table.render_table(table, sys.stdout, 'text')
    else:
        cashflows = get_cashflows(
            entries=entries, interesting_accounts=args.account, internal_accounts=args.internal,
            date_from=args.date_from, date_to=args.date_to, currency=args.currency)

        if cashflows:
            # we need to coerce everything to a float for xirr to work...
            r = xirr([(f.date, float(f.amount)) for f in cashflows])
            print(fmt_pct(r))
        else:
            logging.error(f'No cashflows found during the time period {args.date_from} -> {args.date_to}')

        if args.debug_cashflows:
            pprint([(f.date, f.amount) for f in cashflows])
        if args.debug_inflows:
            print('>> [inflows]')
            pprint(set().union(*[f.inflows for f in cashflows]))
        if args.debug_outflows:
            print('<< [outflows]')
            pprint(set().union(*[f.outflows for f in cashflows]))
