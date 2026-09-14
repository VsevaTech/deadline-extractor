"""Make sure the generated example documents exist before the suite runs."""

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default suite is offline: rule-based extractor regardless of a local .env.
# The live Gemini test (tests/test_llm_live.py) opts in explicitly.
os.environ.setdefault("DE_EXTRACTOR", "rules")


def pytest_sessionstart(session):
    spec = importlib.util.spec_from_file_location(
        "generate_examples", ROOT / "examples" / "generate_examples.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_examples()
