"""Все строки интерфейса и сообщений на русском.

В остальном коде русских строк для пользователя нет, только ссылки сюда.
"""

def format_clock(seconds: int) -> str:
    """Игровое время в виде 6:00."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


# --- Уведомления таймеров (текст на оверлее) ---
MSG_FIRST_POWER_RUNE_EARLY = "Руна силы на {time}, подтягивайтесь к реке"
MSG_POWER_RUNE = "Руна силы через {sec} сек"
MSG_NIGHT = "Ночь через {sec} сек"
MSG_DAY = "Рассвет через {sec} сек"
MSG_TORMENTOR = "Терзатель появился {side}"
TORMENTOR_SIDES = {
    "radiant_bottom_right": "у Света (внизу справа)",
    "dire_top_left": "у Тьмы (вверху слева)",
}
MSG_NEUTRAL_TIER = "Нейтралки: открыт тир {tier}"
MSG_SIMPLE = {
    "bounty_rune": "Баунти через {sec} сек",
    "water_rune": "Водные руны через {sec} сек",
    "shrine_of_wisdom": "Святилище мудрости через {sec} сек",
    "lotus": "Лотос через {sec} сек",
    "siege_creeps": "Осадные крипы через {sec} сек",
}
VOICE_KEY_BY_EVENT = {
    "bounty_rune": "bounty",
    "water_rune": "water",
    "shrine_of_wisdom": "shrine",
    "lotus": "lotus",
    "siege_creeps": "siege",
}

# --- Оверлей ---
OVERLAY_LAYOUT_HINT = (
    "Настройка расположения\n"
    "Тащите мышью · колесо: размер · Ctrl+колесо: прозрачность\n"
    "Правый клик: готово"
)
OVERLAY_DEMO = [
    ("Руна силы на 6:00, подтягивайтесь к реке", True),
    ("Баунти через 10 сек\nВодные руны через 10 сек", False),
    ("Терзатель появился у Света (внизу справа)", False),
    ("Ночь через 15 сек\nОсадные крипы через 10 сек", False),
]

# --- Угрозы и предметы ---
RANK_NAMES = {1: "Рекрут", 2: "Страж", 3: "Рыцарь", 4: "Герой", 5: "Легенда", 6: "Властелин", 7: "Божество", 8: "Титан"}
RANK_UNKNOWN = "всех рангах"
CARD_TITLE = "Главные угрозы"
CARD_REASON = {
    "winrate": "винрейт {wr:.0f}% на {rank}",
    "matchup": "контрит вашего героя",
    "core": "главный фармящий у врага",
}
CARD_LINE = "{hero} — {reason}"
CARD_COUNTERS = "   → {items}"
HINT_ITEM = "У {hero} появился {item}\n→ {counters}"
HINT_BUILDING = "{hero} собирает {item} (есть {component})\n→ {counters}"
HINT_NO_COUNTERS = "подходящего ответа на вашем герое не нашлось"
SUGGEST_VS_HERO = "{item} ({delta:+.0f}% против {target})"
SUGGEST_VS_TRAIT = "{item} ({delta:+.0f}% против «{rule}»)"
SUGGEST_PLAIN = "{item}"
SUGGEST_NO_STATS = " (статистики пока нет)"
HINT_DEAD_REMINDER = "Кликни по врагам вверху — посмотрю их предметы"
TOPBAR_PARTIAL = "Не всех врагов узнал по экрану. Ctrl+Alt+P — выбрать вручную"
TOPBAR_FAILED = "Не удалось узнать врагов по экрану. Ctrl+Alt+P — выбрать вручную"

# --- Окна ручного ввода ---
PICKER_HEROES_TITLE = "Враги в этой катке"
PICKER_ITEM_TITLE = "Вижу у врага предмет"
PICKER_SEARCH = "Поиск: первые буквы, например blo"
PICKER_CHOSEN = "Выбрано {n} из 5: {names}"
PICKER_ENEMY = "Враг:"
PICKER_NO_ENEMIES = "Сначала выберите врагов (Ctrl+Alt+P)"

# --- Вкладка «Угрозы» ---
TAB_THREATS = "Угрозы"
CHECK_THREATS_ENABLED = "Подсказывать угрозы и контр-предметы"
LABEL_THREAT_COUNT = "Сколько главных угроз показывать:"
LABEL_RANK = "Ваш ранг:"
RANK_NONE = "Нет ранга (статистика по всем)"
LABEL_STATS = "Статистика предметов:"
STATS_STATUS = "патч {patch}, матчей: {normal} обычных, {turbo} турбо; скачано {when}"
STATS_MISSING = "ещё не скачана (нужен интернет) — советы только по механике"
BUTTON_PICK_HEROES = "Выбрать врагов вручную (Ctrl+Alt+P)"
BUTTON_SEEN_ITEM = "Вижу у врага предмет (Ctrl+Alt+I)"
BUTTON_SHOW_CARD = "Скрыть / показать полоску угроз (Ctrl+Alt+H)"
THREATS_HINT = (
    "Враги узнаются сами по верхней панели в начале катки. Предметы — когда вы кликаете "
    "по портрету врага вверху. Кнопки ниже — запасной вариант, если что-то не распозналось."
)
CARD_NONE = "Врагов пока нет: они появятся после начала катки или выберите вручную (Ctrl+Alt+P)"

# --- Главное окно и трей ---
APP_TITLE = "Dota таймер"
STATUS_WAITING = "Ожидание игры…"
STATUS_CONNECTED = "Dota 2 подключена ✓"
STATUS_MUTED = " (уведомления на паузе)"
STATUS_WAITING_HINT = (
    "Данные приходят только во время катки. Если катка идёт, а статус не меняется: перезапустите Доту; "
    "если не помогло, добавьте в Steam в параметры запуска Доты -gamestateintegration, не стирая остальные."
)
TAB_TIMERS = "Таймеры"
TAB_VOICE_SCREEN = "Голос и экран"
TAB_HOTKEYS = "Горячие клавиши"
TAB_GENERAL = "Общие"
TAB_ABOUT = "О программе"
LABEL_MODE = "Режим игры:"
MODE_NORMAL = "Обычный"
MODE_TURBO = "Турбо"
MODE_HINT = "Режим определяется сам к 0:30 катки по пассивному золоту. Можно выбрать и вручную."
LABEL_PRESET = "Пресет:"
PRESETS = {"minimum": "Минимум", "standard": "Стандарт", "all": "Всё"}
GROUP_EVENTS = "События (галочка — включено, число — за сколько секунд предупреждать)"
EVENT_TITLES = {
    "power_rune": "Руны силы (активки)",
    "bounty_rune": "Баунти",
    "water_rune": "Водные руны",
    "shrine_of_wisdom": "Святилища мудрости",
    "lotus": "Лотосы",
    "tormentor_spawn": "Терзатель: первое появление",
    "tormentor_respawn": "Терзатель: повторное появление",
    "day_night": "День и ночь",
    "siege_creeps": "Осадные крипы",
    "neutral_tier": "Нейтралки: новый тир",
}
CHECK_FIRST_POWER_RUNE = "Первая руна силы (6:00): предупреждать за 30 и за 10 сек"
CHECK_EVERY_POWER_RUNE_30 = "Предупреждать за 30 сек о каждой руне силы"
SUFFIX_SEC = " сек"
LABEL_VOLUME = "Громкость голоса:"
BUTTON_TEST_VOICE = "Проверить голос"
BUTTON_LAYOUT = "Настроить расположение надписей"
BUTTON_DEMO = "Показать пробные надписи"
LAYOUT_HINT_WINDOW = "Надписи можно перетащить мышью. Правый клик по ним — готово."
CHECK_AUTOSTART = "Запускать вместе с Windows (программа тихо ждёт в трее)"
AUTOSTART_ERROR = "Не удалось изменить автозапуск: {error}"
ABOUT_TEXT = (
    "Dota таймер — помощник для Dota 2.\n\n"
    "Работает только на разрешённых данных: Game State Integration (официальный механизм Valve), "
    "то, что видно на экране, и ручной ввод. Память игры не читает, клавиши не нажимает.\n\n"
    "Настройки и логи: %APPDATA%\\DotaTimer\\"
)
HOTKEY_TITLES = {
    "tormentor_killed": "Терзатель убит",
    "silence": "Тишина на 1 минуту",
    "show_threats": "Скрыть / показать полоску угроз",
    "seen_item": "Вижу у врага предмет",
    "pick_heroes": "Выбрать врагов вручную",
}
HOTKEYS_HINT = "Кликните в поле и нажмите новое сочетание. Нужен хотя бы один из Ctrl, Alt, Shift."
BUTTON_RESET_HOTKEYS = "Вернуть клавиши по умолчанию"
HOTKEY_INVALID = "Сочетание {combo} не подходит. Нужны Ctrl/Alt/Shift и буква, цифра или F1–F24."
HOTKEY_FEEDBACK = {
    "tormentor_killed": "Таймер терзателя запущен",
    "silence": "Тишина на 1 минуту",
}
HOTKEY_NO_MATCH = "Матч ещё не идёт"
TRAY_OPEN = "Открыть"
TRAY_PAUSE = "Пауза уведомлений"
TRAY_EXIT = "Выход"
TRAY_STILL_RUNNING = "Программа работает в трее. Выход — правый клик по иконке."

# --- Проверки при запуске ---
CHECK_FULLSCREEN = (
    "Программа работает только в режиме «В окне без рамки».\n\n"
    "Сейчас в Доте стоит полноэкранный режим, и надписи поверх игры не будут видны.\n"
    "Настройки → Видео → Режим отображения."
)
CHECK_SCREEN_REMINDER = (
    "Проверьте, что в Доте стоит режим «В окне без рамки» (Настройки → Видео → Режим отображения). "
    "В полноэкранном режиме надписи поверх игры не видны."
)
BUTTON_OK = "Понятно"
BUTTON_DONT_SHOW = "Больше не показывать"
CHECK_DOTA_NOT_FOUND = (
    "Не нашёл папку Dota 2 автоматически.\n\n"
    "Укажите её вручную: обычно это ...\\steamapps\\common\\dota 2 beta"
)
CHECK_CHOOSE_DOTA = "Папка Dota 2 (dota 2 beta)"
CHECK_WRONG_FOLDER = "В этой папке нет Dota 2. Нужна папка «dota 2 beta»."
CHECK_CFG_WRITE_ERROR = "Не удалось записать файл настроек для Доты:\n{error}\n\nПопробуйте запустить программу от имени администратора."
CHECK_RESTART_DOTA = "Программа настроила связь с Дотой. Перезапустите Доту, чтобы она начала присылать данные."

# --- Угрозы по нашим матчам: причины ---
THREAT_ROLE = {"carry": "Керри", "core": "Кор", "support": "Саппорт"}
THREAT_LATE = "силён в долгих играх ({wr:.0f}% после 40 мин)"
THREAT_MATCHUP = "ваш герой проигрывает ему ({wr:.0f}%)"
MECHANIC_REASONS = {
    "heal_reduction": "режет лечение",
    "true_strike": "пробивает уклонение",
    "true_sight": "видит невидимых",
    "multi_target": "бьёт по нескольким целям",
    "illusion_kill": "убивает иллюзии",
    "break": "отключает пассивки",
    "bkb_pierce": "работает сквозь BKB",
    "silence": "немота — не убежит",
    "root": "корни — не убежит",
    "hex": "хекс — не убежит",
    "magic_defense": "защита от магии",
    "physical_defense": "защита от физического урона",
    "dispel_enemy": "снимает с врага спасающие эффекты",
    "mana_burn": "жжёт ману",
    "cheap_target": "сбивает Linken's Sphere",
    "self_dispel": "снимает с вас контроль",
    "armor_reduction": "снижает броню",
}
LOW_DATA = "мало данных"

# --- Полоска угроз ---
STRIP_BOUGHT = "есть ✓"
CARD_BOUGHT = "{item} ✓"
STRIP_DELTA = "{delta:+.0f}%"
DELTA_ZERO = "±0%"
CARD_ITEM_DELTA = "{item} ({delta})"
CARD_ITEM_VS = "{item} ({delta} против {target})"
STRIP_LAYOUT = "Полоска угроз"
BUTTON_STRIP_LAYOUT = "Настроить полоску угроз"

# --- Режим ---
MODE_NAMES = {"normal": "Обычный", "turbo": "Турбо"}
MODE_ANNOUNCE = "Режим: {mode}"
MODE_AUTO_SWITCHED = "Режим: {mode} (определён автоматически)"

# --- Главная программа ---
APP_PORT_BUSY_TITLE = "Dota таймер"
APP_PORT_BUSY = "Программа уже запущена (или порт {port} занят другой программой)."

# --- Голосовые фразы (ключ → текст для генерации WAV, утверждаются с автором) ---
# Ударение можно поправить знаком U+0301 после ударной гласной.
VOICE_PHRASES = {
    "power_rune_30": "Активка через тридцать",
    "power_rune": "Ру́на!",  # знак ударения над «у»: без него голос говорит «рунА»
    "bounty": "Баунти",
    "water": "Вода",
    "shrine": "Мудрость",
    "lotus": "Лотос",
    "siege": "Катапульты",
    "night": "Скоро ночь",
    "day": "Рассвет",
    "tormentor": "Терзатель",
    "neutral": "Нейтралки",
    "threat": "Угроза",
    "item_hint": "Совет по предметам",
}

# --- GSI-рекордер (tools/gsi_recorder.py) ---
REC_CFG_INSTALLED = "GSI-конфиг установлен: {path}\nЕсли Dota 2 уже запущена, перезапустите её, иначе она не увидит конфиг."
REC_CFG_UP_TO_DATE = "GSI-конфиг уже на месте: {path}"
REC_CFG_DOTA_NOT_FOUND = (
    "Не нашёл папку Dota 2 автоматически.\n"
    "Скопируйте файл {cfg} вручную в папку\n"
    "  ...\\steamapps\\common\\dota 2 beta\\game\\dota\\cfg\\gamestate_integration\\"
)
REC_CFG_WRITE_ERROR = "Не получилось записать GSI-конфиг: {error}"
REC_PORT_BUSY = "Порт {port} занят. Возможно, рекордер уже запущен в другом окне. Закройте его и попробуйте снова."
REC_STARTED = "Рекордер запущен, жду данные от Dota 2 на {url}\nЗапись идёт в файл: {path}\nОстановить: Ctrl+C (или красный квадрат «Stop» в PyCharm)."
REC_WAITING = "Пока ни одного пакета от Dota 2. Запустите игру (или перезапустите, если она уже была открыта)."
REC_STATUS = "Пакетов: {packets} | время матча: {clock} | состояние: {state} | герой: {hero} | последний пакет {ago:.0f} сек назад"
REC_REJECTED = "Отклонено пакетов с чужим токеном: {count}"
REC_STOPPED = "Запись остановлена. Сохранено пакетов: {packets}\nФайл: {path}"

# --- Разбор записи (tools/analyze_recording.py) ---
AN_NO_FILES = "Записей не найдено в {path}"
AN_DONE = "Отчёт сохранён: {path}"

# --- Проигрывание записи (tools/gsi_replay.py) ---
RP_START = "Проигрываю {path} на {url}, скорость x{speed}"
RP_DONE = "Готово, отправлено пакетов: {count}"
RP_SEND_ERROR = "Не удалось отправить пакет: {error}"
