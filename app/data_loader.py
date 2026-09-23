# Load validated NQ CSV rows as the shared engine Bar model.
from __future__ import annotations
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from app.models import NQ_TICK_SIZE, Bar
Required_columns = (
    'Date',
    'Open',
    'High',
    'Low',
    'Close',
    'Volume',
    'contract',
    'trade_date',)
Price_columns = ('Open', 'High', 'Low', 'Close')
Tick_size = Decimal(str(NQ_TICK_SIZE))

def parse_timestamp(value: str | None, row_number: int) -> datetime:
    '''Parse an ISO timestamp while keeping any source timezone offset.'''

    if not value or ('T' not in value and ' ' not in value):
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}') from error

def parse_price(value: str | None, column: str, row_number: int) -> Decimal:
    '''Require a finite numeric price aligned to the shared NQ tick size.'''

    try:
        price = Decimal(value) if value is not None else Decimal('NaN')
    except InvalidOperation as error:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}') from error
    if not price.is_finite():
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')
    if not isfinite(float(price)):
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')
    try:
        on_tick = price %  Tick_size == 0 # Tick_size is the same as the one pulled from models.py
    except InvalidOperation as error:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}') from error
    if not on_tick:
        raise ValueError(f'{column} price is not aligned to NQ tick size in row 'f'{row_number}: {value!r}')
    return price

def parse_volume(value: str | None, row_number: int) -> int:
    '''Accept non-negative integral volume, including values such as 100.0.'''

    try:
        volume = Decimal(value) if value is not None else Decimal('NaN')
    except InvalidOperation as error:
        raise ValueError(f'Invalid Volume in row {row_number}: {value!r}') from error
    if not volume.is_finite() or volume != volume.to_integral_value():
        raise ValueError(f'Invalid Volume in row {row_number}: {value!r}')
    if volume < 0:
        raise ValueError(f'Negative volume in row {row_number}: {value!r}')
    return int(volume)

def parse_trade_date(value: str | None, row_number: int) -> date:
    '''Parse the supplied futures session date without deriving it from Date.'''

    if value is None:
        raise ValueError(f'Invalid trade_date in row {row_number}: {value!r}')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f'Invalid trade_date in row {row_number}: {value!r}') from error

def load_nq_data(path: str | Path): # Returns a list of Bar objects
    '''Returns chronological, validated Bar objects from the NQ CSV file.
    The source trade_date is parsed as a ISO timestamp, preserving its timezone if
    present. The supplied trade_date remains separate from the timestamp's
    calendar date. Optional CSV fields are ignored. Gaps are allowed, duplicate
    or decreasing timestamps and invalid market data raise ValueErrors.'''
    bars = [] # Bar Objects
    previous_timestamp = None

    with Path(path).open('r', encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source) 
        if reader.fieldnames is None:
            raise ValueError('Dataset contains no rows') 
        missing = [name for name in Required_columns if name not in reader.fieldnames]
        if missing:
            missing_names = ', '.join(missing)
            raise ValueError(f'Missing required columns: {missing_names}')
        for row in reader:
            timestamp = parse_timestamp(row['Date'], reader.line_num) # 'reader.line_num' is and attribute of DictReader'
            if previous_timestamp is not None:
                try:
                    if timestamp == previous_timestamp:
                        raise ValueError(f'Duplicate timestamp found: {timestamp.isoformat()}')
                    if timestamp < previous_timestamp:
                        raise ValueError('Dataset timestamps are not chronological at row 'f'{reader.line_num}: {timestamp.isoformat()}')
                except TypeError as error:
                    raise ValueError('Dataset contains mixed timezone-aware and timezone-naive 'f'timestamps at row {reader.line_num}') from error
            prices = {name: parse_price(row[name], name, reader.line_num)
                for name in Price_columns}
            opening = prices['Open']
            high = prices['High']
            low = prices['Low']
            close = prices['Close']
            if high < max(opening, low, close) or low > min(opening, high, close):
                raise ValueError('Invalid OHLC relationship at timestamp 'f'{timestamp.isoformat()} (row {reader.line_num})')
            contract = row['contract']
            if contract is None or not contract.strip():
                raise ValueError(f'Invalid contract in row {reader.line_num}: {contract!r}')

            bars.append(Bar(timestamp=timestamp,
                    open=float(opening),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=parse_volume(row['Volume'], reader.line_num),
                    contract=contract.strip(),
                    trade_date=parse_trade_date(row['trade_date'], reader.line_num),))
            previous_timestamp = timestamp
    if not bars:
        raise ValueError('Dataset contains no rows')
    return bars