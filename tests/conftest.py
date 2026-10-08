import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def demo_dir() -> Path:
    return ROOT / "demo"


@pytest.fixture(scope="session")
def demo_csv(tmp_path_factory, demo_dir) -> Path:
    from demo.make_demo import generate
    p = tmp_path_factory.mktemp("demo") / "demo_raw.csv"
    return generate(p)


@pytest.fixture(scope="session")
def codebook(demo_dir):
    from filedrawer.qsf import parse_qsf
    return parse_qsf(demo_dir / "demo.qsf")
