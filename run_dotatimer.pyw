"""Запуск без консольного окна (для автозапуска с Windows): pythonw run_dotatimer.pyw"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

if "--child" in sys.argv:  # программа, запущенная сторожем
    from app.main import main  # noqa: E402
    main()
else:  # сторож: перезапускает программу, если она умерла посреди катки (BUGLOG №57)
    from app.supervisor import supervise  # noqa: E402
    sys.exit(supervise(sys.argv[1:]))
