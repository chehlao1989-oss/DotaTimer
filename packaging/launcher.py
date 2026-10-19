"""Точка входа собранной программы (PyInstaller, packaging/DotaTimer.spec).

Без флагов — сторож (app/supervisor.py): запускает этот же exe с --child и перезапускает, если программа умерла
посреди катки (BUGLOG №57). С --child или --selftest — сама программа.
"""
import sys

if "--child" in sys.argv or "--selftest" in sys.argv:
    from app.main import main
    main()
else:
    from app.supervisor import supervise
    sys.exit(supervise(sys.argv[1:]))
