"""Запасные окна ручного ввода (п. 6.1 и 6.4 ТЗ), если экран не распознался:
- выбор врагов (Ctrl+Alt+P): поиск по первым буквам и сетка портретов, выбор за один клик;
- «вижу у врага предмет» (Ctrl+Alt+I): герой + поиск предмета.
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout,
)

from app.i18n import ru
from app.threats.data import DataCache, GameData

MAX_ENEMIES = 5
PORTRAIT_SIZE = QSize(96, 54)
ITEM_ICON_SIZE = QSize(44, 32)


def _icon(cache: DataCache, kind: str, key: str) -> QIcon:
    path = cache.root / "images" / kind / f"{key}.png"
    return QIcon(QPixmap(str(path))) if path.is_file() else QIcon()


def _matches(query: str, name: str) -> bool:
    """«blo» → Bloodseeker; поиск по началу любого слова."""
    query = query.strip().lower()
    return not query or any(word.startswith(query) for word in name.lower().replace("-", " ").split()) \
        or name.lower().startswith(query)


class _TopDialog(QDialog):
    def __init__(self, title: str):
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setWindowTitle(title)


class HeroPicker(_TopDialog):
    """Выбор до 5 врагов. selected() — ключи героев."""

    def __init__(self, data: GameData, cache: DataCache, current: list[str]):
        super().__init__(ru.PICKER_HEROES_TITLE)
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText=ru.PICKER_SEARCH)
        layout.addWidget(self.search)
        self.grid = QListWidget(viewMode=QListWidget.IconMode, iconSize=PORTRAIT_SIZE, movement=QListWidget.Static,
                                resizeMode=QListWidget.Adjust, spacing=4, wordWrap=True)
        self.grid.setSelectionMode(QListWidget.MultiSelection)
        for hero in sorted(data.heroes.values(), key=lambda h: h.localized):
            item = QListWidgetItem(_icon(cache, "heroes", hero.name), hero.localized)
            item.setData(Qt.UserRole, hero.name)
            self.grid.addItem(item)
            item.setSelected(hero.name in current)
        layout.addWidget(self.grid)
        self.counter = QLabel()
        layout.addWidget(self.counter)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.grid.itemSelectionChanged.connect(self._limit)
        self._limit()
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            item.setHidden(not _matches(text, item.text()))

    def _limit(self) -> None:
        chosen = self.grid.selectedItems()
        if len(chosen) > MAX_ENEMIES:
            chosen[-1].setSelected(False)
            chosen = chosen[:MAX_ENEMIES]
        self.counter.setText(ru.PICKER_CHOSEN.format(n=len(chosen), names=", ".join(i.text() for i in chosen)))

    def selected(self) -> list[str]:
        return [i.data(Qt.UserRole) for i in self.grid.selectedItems()][:MAX_ENEMIES]


class ItemPicker(_TopDialog):
    """«Вижу у врага предмет»: герой из списка врагов и предмет с поиском."""

    def __init__(self, data: GameData, cache: DataCache, enemies: list[str], items: list[str]):
        super().__init__(ru.PICKER_ITEM_TITLE)
        self.resize(420, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(ru.PICKER_ENEMY))
        self.hero = QComboBox()
        for key in enemies:
            hero = data.hero_by_name(key)
            self.hero.addItem(_icon(cache, "heroes", key), hero.localized if hero else key, key)
        row.addWidget(self.hero, 1)
        layout.addLayout(row)
        self.search = QLineEdit(placeholderText=ru.PICKER_SEARCH)
        layout.addWidget(self.search)
        self.list = QListWidget(iconSize=ITEM_ICON_SIZE)
        for key in sorted(items, key=lambda k: data.items[k].dname if k in data.items else k):
            name = data.items[key].dname if key in data.items else key
            entry = QListWidgetItem(_icon(cache, "items", key), name)
            entry.setData(Qt.UserRole, key)
            self.list.addItem(entry)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hidden = not _matches(text, item.text())
            item.setHidden(hidden)
            if not hidden and first is None:
                first = item
        if first is not None:
            self.list.setCurrentItem(first)

    def selected(self) -> tuple[str | None, str | None]:
        item = self.list.currentItem()
        return self.hero.currentData(), (item.data(Qt.UserRole) if item else None)
