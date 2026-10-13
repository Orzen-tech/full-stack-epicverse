"""Knowledge-base spreadsheets: the loader must load them, and must say so loudly when it
cannot. Offline: reads only the files under backend/data and a temporary empty folder."""
import pathlib

import pytest

from app.services import retriever

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MODE_FILES = [f"mode {i}.xlsx" for i in range(1, 8)]


@pytest.fixture
def clean_caches():
    cache = dict(retriever.KNOWLEDGE_BASE_CACHE)
    valid = {k: set(v) for k, v in retriever.VALID_CHARACTERS_PER_MODE.items()}
    retriever.KNOWLEDGE_BASE_CACHE.clear()
    yield
    retriever.KNOWLEDGE_BASE_CACHE.clear()
    retriever.KNOWLEDGE_BASE_CACHE.update(cache)
    retriever.VALID_CHARACTERS_PER_MODE.clear()
    retriever.VALID_CHARACTERS_PER_MODE.update(valid)


def test_the_seven_game_workbooks_exist():
    assert sorted(p.name for p in (BACKEND / "data").glob("*.xlsx")) == MODE_FILES


async def test_loader_reads_every_mode(monkeypatch, clean_caches):
    monkeypatch.chdir(BACKEND)
    await retriever.load_excel_data()
    modes = {entry["gameplay_mode"] for entry in retriever.KNOWLEDGE_BASE_CACHE.values()}
    assert len(modes) == 7
    assert len(retriever.KNOWLEDGE_BASE_CACHE) > 0


async def test_loader_fails_loudly_when_nothing_loads(monkeypatch, tmp_path, capsys, clean_caches):
    monkeypatch.chdir(tmp_path)  # no data/ folder here
    with pytest.raises(RuntimeError, match=r"0 combinations loaded"):
        await retriever.load_excel_data()
    assert "not found" in capsys.readouterr().out
    assert retriever.KNOWLEDGE_BASE_CACHE == {}


def test_cloud_run_upload_keeps_only_the_game_workbooks():
    rules = [line.strip() for line in (BACKEND / ".gcloudignore").read_text().splitlines()]
    assert "*.xlsx" in rules and "data/*.xlsx" in rules          # invite sheets etc. still excluded
    reincluded = [r for r in rules if r.startswith("!") and ".xlsx" in r]
    assert reincluded == [f"!data/{name}" for name in MODE_FILES]  # exactly the seven, nothing broader
