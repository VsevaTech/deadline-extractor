"""Make sure the generated example documents exist before the suite runs."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pytest_sessionstart(session):
    spec = importlib.util.spec_from_file_location(
        "generate_examples", ROOT / "examples" / "generate_examples.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_examples()
