from importlib import util
from pathlib import Path
from types import ModuleType
import sys


ROOT = Path(__file__).parents[3]
RESEARCH_DIR = ROOT / "vnpy" / "alpha" / "research"

if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))


def load_research_module(name: str) -> ModuleType:
    """Load a research module without importing the full vnpy.alpha package."""
    module_path = RESEARCH_DIR / f"{name}.py"
    spec = util.spec_from_file_location(f"alpha_research_{name}", module_path)
    assert spec is not None
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
