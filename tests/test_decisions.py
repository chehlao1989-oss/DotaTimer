"""Тесты-сторожа действующих решений (docs/DECISIONS.md).

Если тест здесь упал — изменено действующее решение. Менять число можно только с новым замером:
сначала запись в DECISIONS.md (и BUGLOG.md/DATA_SOURCES.md), потом правка теста и кода в том же коммите.
"""
import json
import re
import urllib.error
from pathlib import Path

import tools.collect_raw as cr

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")


def test_code_references_existing_decisions():
    """Каждый «# DECISIONS №N» в коде и данных ссылается на существующую запись."""
    existing = set(re.findall(r"^### №(\d+) ", DECISIONS, flags=re.M))
    files = [*ROOT.glob("app/**/*.py"), *ROOT.glob("tools/*.py"), *ROOT.glob("data/*.json"),
             *ROOT.glob(".github/workflows/*.yml")]
    missing = {(str(f.relative_to(ROOT)), n) for f in files
               for n in re.findall(r"DECISIONS №(\d+)", f.read_text(encoding="utf-8")) if n not in existing}
    assert not missing


def test_raw_match_schema():
    """№1: храним сырые строки на игрока с итоговым инвентарём, а не суммы."""
    assert cr.MATCH_SCHEMA.names == [
        "match_id", "start_time", "duration", "game_mode", "lobby_type", "radiant_win", "patch", "is_radiant",
        "hero_id", "items", "item_neutral", "net_worth", "gold_per_min", "xp_per_min", "kills", "deaths",
        "assists", "last_hits"]


def test_steam_regulator_matches_decision():
    """№2: регулятор автора — ×1,5 после 429, ×0,9 после 20 успешных, пауза 1–10 с, Retry-After иначе 20 с."""
    assert (cr.PAUSE_START_SEC, cr.PAUSE_MIN_SEC, cr.PAUSE_MAX_SEC) == (3.0, 1.0, 10.0)
    assert (cr.PAUSE_UP_FACTOR, cr.PAUSE_DOWN_FACTOR, cr.CALM_CALLS) == (1.5, 0.9, 20)
    assert cr.RETRY_AFTER_DEFAULT_SEC == 20.0
    assert cr.LOG_EVERY_SEC == 600


def test_error_streak_does_not_stop_collection(tmp_path, monkeypatch):
    """№3: после 20 ошибок подряд сбор ждёт и продолжает, а не заканчивается (запуск №5 бросил сбор)."""
    assert (cr.MAX_ERRORS_IN_ROW, cr.LONG_WAIT_SEC) == (20, 300)
    steam_calls = {"n": 0}

    def fake(url, timeout=60):
        if "constants/patch" in url:
            return [{"name": "7.41", "date": "2026-03-24T00:00:00Z"}]
        steam_calls["n"] += 1
        if steam_calls["n"] <= 25:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
        return {"result": {"matches": []}}  # дошли до свежих матчей — конец

    monkeypatch.setattr(cr, "http_json", fake)
    monkeypatch.setattr(cr, "anchor_seq_num", lambda: 1)
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)
    stats = cr.collect_matches("key", 100, 60, tmp_path, "t")
    assert stats["steam_requests"] == 26  # 25 ошибок подряд не остановили сбор


def test_save_every_15_minutes():
    """№4: часть в релиз каждые 15 минут."""
    assert cr.SAVE_EVERY_SEC == 900


def test_opendota_limit_by_header_only():
    """№5: у сбора рангов нет своего бюджета запросов — только остаток лимита из заголовка и время."""
    import inspect
    assert (cr.OPENDOTA_PAUSE_SEC, cr.OPENDOTA_DAY_RESERVE, cr.OPENDOTA_MINUTE_WAIT_SEC) == (1.1, 100, 60)
    assert "calls" not in inspect.signature(cr.collect_ranks).parameters
    workflow = (ROOT / ".github" / "workflows" / "stats.yml").read_text(encoding="utf-8")
    ranks_line = next(line for line in workflow.splitlines() if "collect_raw.py ranks" in line)
    assert "--calls" not in ranks_line


def test_collection_schedule():
    """№6: расписание каждый час + сторож 11 ч (реально 2 сбора в сутки), на main 290 мин и 12 окон, ранги 20 мин."""
    import tools.collect_guard as guard
    workflow = (ROOT / ".github" / "workflows" / "stats.yml").read_text(encoding="utf-8")
    assert 'cron: "17 * * * *"' in workflow and "collect_guard.py --min-hours 11" in workflow
    assert guard.MIN_HOURS == 11
    assert "github.ref_name == 'main' && '290'" in workflow and "github.ref_name == 'main' && '12'" in workflow
    assert "--max-minutes 20" in workflow and "timeout-minutes: 350" in workflow


