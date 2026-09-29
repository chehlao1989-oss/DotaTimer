"""Запуск без консольного окна (для автозапуска с Windows): pythonw run_dotatimer.pyw"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app.main import main  # noqa: E402

main()
