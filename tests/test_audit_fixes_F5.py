"""C-01/C-02/C-03: tests must never resolve the owner's data/ dir or config.yaml."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = (ROOT / "data").resolve()
REAL_CONFIG = (ROOT / "config.yaml").resolve()


def _inside_real_data(p) -> bool:
    p = Path(p).resolve()
    return p == REAL_DATA or REAL_DATA in p.parents


def test_paths_are_isolated_from_the_real_repo():
    from src import config, recorder, storage
    from src.review import store
    for p in (storage.DB_PATH, store.DB_PATH, recorder.LOCK_PATH):
        assert not _inside_real_data(p), p
    assert Path(config.CONFIG_PATH).resolve() != REAL_CONFIG
    assert storage.connect.__defaults__[0] == storage.DB_PATH
    assert config.load_config.__defaults__[0] == config.CONFIG_PATH