def test_alarm_dispatch_uses_guard():
    """BUGLOG №37: будильник (workflow_dispatch с guard=true) проходит через сторожа, как расписание;
    ручная кнопка (guard по умолчанию не отмечен) собирает всегда."""
    workflow = (ROOT / ".github" / "workflows" / "stats.yml").read_text(encoding="utf-8")
    block = workflow[workflow.index("workflow_dispatch:"):workflow.index("permissions:")]
    assert "guard:" in block and "type: boolean" in block and "default: false" in block
    guard_step = workflow[workflow.index("id: guard"):workflow.index("collect_guard.py")]
    assert "if: github.event_name == 'schedule' || inputs.guard" in guard_step


def test_no_hand_written_counter_table():
    """№8: связок «X против Y» из головы нет; правила механик — ровно 15 утверждённых автором."""
    assert not (ROOT / "data" / "counters.json").exists()
    rules = json.loads((ROOT / "data" / "mechanics.json").read_text(encoding="utf-8"))["rules"]
    assert [r["id"] for r in rules] == ["heal", "evasion", "invisibility", "illusions", "summons", "passives",
                                        "magic_immunity", "mobility", "magic_damage", "physical_damage", "saves",
                                        "mana", "spell_block", "disables", "armor"]


def test_roles_from_net_worth():
    """№9: роль героя в расчёте — по месту по нетворсу в команде; теги Valve (Carry…) для роли не используются."""
    code = (ROOT / "tools" / "compute_recs.py").read_text(encoding="utf-8")
    assert "PARTITION BY m.match_id, is_radiant ORDER BY net_worth DESC" in code
    assert "WHEN nw_place <= 2 THEN 'core' WHEN nw_place = 3 THEN 'offlane' ELSE 'support'" in code
    assert "Carry" not in code


def test_turbo_timings_have_source_or_unverified():
    """№10: у каждой записи Турбо — источник или verified: false (никаких «делим на 2» без источника)."""
    turbo = json.loads((ROOT / "data" / "timings.json").read_text(encoding="utf-8"))["turbo"]
    for name, entry in turbo.items():
        assert entry.get("source") or entry.get("verified") is False, name


def test_turbo_shrine_not_overridden():
    """№10: святилища в Турбо — как в обычной игре (подтверждено автором по игре 28.09)."""
    shrine = json.loads((ROOT / "data" / "timings.json").read_text(encoding="utf-8"))["turbo"]["shrine_of_wisdom"]
    assert set(shrine) <= {"source", "verified"} and shrine["verified"] is True


def test_threat_overlays_enabled_for_game_check():
    """№11: интерфейс угроз включён 30.09 для проверки в игре (карт-бланш автора), советы — из recs.zip."""
    raw = json.loads((ROOT / "data" / "threats.json").read_text(encoding="utf-8"))
    assert raw["overlays_enabled"] is True and "recs_advice" in raw


def test_recs_config_decisions():
    """№12: вес по давности 21 день; кандидат — если H покупает Y в ≥ 3% игр."""
    config = json.loads((ROOT / "data" / "recs_config.json").read_text(encoding="utf-8"))
    assert config["half_life_days"] == 21 and config["min_buy_share"] == 0.03


def test_mechanic_answers_first():
    """№18–19: первыми — ответы по механике с A > 0; широкое правило (признак у > 40% героев) — только при значимом A."""
    config = json.loads((ROOT / "data" / "recs_config.json").read_text(encoding="utf-8"))
    assert config["mechanic_first"] is True and config["mechanic_first_min_a"] == 0.0
    assert config["broad_rule_share"] == 0.4
    assert config["mechanic_first_b_significant"] is False  # «A > 0 или B значимо > 0» не включать (автор, 01.10)


def test_b_is_veto_only():
    """№20: λ = 0 — B только вето, пока знак B на свежих данных не совпадает в ≥ 70%."""
    config = json.loads((ROOT / "data" / "recs_config.json").read_text(encoding="utf-8"))
    assert config["lam"] == 0.0 and config["veto_b"] == 2.0


def test_checks_run_on_github():
    """№21, BUGLOG №39: тяжёлые проверки 8.1–8.4 — отдельный запуск на GitHub по кнопке, отчёт — в релиз recs-report."""
    workflow = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "tools/validate_recs.py" in workflow
    assert "gh release upload recs-report" in workflow


def test_recs_incremental_on_github():
    """№23: пересчёт советов на GitHub — по хранилищу дневных сумм (tools/recs_state.py), хранилище — в релизе."""
    workflow = (ROOT / ".github" / "workflows" / "recs.yml").read_text(encoding="utf-8")
    assert "tools/recs_state.py --release data-raw" in workflow and "gh release upload \"$STATE_RELEASE\" state.tar.gz" in workflow
    assert "compute_recs.py --raw raw" not in workflow  # расчёт с нуля по всему сырью — только в проверках (checks.yml)
