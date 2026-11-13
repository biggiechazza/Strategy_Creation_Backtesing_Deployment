# Load trusted strategy source from the DB into a fresh instance

import sys
from types import ModuleType
from app.models import Strategy


# The version dict supplies source_code, strategy_version_id, and the exact entrypoint_name
# Its class is constructed without arguments and must provide a callable evaluate
# Missing keys, source errors, and constructor errors pass through
def load_strategy(version: dict) -> Strategy:
    class_name = version['entrypoint_name']
    if not class_name:
        raise ValueError('Strategy version needs an entrypoint_name')

    # Each load needs its own module name so earlier instances keep their original module
    base_name = f'db_strategy_{version["strategy_version_id"]}'
    module_name = base_name
    suffix = 1
    while module_name in sys.modules:
        module_name = f'{base_name}_{suffix}'
        suffix += 1

    # Decorators such as dataclass look up the module while the source is executing
    module = ModuleType(module_name)
    sys.modules[module_name] = module
    try:
        # Stored source runs in this process without a sandbox
        exec(compile(version['source_code'], module_name, 'exec'), module.__dict__)

        strategy_class = module.__dict__.get(class_name)
        if not isinstance(strategy_class, type):
            raise ValueError(f'Strategy class {class_name!r} was not found')

        strategy = strategy_class()
        if not callable(getattr(strategy, 'evaluate', None)):
            raise ValueError(f'Strategy class {class_name!r} needs evaluate()')
    except BaseException:

    # On failure, leave any replacement module alone and let the original error through
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return strategy
