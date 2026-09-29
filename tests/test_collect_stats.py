"""Тесты сборщика статистики на синтетических матчах (без сети)."""
from tools.collect_stats import aggregate, build_stats, empty_state, farm_ranks, match_rows


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
        net_worth = {1: 20000, 2: 15000, 3: 9000, 4: 5000, 5: 4000}.get(hero, 10000 - slot * 100)
        players.append({"hero_id": hero, "player_slot": slot if radiant else 128 + slot - 5, "net_worth": net_worth,
                        "leaver_status": leaver if slot == 0 else 0, "account_id": 123, **items})
    return {"game_mode": mode, "duration": duration, "radiant_win": radiant_win, "players": players}


def test_filters():
    assert match_rows(match(mode=18))[1] is None  # другой режим
    assert match_rows(match(duration=300))[1] is None  # короткий
    assert match_rows(match(leaver=2))[1] is None  # ливер
    assert match_rows(match(mode=23))[0] == "turbo"


def test_farm_ranks():
    _, rows = match_rows(match())
    ranks = farm_ranks(rows)
    assert ranks[:5] == [1, 2, 3, 4, 5]  # у Света по убыванию нетворса
    assert sorted(ranks[5:]) == [1, 2, 3, 4, 5]


def test_aggregate_and_stats():
    state = empty_state("7.41")
    for win, duration in ((True, 2000), (True, 2700), (False, 1500)):
        aggregate(match(radiant_win=win, duration=duration), state, FakeMaps())
    assert state["matches"] == {"normal": 3}
    assert state["hero"]["normal"]["1"] == [3, 2]
    assert state["pair"]["normal"]["1"]["10"] == [3, 2]  # знаменатель для частоты покупки
    assert state["trait_games"]["normal"]["1"]["heal"] == [3, 2]
    assert state["item_games"]["normal"]["1"]["heart"] == [3, 2]
    assert state["base"]["normal"]["1"]["spirit_vessel"] == [3, 2]
    assert state["vs_hero"]["normal"]["1"]["10"]["spirit_vessel"] == [3, 2]
    assert state["length"]["normal"]["1"] == {"long": [1, 1], "short": [1, 0]}  # 45 мин и 25 мин; 33 мин не считается
    assert state["farm"]["normal"]["1"] == [3, 3]  # всегда самый богатый: сумма мест 1+1+1
    assert "tango" not in state["base"]["normal"]["1"]  # не предмет-ответ
    stats = build_stats(state, min_games=3)
    assert stats["base"]["normal"]["1"]["spirit_vessel"] == [3, 2]
    assert stats["farm"]["normal"]["1"] == [3, 3]
    assert build_stats(state, min_games=4)["base"] == {}
    assert "123" not in str(stats)  # аккаунты не сохраняются
