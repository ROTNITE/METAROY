# MetaEvolution Rescue

Переносимый метасценарный контроллер спасательного роя и независимая A2X-среда.

Активная алгоритмическая база — **v0.5.2**. В тестовой 3D-миссии
отдельный Python-контроллер использует два NSGA-II и управляет физическими
агентами Unreal через Core Protocol 1.0. Проверены обнаружение, погрузка,
перевозка и передача трёх пострадавших. Динамические эффекты v3 прошли отдельную
физическую приёмку. RescueQuarter и конкурсный комплект находятся в разработке.
Текущий статус и исходы проверок собраны в [журнале](docs/quarter-v052-execution.md)
и [матрице приёмки](docs/quarter-v052-acceptance.json).

## Начать отсюда

### Получение проекта и подготовка Windows

Установите Git и Git LFS, затем клонируйте репозиторий с LFS-объектами:

```powershell
git lfs install
git clone https://github.com/ROTNITE/METAROY.git
cd METAROY
git lfs pull
if (-not (Test-Path 'benchmarks/MetaRescue_Benchmark_v0_5_2/secret.env')) { Set-Content -Encoding ascii 'benchmarks/MetaRescue_Benchmark_v0_5_2/secret.env' 'API=' }
python -m pip install -r requirements.txt
```

Файлы моделей и карты хранятся в Git LFS: скачивание ZIP через интерфейс GitHub
может содержать указатели вместо самих файлов. Для 3D нужны Python 3.10+,
Unreal Engine 5.8.4, Visual Studio C++ и Windows SDK. Если движок установлен
не в `C:\Program Files\Epic Games\UE_5.8`, задайте переменную
`MR_UNREAL_ENGINE`. Для результатов на компьютере без диска D перед запуском
задайте, например, `$env:METARESCUE_OUTPUT = "$env:USERPROFILE\MetaRescue-output"`.
Движок, локальные кэши и инструменты сборки в репозиторий не входят.
Команда создаёт пустой локальный шаблон `secret.env` для тестов бенчмарка,
если его ещё нет; этот файл исключён из Git, чтобы не публиковать ключи.

- [Единые инструкции и краткий контекст проекта](AGENTS.md).
- [Точка продолжения v0.5.2](docs/quarter-v052-handoff.md).
- [Динамика и физические проверки Core](docs/core-events.md).
- [Действующая общая концепция и изменения](docs/current-concept.md).
- [Требования v0.3: динамика, вероятности и поздние цели](docs/benchmark-v0.3-spec.md).
- [Проверка исходников и ограничений v0.2](docs/benchmark-v0.2-review.md).
- [Проверка динамической v0.3 и воспроизведённые дефекты](docs/benchmark-v0.3-review.md).
- [Программа исследований и бенчмарков](docs/benchmark-research-plan.md).
- [Оригиналы контекста, архив и карта требований](docs/sources/README.md).
- [3D-визуальная концепция](docs/3d-visual-direction.md).
- [Запуск Unreal-сцены](unreal/README.md).
- [Текущее окружение и проверки](docs/3d-world-status.md).
- [Осмотр всех пользовательских 3D-моделей](docs/3d-asset-library-review.md).
- [Скачанные деревья и древесные обломки: 12 наборов BLEND/FBX, CC0](blender/vegetation/README.md) · [галерея](blender/vegetation/index.html). Доступны локально; при плановом аудите окружения сверить с игровыми импортами и размещением, подходящие недостающие ассеты добавить после подготовки.

Запуск пользовательской базы v0.5.2 из корня проекта

```powershell
python -B -X utf8 "benchmarks/MetaRescue_Benchmark_v0_5_2/main.py"
python -B -X utf8 -m unittest discover -s "benchmarks/MetaRescue_Benchmark_v0_5_2/main part/tests" -v
```

v0.5.2 запускается на Python 3.10+ без внешних пакетов. Исторические v0.2 и v0.3
хранятся в соседних папках. `benchmark/` содержит ранний черновик.

## Физическая миссия из двух процессов

Для разработки нужны Unreal 5.8.4, Visual Studio с C++/Windows SDK и Python 3.10+.
Закройте редактор перед запуском. Runner запускает нативную среду и отдельный
контроллер, затем проверяет архив в `D:/MetaRescue-output`.

```powershell
python -m pip install -r requirements.txt
python -B -X utf8 tools/run_mission.py --build --policy nsga2_nsga2 --ai-mode MOCK --visible
```

`--config` принимает JSON с настройками из `environment/config.py`.
`--layout` принимает сохранённую приватную конфигурацию. Режим `MOCK` выполняет
обе линии AI без сетевых запросов. В `OFF` работает алгоритмический контроллер.
Пояснения о LIVE, replay и учёте вызовов находятся в [документации AI](docs/ai-runtime.md).

## Локальная лаборатория M5 (этап 3.2)

```powershell
python -m pip install -r requirements.txt
python -B -X utf8 -m tools.m5_lab --output ./m5-results
```

Откройте http://127.0.0.1:8766/. Выберите контроллер, модель среды,
режим Strict/Declared и seed; подтвердите запуск. Для начальной проверки
подходят независимая 3D-модель A/K и планарная модель без Unreal.
Внешние уже запущенные доверенные loopback-сервисы регистрируются
аргументами `--environment NAME=PORT` и `--controller NAME=PORT`.
Автозапуск чужих архивов из браузера и нативная приёмка пока не входят
в подтверждённый объём UI. Подробности и ограничения: [лаборатория M5](docs/m5-interface.md).

## Документы

- [Net65, демонстрация и двусторонняя проверка](docs/net65-runtime.md).

- [Концепция](концепция/КОНЦЕПЦИЯ_MetaEvolution_Rescue_v1.0.md).
- [Этап 1: действующий контракт](docs/stage-1-interaction.md).
- [Проверка и решение о принятии](docs/stage-1-review.md).
- [Машиночитаемые эталоны](contracts/examples/interaction-v1.json).

Core Protocol сохраняет официальную версию 1.0. Редакция документа 1.1 не меняет эту версию. Неоднозначности организаторского ТЗ перечислены в контракте и не считаются официально разрешёнными.

## Проверка эталонов

Из корня проекта:

    python -B -X utf8 -m unittest discover -s tests -v

Контракт использует `jsonschema==4.26.0`. Физические проверки и нативные HTTP
эпизоды запускаются отдельными командами из [документации Unreal](unreal/README.md)
и [журнала выполнения](docs/quarter-v052-execution.md).

### Опциональный облегчённый рендер Unreal

Для обычной интерактивной работы доступен профиль `economy` с ограничением
частоты рендера (при необходимости сначала выполнить стандартную сборку):

```powershell
python -B -X utf8 -m tools.run_product --render-profile economy --fps-cap 45
```

Также доступен `balanced`. Без аргументов остаётся прежняя конфигурация;
`--demo-render` сохранён и не совмещается с новым профилем. FPS cap
не используется для зачётного измерения максимального FPS. Реальный GPU/FPS
на конкретной машине ещё требует нативного замера. См.
[аудит производительности и незакрытых требований](docs/stage3-performance-and-open-audit.md).
