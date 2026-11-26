# Net65 в MetaRescue

Net65 выбирает следующий повторный осмотр после завершения первичного покрытия и 65% горизонта. Планировщик использует время последних подтверждённых осмотров, оценки движения и расхода, доступность разведчиков и резерв на возврат. Штатные варианты первичного поиска, эвакуации и координации сценариев доступны через прежние параметры. Управление патрулём задаёт `--scout-patrol core|net65`.

## Самостоятельный запуск

Откройте PowerShell в корне MetaRescue.

```powershell
python -B -X utf8 tools/run_interop.py self-test
python -B -X utf8 tools/run_interop.py self-test --matrix
python -B -X utf8 tools/run_product.py
```

В приложении выберите **Загрузить пресет → Демо Net65**, затем **Подготовить и открыть мир → Продолжить**. Выбор алгоритма находится в настройке **Повторная разведка**. Для первой сборки после изменения C++ запустите `python -B -X utf8 tools/run_product.py --build`. Для измерения FPS используйте `tools/run_mission.py --visible --realtime --frame-profile` на одном layout. Укажите разные имена запусков и меняйте только `--scout-patrol`. Метрики строятся по интервалам между кадрами после первых 15 модельных секунд. В отчёте есть отдельная максимальная пауза продвижения миссии.

Параметр `--demo-render` доступен в приложении и в запуске парной миссии. Он сохраняет окно 1600×1000, использует 75% внутреннего разрешения и снижает качество отражений, теней и глобального освещения. В оконном realtime Core просит Windows учитывать 1 мс таймер Unreal даже для перекрытого окна. Исходная политика процесса восстанавливается при остановке нативного Core. [Таймер Windows](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)

## Core и два направления проверки

```powershell
python -B -X utf8 tools/run_interop.py our-controller --environment http://127.0.0.1:18450 --seed 17331 --require-goal
python -B -X utf8 tools/run_interop.py our-environment --controller http://127.0.0.1:18451 --mode decide --seed 2026 --layout "D:/MetaRescue-output/worlds/net65-demo-fps-20261126-01.private.json" --require-goal
```

Перед первой командой запустите выделенную чужую среду. Перед второй запустите чужой контроллер. Для автономного контроллера используется `--mode driver`. Среда сообщает URL, по которому контроллер ведёт свой эпизод. Для RPC используйте `--decide-path decide` или `--decide-path decision` в соответствии с инструкцией поставщика. В режиме Metaroi v9 нужен `--decide-profile metaroi-v9`. Для быстрой проверки RPC в плоской среде добавьте `--engine planar` и опустите `--layout`.

Параметры миссии можно передать JSON-объектом в `--mission-parameters`. Указывайте `--input-package` для каждого пакета, чей SHA-256 должен войти в отчёт. `--decision-timeout` ограничивает отдельный запрос RPC. `--timeout` ограничивает весь эпизод. Каталоги запусков в `D:/MetaRescue-output/checks` содержат `report.json`, публичный обмен и журналы. Для нашего контроллера итог Net65 и подтверждённые осмотры доступны в его манифесте и JSONL. `--require-goal` проверяет фактический результат наряду с обменом.

Core 1.0 использует HTTP и рабочую форму Приложения А. Возможности и параметры действий проверяются по публичным декларациям, пространственные данные нормализуются в метры XYZ, ID остаются непрозрачными. Публикация `health` обязательна для контроллера, RPC `decide` или `decision` — расширение поставщика. Расхождения ТЗ и Приложения А, включая каталог категорий и форму Decision, описаны в [профиле взаимодействия](stage-1-interaction.md). Повтор байтов `step` разрешён при объявленной поддержке дедупликации.

## Проверенные исходные опыты

