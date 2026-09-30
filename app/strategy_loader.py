# Load a stored strategy class from trusted Python source.
import sys
from types import ModuleType
from app.models import Strategy


def load_strategy(version: dict) -> Strategy:
    '''Run trusted Python source and create its named strategy class.'''
    class_name = version['entrypoint_name']
    if not class_name:
        raise ValueError('Strategy version needs an entrypoint_name')
    base_name = f'db_strategy_{version["strategy_version_id"]}'
    module_name = base_name
    suffix = 1
    while module_name in sys.modules:
        module_name = f'{base_name}_{suffix}'
        suffix += 1
    module = ModuleType(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(version['source_code'], module_name, 'exec'), module.__dict__)
        strategy_class = module.__dict__.get(class_name)
        if not isinstance(strategy_class, type):
            raise ValueError(f'Strategy class {class_name!r} was not found')
        strategy = strategy_class()
        if not callable(getattr(strategy, 'evaluate', None)):
            raise ValueError(f'Strategy class {class_name!r} needs evaluate()')
    except BaseException:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return strategy
