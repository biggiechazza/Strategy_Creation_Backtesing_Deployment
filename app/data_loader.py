# CSV values become Bar objects here, keeping the supplied timestamps and session dates
# Validation errors use ValueError, file and decoding errors pass through

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

# Float rounding can hide an off tick price, so tick checks use Decimal
Tick_size = Decimal(str(NQ_TICK_SIZE))


# Read Date as an ISO timestamp, keeping any supplied timezone offset
# This loader needs a time as well as a date, and does not pick a timezone for missing offsets
def parse_timestamp(value: str | None, row_number: int) -> datetime:
    if not value or ('T' not in value and ' ' not in value):
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Invalid timestamp in row {row_number}: {value!r}') from error


# Prices must be positive, finite, and on a valid NQ tick before becoming Bar floats
def parse_price(value: str | None, column: str, row_number: int) -> Decimal:
    try:
        price = Decimal(value) if value is not None else Decimal('NaN')
    except InvalidOperation as error:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}') from error
    if not price.is_finite():
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')

    # Decimal can hold a finite value that becomes infinity as a float
    if not isfinite(float(price)):
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')

    if price <= 0:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}')

    try:
        on_tick = price % Tick_size == 0
    except InvalidOperation as error:
        raise ValueError(f'Invalid {column} price in row {row_number}: {value!r}') from error
    if not on_tick:
        raise ValueError(f'{column} price is not aligned to NQ tick size in row 'f'{row_number}: {value!r}')
    return price


# Some CSVs write whole volume as 100.0, accept that but reject fractions and negatives
def parse_volume(value: str | None, row_number: int) -> int:
    try:
        volume = Decimal(value) if value is not None else Decimal('NaN')
    except InvalidOperation as error:
        raise ValueError(f'Invalid Volume in row {row_number}: {value!r}') from error
    if not volume.is_finite() or volume != volume.to_integral_value():
        raise ValueError(f'Invalid Volume in row {row_number}: {value!r}')
    if volume < 0:
        raise ValueError(f'Negative volume in row {row_number}: {value!r}')
    return int(volume)


# Use the supplied session date because a futures session can cross midnight
def parse_trade_date(value: str | None, row_number: int) -> date:
    if value is None:
        raise ValueError(f'Invalid trade_date in row {row_number}: {value!r}')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Invalid trade_date in row {row_number}: {value!r}') from error


# Track the raw bytes while the CSV reader consumes the file
# The hash includes the original encoding marker and line endings
class _HashingReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, digest):
        self.source = source
        self.digest = digest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        count = self.source.readinto(buffer)

        # Only part of the buffer may be filled, the unused bytes should not affect the hash
        if count:
            self.digest.update(memoryview(buffer)[:count])
        return count


# An open CSV stream becomes a nonempty list of bars in its original order
# The caller remains responsible for closing the stream
def _read_nq_data(source: TextIO) -> list[Bar]:
    bars = []
    previous_timestamp = None

    # Missing headers should explain the bad schema instead of failing later with a KeyError
    reader = csv.DictReader(source)
    if reader.fieldnames is None:
        raise ValueError('Dataset contains no rows')
    missing = [name for name in Required_columns if name not in reader.fieldnames]
    if missing:
        missing_names = ', '.join(missing)
        raise ValueError(f'Missing required columns: {missing_names}')
    for row in reader:
        timestamp = parse_timestamp(row['Date'], reader.line_num)

    # A repeated or earlier timestamp would break the engine's forward order
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

        # Strategy price checks depend on High and Low containing the bar's other prices
        if high < max(opening, low, close) or low > min(opening, high, close):
            raise ValueError('Invalid OHLC relationship at timestamp 'f'{timestamp.isoformat()} (row {reader.line_num})')

        # The NQ point and tick values would be wrong for another product
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


# The path based loader accepts UTF8 files with or without a BOM
def load_nq_data(path: str | Path) -> list[Bar]:
    with Path(path).open('r', encoding='utf-8-sig', newline='') as source:
        return _read_nq_data(source)


# The bars and their CSV fingerprint come from one read
# Hashing in a separate pass could identify different bytes if the file changes
def load_nq_data_with_hash(path: str | Path) -> tuple[list[Bar], str]:
    digest = hashlib.sha256()
    with Path(path).open('rb') as binary_source:
        reader = io.BufferedReader(_HashingReader(binary_source, digest))
        with io.TextIOWrapper(reader, encoding='utf-8-sig', newline='') as source:
            bars = _read_nq_data(source)
    return bars, digest.hexdigest()