| Опыт | Результат | Свидетельство |
| --- | --- | --- |
| Воспроизведение архива | 18 тестов. 32 миссии на политику, по 213 передач, средний модельный расход 393,2058 у Guard20 и 391,9361 у Net65 | `D:/MetaRescue-output/checks/net65-source-20261126-01/verification.json` |
| Сверка расчёта | 240 одинаковых входов, в том числе 182 решения ждать | Тот же отчёт |
| 2D, парное сравнение | 12 пар на трёх seed. Поздняя цель в каждом запуске Net65, 6 передач против 3 Core. Штатная миссия, потеря связи и недоступный маршрут дали одинаковые исходы. 0 отклонений по ограничениям | `D:/MetaRescue-output/checks/net65-2d-matrix-20261126-03/report.json` |
| RescueQuarter, окно 1600×1000, исходная пара | 2 передачи у обоих режимов, 11 подтверждённых повторов Net65, 0 контактов машин. Модельный расход 347,02 и 290,79 Wh. Возраст осмотра в конце, средний 92,66 и 151,50 с, максимальный 258,5 и 236,5 с | `D:/MetaRescue-output/missions/net65-visible-20261126-01-core/manifest.json` и `D:/MetaRescue-output/missions/net65-visible-20261126-01-net65/manifest.json` |
| Штатный сценарий RescueQuarter | По 3 передачи на 387,97 с. Net65 подтвердил 5 повторов. Расход Core 195,45 Wh, Net65 190,38 Wh, контактов машин 0 | `D:/MetaRescue-output/checks/net65-independent-final-20261126-01/native-paired-index.json` |
| Объёмное препятствие | Парный эпизод по 24 с, одинаковый исход, контактов 0. Отдельная физическая проверка завершила маршруты с набором высоты за 4,72 и 9,47 с | `D:/MetaRescue-output/core-acceptance/net65-air-clearance-20261126-01/verification.json` |
| Асинхронное сохранение шага | Инъекция ошибки записи завершила эпизод с `INTERNAL_ERROR` при повторе. Оконный Core прошёл 405 модельных секунд после прогрева с 45,67 FPS, p95 30,02 мс, p99 54,83 мс. До изменения p99 Core был 367,96 мс | `D:/MetaRescue-output/core-acceptance/net65-realtime-archive-20261126-01/verification.json` и `D:/MetaRescue-output/missions/net65-visible-20261126-02-core/manifest.json` |
| Демо 300 с, облегчённый рендер и запись на SSD | Core 47,47 FPS и p95 30,16 мс, Net65 45,50 FPS и p95 30,33 мс. Две передачи у обоих режимов, 7 подтверждённых повторов Net65 | `D:/MetaRescue-output/checks/net65-independent-final-20261126-01/performance-gate.json` |
| Итоговый оконный замер после изменения таймера | Core 57,46 FPS и p95 19,02 мс за 335,44 с. Net65 59,00 FPS и p95 18,16 мс за 321,72 с. Оба порога и относительный предел пройдены | `D:/MetaRescue-output/checks/net65-independent-final-20261126-01/performance-gate-windows-timer.json` |
| Запуск из приложения | Выбор «Демо Net65» и подготовка мира открыли миссию на паузе. Нажатие «Продолжить» довело её до 300 с. Найдены 3 человека, подтверждены 2 передачи и 7 повторных осмотров. Окно закрыто после завершения | [Действия интерфейса](<D:/MetaRescue-output/product-sessions/20261126T202953698328Z/ui.jsonl>) · [Миссия](<D:/MetaRescue-output/missions/product-20261126T203036079117Z/manifest.json>) |
| Наш контроллер в независимых средах | В Metaroy Lab подтверждено 3, 3, 3 цели, в Metaroy Portable 2, 2, 1 по seed 16429, 19013, 23821. Все шесть прогонов сохранили зафиксированные входы | `D:/MetaRescue-output/checks/net65-independent-final-20261126-01/outbound-index.json` |
| Автономный режим тестера | Локальная 2D-среда, контроллер ведёт эпизод через Core. Две подтверждённые передачи за 159 с. Тестер прочитал публичный журнал и связал результаты с решениями | `D:/MetaRescue-output/checks/net65-local-driver-20261126-01/acceptance/report.json` |
| Публичный Core в Unreal realtime | Четыре проверенных архива, параллельный точный повтор шага, ошибки конфликта и устаревания, чтение публичного журнала, закрытие и восстановление seed | [Нативный отчёт](<D:/MetaRescue-output/core-acceptance/net65-public-core-realtime-20261126-01/verification.json>) |

Модельный расход архива, нормированный расход 2D-стенда и расход в Wh относятся к своим профилям. В 2D поиск и эвакуация второй цели увеличили средний расход с 0,05322 до 0,08447 при завершении миссии на 159-й секунде вместо достижения лимита в 200 секунд. Для физической эвакуации RescueQuarter подтверждение связывает принятое решение, доставленную команду и её фактическое исполнение. Партнёры определяют выполненную цель своей миссии, например доставку помощи.

В текущем пресете горизонт 300 с. В проверке этого layout первая передача прошла примерно на 119-й модельной секунде, первый подтверждённый повтор Net65 на 208-й. В журнале контроллера ищите `net65_repeat_completed` внутри записей `planning_trace`. В [парной сводке](<D:/MetaRescue-output/checks/net65-independent-final-20261126-01/paired-metrics.json>) также собраны задержки рабочего планировщика, решения и HTTP-шага.

Итоговый замер выполнен в окне 1600×1000, с одной картой, камерой и параметром
`--demo-render`. Журналы записывались на SSD, копии на D сверены по SHA-256.
У Net65 p99 кадра 51,01 мс, 206 кадров длиннее 50 мс, максимум 80,03 мс,
пиковая память процесса 3,58 GiB. Самая длинная пауза модельного времени
0,22 с. У Core p99 53,22 мс, 281 кадр длиннее 50 мс, 3,44 GiB и единичная
пауза кадра и модели на 5,95 с. Игра продолжила эпизод.

В итоговом 300-секундном демо расход составил 258,05 Wh у Core и 210,64 Wh
у Net65. В конце сценария средний и максимальный возраст обследованных
секторов были 72,14 и 138,5 с у Core, 143,74 и 265,5 с у Net65. Оба режима
физически передали по два человека, контактов между машинами не было.

