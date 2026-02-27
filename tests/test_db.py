from pathlib import Path

from analizador.db import init_db, insert_observation, stats_for_day


def test_insert_and_stats_for_specific_day(tmp_path: Path) -> None:
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)

    insert_observation("2026-02-27T10:00:00", 3, 5, db_path=db_path)
    insert_observation("2026-02-27T11:00:00", 1, 2, db_path=db_path)
    insert_observation("2026-02-26T10:00:00", 9, 9, db_path=db_path)

    stats = stats_for_day("2026-02-27", db_path=db_path)

    assert stats["observations"] == 2
    assert stats["total_unique"] == 4
    assert stats["avg_unique"] == 2.0
    assert stats["max_unique"] == 3
    assert stats["min_unique"] == 1
