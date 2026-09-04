from pathlib import Path

import pytest

from dumper7_pyparser import Dump, load_dump

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mini_dump"


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def dump(fixture_dir: Path) -> Dump:
    return load_dump(fixture_dir, strict=True)
