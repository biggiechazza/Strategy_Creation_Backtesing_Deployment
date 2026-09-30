"""Instantiate a trusted strategy version stored in PostgreSQL."""

from app.models import Strategy


def load_strategy(version: dict) -> Strategy:
    """Run trusted Python source and create its named strategy class."""
    class_name = version["entrypoint_name"]
    if not class_name:
        raise ValueError("Strategy version needs an entrypoint_name")
    namespace = {"__name__": f"db_strategy_{version['strategy_version_id']}"}
    exec(compile(version["source_code"], namespace["__name__"], "exec"), namespace)
    strategy_class = namespace.get(class_name)
    if not isinstance(strategy_class, type):
        raise ValueError(f"Strategy class {class_name!r} was not found")
    strategy = strategy_class()
    if not callable(getattr(strategy, "evaluate", None)):
        raise ValueError(f"Strategy class {class_name!r} needs evaluate()")
    return strategy
