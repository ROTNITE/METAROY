MetaRescue — готовый автономный 2D-комплект

Распакуйте в новую папку. Не поверх основного Unreal-проекта.
В PowerShell из папки с controller, environment, experiments и requirements.txt:

python -m pip install -r requirements.txt
python -m experiments.mission_flow.auto_information --help
python -m experiments.mission_flow.auto_information --output research/information-001 --rounds 40 --patience 8 --proposals 3 --screen 24 --validate 64 --final 128 --workers 4

Либо запустите Start-Research.cmd после установки зависимости.
Повтор той же команды продолжает сохранённую кампанию.

Состав: Net65-light 2026-11-27 + Global Dispatch + Bottleneck + Resource Predictor + Adaptive Survey + Repeat Value (включает AutoResearch). Все исходные установщики отработали с проверкой SHA-256.
Штатный режим контроллера не изменён. AutoResearch использует Adaptive Survey (range-late) как исходную базу, а не Repeat Value. Поиск ограничен уже реализованными гипотезами, автоматически не изменяет производственный контроллер.
Исследование — 2D. Unreal Engine, готовая сборка и 3D-ассеты отсутствуют.
Проверено в Linux/Python 3.13.5: 12 целевых тестов, --help, один короткий цикл (8 парных запусков по четырём профилям), повторный запуск с тем же каталогом. Windows/Python 3.14 не проверялись. Полная кампания из 40 раундов не выполнялась для этой сборки.
