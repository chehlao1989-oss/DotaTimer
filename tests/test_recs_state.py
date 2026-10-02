"""Инкрементальный расчёт по дневным суммам совпадает с расчётом с нуля (решение автора 02.10)."""
import pyarrow as pa
import pyarrow.parquet as pq

from tests.test_compute_recs import HEROES, ITEMS, make_matches
from tools.compute_recs import compute, load_config
from tools.recs_state import compute_from_state, state_config, update

DAY = 86400
START = 1780000000  # 2026-05-28 20:26 UTC: с разбросом до 10 ч матчи ложатся на 5 разных дней


def spread(src, dst, first, last, shift, only_day=None):
    """Матчи first…last-1 из src, раскиданные по дням: start_time = START + shift + (match_id % 3) дня + минуты."""
    table = pq.read_table(src)
    rows = [dict(r, start_time=START + shift + (r["match_id"] % 3) * DAY + r["match_id"] % 600 * 60)
            for r in table.to_pylist() if first <= r["match_id"] < last
            and (only_day is None or r["match_id"] % 3 == only_day)]
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), dst)


def flat(d, prefix=()):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from flat(v, prefix + (k,))
    else:
        yield prefix, d


def close(a, b, tol=0.11):
    """Совпадение с точностью до округления в файле (0,1 п.п.; S — 0,01): суммы по дням складываются в другом порядке."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= tol
    return a == b


def test_incremental_equals_from_scratch(tmp_path):
    base = tmp_path / "base.parquet"
    make_matches(base, n=3000)
    raw = tmp_path / "raw"
    raw.mkdir()
    # первая порция: матчи 0–1999; вторая: 2000–2999 и повтор части первой (пересечение запусков сбора)
    spread(base, raw / "matches-a.parquet", 0, 2000, 0)
    cfg = load_config()
    refs = (ITEMS, {}, {}, HEROES)
    state = tmp_path / "state"
    update(raw, state, refs, cfg, log=lambda *_: None)
    spread(base, raw / "matches-b.parquet", 2000, 3000, DAY)
    # повтор — только матчи последних дней первой порции: эти дни ещё не заморожены, их суммы вычитаются и считаются заново
    spread(base, raw / "matches-c.parquet", 1500, 2000, 0, only_day=2)
    logs = []
    manifest = update(raw, state, refs, cfg, log=logs.append)
    assert not any("пересборка" in line for line in logs), logs  # путь «итог − старое + новое», а не пересборка
    assert len(manifest["days"]) == 5  # 3 дня × 2 порции со сдвигом и переходом через полночь
    incremental = compute_from_state(state, refs, cfg, log=lambda *_: None)
    # хранилище — без L1 (DECISIONS №23): эталон с нуля считаем с теми же уровнями
    scratch = compute(str(raw / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES, state_config(cfg),
                      log=lambda *_: None)
    fa, fb = dict(flat(scratch)), dict(flat(incremental))
    skip = ("generated_at", "seconds")
    assert set(k for k in fa if k[-1] not in skip) == set(k for k in fb if k[-1] not in skip)
    bad = [k for k in fa if k[-1] not in skip and not close(fa[k], fb[k])]
    assert not bad, [(k, fa[k], fb[k]) for k in bad[:3]]
    assert incremental["meta"]["matches"] == scratch["meta"]["matches"] == 3000
