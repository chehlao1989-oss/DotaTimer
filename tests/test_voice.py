"""Тесты голоса: фразы в программе, генерация недостающих и выбор файла (пользовательский важнее)."""
from app.i18n import ru
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
    monkeypatch.setattr(voice_gen, "BUNDLED_DIR", tmp_path / "bundle")  # фраз в программе нет
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


def test_bundle_matches_texts():
    """Все фразы лежат в программе и записаны нынешним текстом из ru.py (DECISIONS №29, BUGLOG №50).
    Упал — текст фразы поменяли: перезаписать фразы командой python tools/gen_voice.py."""
    assert voice_gen.bundled_phrases() == ru.VOICE_PHRASES
    assert [k for k, t in ru.VOICE_PHRASES.items() if voice_gen.bundled_file(k, t) is None] == []
    assert voice_gen.missing_phrases() == []  # генерировать у игрока нечего — Microsoft не нужен


def test_bundled_file_preferred_unless_text_changed(tmp_path, monkeypatch):
    """Фраза из программы важнее сгенерированной; с устаревшим текстом — берётся сгенерированная."""
    bundled, generated, user = tmp_path / "b", tmp_path / "g", tmp_path / "u"
    for folder in (bundled, generated, user):
        folder.mkdir()
    monkeypatch.setattr(voice_gen, "cache_dir", lambda: generated)
    voice_gen.build_bundle({"threat": ru.VOICE_PHRASES["threat"]}, bundled, FakeCommunicate)
    (generated / "threat.mp3").write_bytes(b"1")
    assert find_voice_file("threat", user, generated, bundled) == bundled / "threat.mp3"
    voice_gen.build_bundle({"threat": "старый текст"}, bundled, FakeCommunicate)
    assert find_voice_file("threat", user, generated, bundled) == generated / "threat.mp3"
    assert voice_gen.missing_phrases({"threat": "новый"}, bundled) == []  # сгенерированная есть

