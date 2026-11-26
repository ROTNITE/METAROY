MetaRescue: среда и контроллер с Net65 — облегчённый исходный комплект

Содержит Python-среду environment/, контроллер controller/ с Net65, Core-контракт, тесты, инструменты, сценарии и исходный код Unreal. 3D-модели, текстуры, карты Unreal, готовая сборка, Unreal Engine, Python и ключи API в комплект не входят. Плоская 2D-среда работает без 3D-ассетов; 3D-сцену из этого архива запустить или собрать полноценно нельзя без отсутствующих ассетов.

Установка зависимости: python -m pip install -r requirements.txt
Проверка среды и контроллера: python -B -X utf8 tools/run_interop.py self-test
Net65 в контроллере включается явно через --scout-patrol net65 (по умолчанию core). Подробности: docs/net65-runtime.md, docs/stage-1-interaction.md.

Проверки межкомандной совместимости и их ограничения описаны в docs/net65-runtime.md.
