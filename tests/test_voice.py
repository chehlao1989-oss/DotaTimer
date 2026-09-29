"""Тесты голоса: генерация при первом запуске и выбор файла (пользовательский важнее)."""
from app.notify import voice_gen
from app.notify.voice import find_voice_file


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, rate):
        self.text = text
        FakeCommunicate.calls.append(text)

    async def save(self, path):
        with open(path, "wb") as f:
            f.write(b"mp3")


def test_generate_missing_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_gen, "cache_dir", lambda: tmp_path)
    FakeCommunicate.calls = []
    phrases = {"rune": "Руна!", "bounty": "Баунти"}
    assert voice_gen.generate_missing(phrases, FakeCommunicate) == 2
    assert FakeCommunicate.calls == ["Руна!", "Баунти."]  # точка добавляется, иначе сервис иногда молчит
    assert voice_gen.missing_phrases(phrases) == []
    assert voice_gen.generate_missing(phrases, FakeCommunicate) == 0


def test_no_internet_is_not_fatal(tmp_path, monkeypatch):
    class Broken(FakeCommunicate):
        async def save(self, path):
            raise OSError("нет сети")

    monkeypatch.setattr(voice_gen, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(voice_gen.asyncio, "sleep", lambda s: FakeCommunicate("", "", "").save(tmp_path / "x"))
    assert voice_gen.generate_missing({"rune": "Руна!"}, Broken) == 0
    assert voice_gen.missing_phrases({"rune": "Руна!"}) == ["rune"]


def test_user_file_overrides_generated(tmp_path):
    user = tmp_path / "user"
    generated = tmp_path / "gen"
    user.mkdir()
    generated.mkdir()
    (generated / "rune.mp3").write_bytes(b"1")
    assert find_voice_file("rune", user, generated) == generated / "rune.mp3"
    (user / "rune.wav").write_bytes(b"2")
    assert find_voice_file("rune", user, generated) == user / "rune.wav"
    assert find_voice_file("nope", user, generated) is None