## Независимые контроллеры в RescueQuarter

Оба полученных ZIP зафиксированы перед испытаниями с новыми seed 16429, 19013 и 23821. Все шесть нативных эпизодов закрылись с проверяемыми журналами. В каждой попытке входы сохранили исходные SHA-256. Хэши контроллера и общих файлов Core совпадают в обеих сериях. У тестера зафиксированы две ревизии. Для обратного направления добавились явный лимит времени RPC и более строгая корреляция подтверждений.

Наш контроллер выполнил цели партнёров через переносимую политику Core. Для Net65 в чужой среде нужны объявленные оценки патруля через `metarescue.patrol_estimates.v1`. Оба партнёра предоставили базовые действия Core, в их отчётах `net65_repeats=0`.

| Контроллер | Результат в нашей среде на каждом из трёх seed |
| --- | --- |
| Metaroy Lab | `/v1/controller/decision exceeded 75 s at model time 0` |
| Metaroy Portable v0.1 | `INVALID_SCENARIO: Invalid scenario parameters` для первого `select`. Сервис отправил `search_grid_resolution`, `target_confidence_threshold`, `energy_reserve`, `connectivity_recovery_threshold` и `uncertainty_penalty`. `coverage_search` объявляет `observation_interval` и `additionalProperties: false`. |

[Зафиксированные входы](<D:/MetaRescue-output/checks/net65-independent-final-20261126-01/plan.json>) · [Матрица обоих направлений](<D:/MetaRescue-output/checks/net65-independent-final-20261126-01/interop-matrix.json>)

Итог двусторонней приёмки открыт. Для следующего прогона Lab нужен ответ на полный публичный кадр карты в пределах лимита RPC. Для Portable нужен выбор сценариев, параметров и ролей из фактически объявленных capabilities.

Повтор первого публичного кадра seed 16429 включал все 218 зон. Lab оставался
запущен и превысил 200 с на запросе `decision`. Portable вернул решение за
86 мс. В отдельном нативном прогоне тестер зафиксировал отказ на `step`,
ID запроса и решения, правило `additionalProperties` и пять лишних ключей
в `/selected_configuration/parameters`. Ошибка среды хранится отдельно от
сбоя контроллера и неоднозначного исхода сетевого запроса. [Повтор кадра](<D:/MetaRescue-output/checks/net65-independent-first-frame-20261127-01/verification.json>) · [Нативная диагностика](<D:/MetaRescue-output/checks/net65-portable-native-diagnosis-01/trial/report.json>)

Первый запрос можно повторить на вновь запущенном контроллере. Файлы содержат
публичные Observation и capabilities, полученные при reset.

```powershell
$capture = 'D:/MetaRescue-output/checks/net65-independent-first-frame-20261127-01'
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/v1/controller/decision' -Method Post -ContentType 'application/json' -InFile "$capture/metaroy_lab/request.json" -TimeoutSec 75
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/v1/controller/decide' -Method Post -ContentType 'application/json' -InFile "$capture/metaroy_portable_v0_1/request.json"
```

### Регрессия Metaroi v9

Исходный Metaroi v9 повторил две физические эвакуации за горизонт 480 с в актуальном RescueQuarter на исторических карте и seed 123456. Обмен и нативный архив прошли проверку. При запуске в обратном направлении наш публичный клиент обнаружил первое расхождение на ресурсе `energy`. Metaroi v9 использует форму без обязательного в нашем профиле `id` и посылает дополнительное поле `cost_per_meter`. [Контроллер v9 в Unreal](<D:/MetaRescue-output/checks/net65-metaroi-v9-20261126-01/inbound-exact-123456/report.json>) · [Текущий Core в среде v9](<D:/MetaRescue-output/checks/net65-metaroi-v9-20261126-01/outbound-v2/report.json>)

## Где находится реализация

- `controller/net65.py` считает выигрыш от повторного осмотра. `controller/native_patrol.py` оценивает физические маршруты, а `controller/policy.py` принимает результаты планировщика и подтверждения осмотра.
- `environment/world.py` и `environment/server.py` исполняют плоскую миссию. `controller/public_core.py` и `controller/portable.py` обрабатывают публичные capabilities, координаты и ограничения.
- `tools/run_interop.py` запускает три режима приёмки. `tools/net65_demo.py`, `tools/product_session.py` и интерфейс миссии Unreal подключают пресет.
- `MRCoreDecision.cpp` и `MRCoreRuntime.cpp` ведут запись шага, публикацию наблюдений и оконные замеры. Формат нативного архива описан в [документации журналов](native-journals.md).

Набор Python проекта прошёл 276 тестов, в бенчмарке v0.5.2 прошли 55, в
утилитах Unreal 10. Нативная сборка UE 5.8 успешна, отчёт Automation содержит
четыре успешных теста MetaRescue.Core. [Сборка](<D:/MetaRescue-output/checks/net65-verify-20261126-01/timer-build.log>) · [Automation](<D:/MetaRescue-output/automation/20261126T203727685154Z/verification.json>)
