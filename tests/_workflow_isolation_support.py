"""Load a private workflow instance and register cleanup before execution."""
import importlib
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch


def load_isolated_workflow(cleanups):
    package = importlib.import_module("intake_agent_lc")
    attribute = "_isolated_workflow_test_instance"
    name = f"{package.__name__}.{attribute}"
    if name in sys.modules or hasattr(package, attribute):
        raise AssertionError("An earlier isolated Workflow instance was not cleaned up")
    source = Path(__file__).resolve().parents[1] / "intake_agent_lc" / "Workflow.py"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise AssertionError("Cannot load Workflow source")
    workflow = importlib.util.module_from_spec(spec)
    cleanups.callback(sys.modules.pop, name, None)
    sys.modules[name] = workflow
    cleanups.enter_context(patch.object(package, attribute, workflow, create=True))
    spec.loader.exec_module(workflow)
    return workflow
