"""Тесты сборщика статистики на синтетических матчах (без сети)."""
from tools.collect_stats import aggregate, build_stats, empty_state, match_rows


class FakeMaps:
    item_by_id = {1: "spirit_vessel", 2: "monkey_king_bar", 3: "heart", 4: "tango"}
    answers = {"spirit_vessel", "monkey_king_bar"}
    enemy_items = {"heart"}
    hero_traits = {10: ["heal"], 11: []}


def match(radiant_win=True, mode=22, duration=2000, leaver=0):
    players = []
    for slot in range(10):
        radiant = slot < 5
        hero = [1, 2, 3, 4, 5][slot] if radiant else [10, 11, 12, 13, 14][slot - 5]
        items = {"item_0": 1, "item_1": 4} if hero == 1 else ({"item_0": 3} if hero == 10 else {})
        players.append({"hero_id": hero, "player_slot": slot if radiant else 128 + slot - 5,
                        "leaver_status": leaver if slot == 0 else 0, "account_id": 123, **items})
    return {"game_mode": mode, "duration": duration, "radiant_win": radiant_win, "players": players}


def test_filters():
    assert match_rows(match(mode=18))[1] is None  # другой режим
    assert match_rows(match(duration=300))[1] is None  # короткий
    assert match_rows(match(leaver=2))[1] is None  # ливер
    assert match_rows(match(mode=23))[0] == "turbo"


def test_aggregate_and_stats():
    state = empty_state("7.41")
    for win in (True, True, False):
        aggregate(match(radiant_win=win), state, FakeMaps())
    assert state["matches"] == {"normal": 3}
    assert state["base"]["normal"]["1"]["spirit_vessel"] == [3, 2]
    assert state["vs_hero"]["normal"]["1"]["10"]["spirit_vessel"] == [3, 2]
    assert state["vs_trait"]["normal"]["1"]["heal"]["spirit_vessel"] == [3, 2]
    assert state["vs_item"]["normal"]["1"]["heart"]["spirit_vessel"] == [3, 2]
    assert "tango" not in state["base"]["normal"]["1"]  # не предмет-ответ
    stats = build_stats(state, min_games=3)
    assert stats["base"]["normal"]["1"]["spirit_vessel"] == [3, 2]
    assert build_stats(state, min_games=4)["base"] == {}
    assert "123" not in str(stats)  # аккаунты не сохраняются
