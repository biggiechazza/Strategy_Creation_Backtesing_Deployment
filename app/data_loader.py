# Load validated NQ CSV rows as the shared engine Bar model.
from __future__ import annotations
import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import BinaryIO, TextIO
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

# INTERNAL FUNCTIONS --------------------------------------------------------
def parse_timestamp(value: str | None, row_number: int) -> datetime:
    '''Parse an ISO timestamp while keeping any source timezone offset.'''

    if not value or ('T' not in value and ' ' not in value):
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}') from error

def parse_price(value: str | None, column: str, row_number: int) -> Decimal:
    '''Require a positive finite price aligned to the shared NQ tick size.'''

    try:
        price = Decimal(value) if value is not None else Decimal('NaN')
    except InvalidOperation as error:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}') from error
    if not price.is_finite():
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')
    if not isfinite(float(price)):
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')
    if price <= 0:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')
    try:
        on_tick = price % Tick_size == 0 # Tick_size comes from the shared NQ model
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
        raise ValueError(f'Invalid trade_date in row {row_number}: {value!r}') from error

class _HashingReader(io.RawIOBase):
    '''Add bytes consumed by the CSV reader to its SHA-256 digest.'''

    def __init__(self, source: BinaryIO, digest):
        self.source = source
        self.digest = digest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        count = self.source.readinto(buffer)
        if count:
            self.digest.update(memoryview(buffer)[:count])
        return count


def _read_nq_data(source: TextIO) -> list[Bar]:
    '''Parse and validate NQ rows from one open text stream.'''
    bars = [] # Bar Objects
    previous_timestamp = None

    reader = csv.DictReader(source)
    if reader.fieldnames is None:
        raise ValueError('Dataset contains no rows')
    missing = [name for name in Required_columns if name not in reader.fieldnames]
    if missing:
        missing_names = ', '.join(missing)
        raise ValueError(f'Missing required columns: {missing_names}')
    for row in reader:
        timestamp = parse_timestamp(row['Date'], reader.line_num) # 'reader.line_num' is an attribute of DictReader
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
        if contract is None or not re.fullmatch(r'NQ[HMUZ][0-9]{1,2}', contract.strip()):
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


# PRIMARY FUNCTIONS -----------------------------------------------------
def load_nq_data(path: str | Path) -> list[Bar]:
    '''Return chronological, validated bars from the NQ CSV file.'''
    with Path(path).open('r', encoding='utf-8-sig', newline='') as source:
        return _read_nq_data(source)


def load_nq_data_with_hash(path: str | Path) -> tuple[list[Bar], str]:
    '''Return validated bars and the SHA-256 of their source CSV bytes.'''
    digest = hashlib.sha256()
    with Path(path).open('rb') as binary_source:
        reader = io.BufferedReader(_HashingReader(binary_source, digest))
        with io.TextIOWrapper(reader, encoding='utf-8-sig', newline='') as source:
            bars = _read_nq_data(source)
    return bars, digest.hexdigest()
