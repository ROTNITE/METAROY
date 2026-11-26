# MetaEvolution Rescue — контекст (2026-11-09)

- Дроны ищут скрытых людей, эвакуаторы одновременно доставляют найденных в пункт. Два NSGA-II: разведка и эвакуация. Среда владеет истиной, контроллер — полученными наблюдениями. NSGA-II не обязан выигрывать.
- **Текущая база — пользовательский v0.3 в `benchmarks/metarescue-v0.3/`**, 16 файлов без правок. v0.2 (19 файлов) сохранена отдельно. Python 3.10+, stdlib, A*, один человек/рейс, без зарядки, общий горизонт.
- Три страницы: разведка, offline-эвакуация, online-миссия. Пять связок: greedy+greedy, frontier+local, nsga2+greedy, greedy+nsga2, nsga2+nsga2.
- v0.3 получена: 8 вероятностей на **30 с**, общая лента, динамика/поздние цели. 30 тестов проходят, но аудит воспроизвёл перерасход сервиса и округление старта пути; оригинал не исправлялся. OR-Tools/JSON-кнопка/авто-venv из v0.2 потеряны. Ретрансляторов нет.
- `benchmark/` — иной черновик (энергия, дедлайны, счёт отличаются). Не подменять им архивы, не смешивать результаты.

Документы по задаче:
- `docs/current-concept.md` — вся концепция/изменения.
- `docs/benchmark-v0.3-spec.md` — требования; `benchmark-v0.3-review.md` — фактическое выполнение, пробелы и проверки.
- `docs/benchmark-v0.2-review.md` — структура, проверки и найденные ограничения.
- `docs/benchmark-research-plan.md` — 30×3, бюджеты, HV/IGD+, соперники, отключения.
- `docs/sources/` — весь TXT (538 строк), 5 доступных ходов ветки, ZIP и хэши.
- `docs/stage-1-interaction.md`, `contracts/` — принятый Core 1.0; `environment/`, `controller/`, `web/` — основной каркас. Лабораторный API не заменяет нормативный.
- `ТЗ/`, `лекции/`, `концепция/` — материалы. Старый Evolution World удалён пользователем; не восстанавливать.

Команды из корня:
```powershell
python -B -X utf8 "benchmarks/metarescue-v0.3/main.py"
python -B -X utf8 -m unittest discover -s "benchmarks/metarescue-v0.3/main part/tests" -v
python -B -X utf8 -m unittest discover -s tests -v
git diff --check
```
Проверено: v0.3 30 тестов, браузер и 3 seed; аудит `tools/audit_metarescue_v03.py` намеренно падает на 2 дефектах. v0.2 16 тестов; отдельно 19 черновика и 15 контракта. OR-Tools не проверен. Импорты не менять молча.

3D: `unreal/MetaRescue/MetaRescue.uproject`, UE 5.8.4. `python unreal/launch.py --editor`; подготовка `--build --setup`, проверка `--smoke`. Сцена/материалы созданы после imagegen (`docs/3d-visual-direction.md`); статический снимок v0.3, 7 физических агентов, 23 обломка, обрушение. 90 с: движение/контакты/1 доставка подтверждены; runtime NSGA/метарой и динамика v0.3 ещё не подключены. Управление камерой вручную не проверено: захват Windows недоступен. Подробнее `unreal/README.md`, `unreal/QA.md`.
