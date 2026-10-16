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



class FakeOutput:
    def __init__(self):
        self.device = None

    def setDevice(self, device):
        self.device = device


def test_voice_follows_default_device(qapp, monkeypatch):
    """Устройство по умолчанию сменилось после запуска (колонка, звук монитора) — фразы идут туда же,
    куда Дота, а не в старое устройство (BUGLOG №52)."""
    import app.notify.voice as voice_mod
    player = voice_mod.VoicePlayer()
    outputs = {"rune": FakeOutput(), "bounty": FakeOutput()}
    player._outputs = outputs
    device = ["Realtek"]
    monkeypatch.setattr(voice_mod, "default_output", lambda: FakeDevice(device[0]))
    player.follow_default_device()
    device[0] = "VK Capsula"
    player.follow_default_device()
    assert [o.device.name for o in outputs.values()] == ["VK Capsula", "VK Capsula"]


class FakeDevice:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, FakeDevice) and other.name == self.name

    def description(self):
        return self.name


def test_stuck_phrase_does_not_block_queue(qapp, monkeypatch):
    """Фраза «играет» дольше PHRASE_MAX_SEC — зависла: следующая не ждёт её вечно (BUGLOG №52)."""
    import app.notify.voice as voice_mod
    from PySide6.QtMultimedia import QMediaPlayer

    class Stuck:
        stopped = False

        def playbackState(self):
            return QMediaPlayer.PlayingState

        def stop(self):
            Stuck.stopped = True

    player = voice_mod.VoicePlayer()
    player._current, player._started_at = Stuck(), 100.0
    monkeypatch.setattr(voice_mod.time, "monotonic", lambda: 101.0)
    assert player.is_playing()
    monkeypatch.setattr(voice_mod.time, "monotonic", lambda: 100.0 + voice_mod.PHRASE_MAX_SEC + 1)
    assert not player.is_playing() and Stuck.stopped
