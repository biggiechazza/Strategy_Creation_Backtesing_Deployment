'''Focused tests for the NQ CSV to Bar boundary.'''

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app.data_loader import load_nq_data
from app.models import Bar


_REQUIRED_COLUMNS = [
    'Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'contract', 'trade_date'
]


class DataLoaderTests(unittest.TestCase):
    '''Exercise parsing and market-data validation with tiny CSV inputs.'''

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'nq.csv'

    def make_row(self, **changes: str) -> dict[str, str]:
        '''Return one valid source row with selected values changed.'''

        row = {
            'Date': '2024-06-11T00:00:00Z',
            'Open': '19000.00',
            'High': '19001.25',
            'Low': '18999.75',
            'Close': '19000.50',
            'Volume': '47',
            'contract': 'NQM4',
            'trade_date': '2024-06-10',
        }
        row.update(changes)
        return row

    def write_rows(
        self,
        rows: list[dict[str, str]],
        columns: list[str] | None = None,
    ) -> Path:
        '''Write an artificial CSV and return its path.'''

        with self.path.open('w', encoding='utf-8', newline='') as target:
            writer = csv.DictWriter(target, fieldnames=columns or _REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return self.path

    def test_valid_rows_become_shared_bars_and_preserve_trade_date(self) -> None:
        bars = load_nq_data(self.write_rows([self.make_row()]))

        self.assertIsInstance(bars, list)
        self.assertEqual(len(bars), 1)
        self.assertIsInstance(bars[0], Bar)
        self.assertEqual(bars[0].timestamp, datetime(2024, 6, 11, tzinfo=timezone.utc))
        self.assertEqual(bars[0].trade_date, date(2024, 6, 10))
        self.assertEqual(bars[0].volume, 47)
        self.assertEqual(bars[0].close, 19000.5)

    def test_naive_timestamp_remains_naive(self) -> None:
        row = self.make_row(Date='2024-06-11T00:00:00')
        bar = load_nq_data(self.write_rows([row]))[0]

        self.assertIsNone(bar.timestamp.tzinfo)

    def test_missing_required_columns_fail_clearly(self) -> None:
        columns = [name for name in _REQUIRED_COLUMNS if name not in {'Close', 'contract'}]
        row = {name: value for name, value in self.make_row().items() if name in columns}

        with self.assertRaisesRegex(ValueError, 'Missing required columns: Close, contract'):
            load_nq_data(self.write_rows([row], columns))

    def test_empty_file_and_header_only_file_fail(self) -> None:
        self.path.write_text('', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Dataset contains no rows'):
            load_nq_data(self.path)

        with self.assertRaisesRegex(ValueError, 'Dataset contains no rows'):
            load_nq_data(self.write_rows([]))

    def test_invalid_timestamp_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Invalid timestamp in row 2'):
            load_nq_data(self.write_rows([self.make_row(Date='not-a-time')]))

    def test_unsorted_timestamps_fail(self) -> None:
        rows = [
            self.make_row(Date='2024-06-11T00:01:00Z'),
            self.make_row(Date='2024-06-11T00:00:00Z'),
        ]
        with self.assertRaisesRegex(ValueError, 'not chronological'):
            load_nq_data(self.write_rows(rows))

    def test_duplicate_timestamps_fail(self) -> None:
        rows = [self.make_row(), self.make_row()]
        with self.assertRaisesRegex(ValueError, 'Duplicate timestamp found'):
            load_nq_data(self.write_rows(rows))

    def test_invalid_ohlc_fails(self) -> None:
        for changes in ({'High': '19000.25'}, {'Low': '19000.75'}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, 'Invalid OHLC relationship'):
                    load_nq_data(self.write_rows([self.make_row(**changes)]))

    def test_malformed_and_nonfinite_ohlc_fail(self) -> None:
        for value in ('bad', '', 'NaN', 'Infinity', '-Infinity', '1e1000'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Invalid Open price'):
                    load_nq_data(self.write_rows([self.make_row(Open=value)]))

    def test_negative_and_nonintegral_volume_fail(self) -> None:
        for value, message in (('-1', 'Negative volume'), ('100.7', 'Invalid Volume')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    load_nq_data(self.write_rows([self.make_row(Volume=value)]))

    def test_integral_decimal_volume_is_accepted(self) -> None:
        bar = load_nq_data(self.write_rows([self.make_row(Volume='100.0')]))[0]
        self.assertEqual(bar.volume, 100)

    def test_blank_contract_fails(self) -> None:
        for value in ('', '   '):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Invalid contract'):
                    load_nq_data(self.write_rows([self.make_row(contract=value)]))

    def test_invalid_or_missing_trade_date_fails(self) -> None:
        for value in ('', '2024-99-99'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Invalid trade_date'):
                    load_nq_data(self.write_rows([self.make_row(trade_date=value)]))

    def test_off_tick_price_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, 'not aligned to NQ tick size'):
            load_nq_data(self.write_rows([self.make_row(Close='19000.10')]))

    def test_timestamp_gap_is_accepted(self) -> None:
        rows = [
            self.make_row(),
            self.make_row(Date='2024-06-11T00:10:00Z'),
        ]
        bars = load_nq_data(self.write_rows(rows))

        self.assertEqual(len(bars), 2)
        self.assertEqual((bars[1].timestamp - bars[0].timestamp).total_seconds(), 600)

    def test_vwap_is_optional_and_empty_values_are_ignored(self) -> None:
        columns = [*_REQUIRED_COLUMNS, 'vwap']
        row = self.make_row(vwap='')
        bars = load_nq_data(self.write_rows([row], columns))

        self.assertEqual(len(bars), 1)

    def test_mixed_timezone_awareness_fails_clearly(self) -> None:
        rows = [self.make_row(), self.make_row(Date='2024-06-11T00:01:00')]
        with self.assertRaisesRegex(ValueError, 'mixed timezone-aware and timezone-naive'):
            load_nq_data(self.write_rows(rows))


if __name__ == '__main__':
    unittest.main()
