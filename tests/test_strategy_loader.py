# Verify stored strategies load as real modules and keep their validation behavior.
import sys
import unittest
from typing import get_type_hints
from app.models import Signal
from app.strategy_loader import load_strategy


class StrategyLoaderTests(unittest.TestCase):
    '''Check stored strategy compilation and module lifetime.'''

    def test_future_annotated_dataclass_loads(self):
        version = {'strategy_version_id': 987654321,
            'entrypoint_name': 'StoredStrategy',
            'source_code': '''from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar
from app.models import Signal
Threshold = float
@dataclass
class StoredStrategy:
    minimum: Threshold = 1.25
    name: ClassVar[str] = 'stored'
    def evaluate(self, current_bar, prior_bars):
        return Signal.HOLD
'''}
        strategy = load_strategy(version)
        self.addCleanup(sys.modules.pop, type(strategy).__module__, None)

        self.assertEqual(strategy.minimum, 1.25)
        self.assertIs(strategy.evaluate(None, []), Signal.HOLD)
        self.assertIs(get_type_hints(type(strategy))['minimum'], float)
        self.assertIs(sys.modules[type(strategy).__module__].StoredStrategy, type(strategy))

    def test_repeated_load_preserves_previous_module(self):
        version = {'strategy_version_id': 987654322,
            'entrypoint_name': 'StoredStrategy',
            'source_code': '''class StoredStrategy:
    def evaluate(self, current_bar, prior_bars):
        return None
'''}
        first = load_strategy(version)
        self.addCleanup(sys.modules.pop, type(first).__module__, None)
        second = load_strategy(version)
        self.addCleanup(sys.modules.pop, type(second).__module__, None)

        self.assertEqual(type(first).__module__, 'db_strategy_987654322')
        self.assertEqual(type(second).__module__, 'db_strategy_987654322_1')
        self.assertIs(sys.modules[type(first).__module__].StoredStrategy, type(first))
        self.assertIs(sys.modules[type(second).__module__].StoredStrategy, type(second))

    def test_failed_construction_cleans_up_temporary_module(self):
        version = {'strategy_version_id': 987654323,
            'entrypoint_name': 'BadStrategy',
            'source_code': '''class BadStrategy:
    def __init__(self):
        raise RuntimeError('broken constructor')
'''}
        prefix = 'db_strategy_987654323'
        before = {name for name in sys.modules
            if name == prefix or name.startswith(prefix + '_')}

        with self.assertRaisesRegex(RuntimeError, 'broken constructor'):
            load_strategy(version)

        after = {name for name in sys.modules
            if name == prefix or name.startswith(prefix + '_')}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
