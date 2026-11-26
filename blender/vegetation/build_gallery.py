"""Build an offline gallery from the verified download catalog; stdlib only."""
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
catalog = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
cards = []
for row in catalog['assets']:
    ident = row['id']
    local = f'polyhaven/{ident}'
    size = sum(f['bytes'] for f in row['files']) / 1e6
    blend = f'{local}/{ident}_2k.blend'
    note = ''
    if ident == 'dry_branches_medium_01' and (ROOT / 'prepared' / f'{ident}_2k.blend').is_file():
        blend = f'prepared/{ident}_2k.blend'
        note = '<p class="meta">Blender: локальная копия с упакованными текстурами и исправленным HDR-фоном. Оригинал сохранён в папке набора.</p>'
    cards.append(f'''<article>
      <img src="{local}/preview.png" alt="{html.escape(row['name'])}">
      <h2>{html.escape(row['name'])}</h2>
      <p>{html.escape(row['purpose_ru'])}</p>
      <p class="meta">BLEND + FBX · текстуры 2K · {size:.1f} MB · CC0</p>
      {note}<p><a href="{blend}">Blender</a> ·
      <a href="{local}/{ident}_2k.fbx">FBX</a> ·
      <a href="{row['source_url']}">Источник и автор</a></p>
    </article>''')
page = '''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MetaRescue — деревья и древесные обломки</title>
<style>
body{font:16px/1.55 system-ui,sans-serif;background:#192022;color:#e8eded;margin:0;padding:30px}
main{max-width:1280px;margin:auto}h1{font-size:30px}a{color:#a1d7b3}
.intro{max-width:900px;color:#c4cece}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(265px,1fr));gap:20px}
article{background:#263032;border:1px solid #455052;border-radius:12px;padding:18px}
img{width:100%;height:240px;object-fit:contain;background:#353e3a;border-radius:8px}
h2{font-size:21px;margin:12px 0}.meta{font-size:13px;color:#bac5c5}
</style><main><h1>Деревья и древесные обломки · MetaRescue</h1>
<p class="intro">Подборка 11 ноября 2026: 12 наборов для квартала после землетрясения.
Оригинальные превью Poly Haven; это не кадры нашей сцены. Все файлы скачаны локально.
<a href="README.md">Назначение, ограничения и проверка</a> ·
<a href="catalog.json">Каталог с хэшами и URL</a>.</p>
<p class="intro">Для дворов: Tree Small 02. Для завалов: Dead Tree Trunk, пни и ветки.
Обломки местами старые/мшистые; свежие изломы потребуют доработки.
Перед UE нужна подготовка LOD, материалов листвы и коллизий.</p><div class="grid">
''' + '\n'.join(cards) + '</div></main></html>'
(ROOT / 'index.html').write_text(page, encoding='utf-8')
print('GALLERY', len(cards), 'assets', ROOT / 'index.html')
