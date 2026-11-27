"""Build and stage the independent Windows environment and controller."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controller.ai import file_lock
from environment.main import DEFAULT_OUTPUT, WINDOWS_GAME_BINARIES, existing_unreal, find_packaged_game
from tools.native_archive import file_sha256
from tools.product_session import default_product_request


PROJECT = ROOT/'unreal/MetaRescue'
TOOLS = ('run_product.py', 'product_session.py', 'run_mission.py', 'native_archive.py',
         'native_process.py', 'analytics_service.py', 'run_native_series.py', 'series_plan.py',
         'issue_operator.py', 'net65_demo.py', 'm5_lab.py', 'm5_trial.py',
         'm5_matrix.py', 'm5_score.py', 'm5_spec.py', 'run_interop.py', 'goal_evidence.py')
ENTRYPOINTS = ('run_product.py', 'product_session.py', 'run_mission.py',
               'run_native_series.py', 'series_plan.py', 'issue_operator.py')
MODULE_ENTRYPOINTS = ('tools.m5_lab', 'tools.m5_trial', 'tools.run_interop',
                      'conformance.reference_environment', 'conformance.reference_controller')
REQUIRED_SCENARIO = ('core-scenarios-v1.json', 'quarter.json', 'scenario.json',
                     'watercraft-hydrostatics-r1.json')
DEPENDENCIES = ('jsonschema', 'jsonschema-specifications', 'attrs', 'referencing', 'rpds-py')
STARTUP = r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
if not defined METARESCUE_OUTPUT set "METARESCUE_OUTPUT=%~dp0output"
if not defined METARESCUE_LIVE_BUDGET if exist "D:\MetaRescue-output\ai-budget.json" set "METARESCUE_LIVE_BUDGET=D:\MetaRescue-output\ai-budget.json"
if not defined METARESCUE_LIVE_BUDGET set "METARESCUE_LIVE_BUDGET=%LOCALAPPDATA%\MetaRescue\ai-budget.json"
"%~dp0python\python.exe" -B -X utf8 tools\run_product.py --configuration "%~dp0presets\offline.json" %*
exit /b %ERRORLEVEL%
"""


M5_STARTUP = r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
if not defined METARESCUE_OUTPUT set "METARESCUE_OUTPUT=%~dp0output"
"%~dp0python\python.exe" -B -X utf8 -m tools.m5_lab %*
exit /b %ERRORLEVEL%
"""


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def freeze_files(root):
    return {p.relative_to(root).as_posix(): file_sha256(p) for p in sorted(root.rglob('*')) if p.is_file()}


def verify_scenario_files(directory, *, expected=None):
    files = freeze_files(directory)
    missing = [name for name in REQUIRED_SCENARIO if name not in files]
    if missing:
        raise FileNotFoundError(f'Missing runtime scenario data in {directory}: '+', '.join(missing))
    if expected is not None and files != expected:
        changed = sorted(name for name in files.keys() | expected.keys() if files.get(name) != expected.get(name))
        raise RuntimeError(f'Runtime scenario data changed in {directory}: '+', '.join(changed))
    return files


def project_inputs(project):
    values = {'MetaRescue.uproject': file_sha256(project/'MetaRescue.uproject')}
    for folder in ('Source', 'Config', 'Content'):
        values.update({folder+'/'+name: value for name, value in freeze_files(project/folder).items()})
    return values


def copy_project(snapshot):
    snapshot.mkdir(parents=True, exist_ok=False)
    descriptor = json.loads((PROJECT/'MetaRescue.uproject').read_bytes())
    plugins = [plugin for plugin in descriptor['Plugins'] if plugin['Name'] != 'AndroidFileServer']
    descriptor['Plugins'] = [*plugins, {'Name': 'AndroidFileServer', 'Enabled': False}]
    write_json(snapshot/'MetaRescue.uproject', descriptor)
    for part in ('Source', 'Config', 'Content'):
        shutil.copytree(PROJECT/part, snapshot/part)
    # Android File Server is not part of the Windows distribution.
    engine_ini = snapshot/'Config/DefaultEngine.ini'
    sections = engine_ini.read_text(encoding='utf-8-sig').splitlines()
    output = []
    include = True
    for line in sections:
        if line.startswith('['):
            include = not line.startswith('[/Script/AndroidFileServer')
        if include:
            output.append(line)
    engine_ini.write_text('\n'.join(output)+'\n', encoding='utf-8')


def prepare_grounding(project, engine, output, cache):
    scenario = project/'Content/Scenario'
    library = json.loads((scenario/'library-v3.json').read_bytes())
    replacements = json.loads((scenario/'city-assets.json').read_bytes())['replacements']
    meshes = set()
    for asset in library['assets']:
        for part in asset['parts']:
            if (asset['id'] in ('BuildingVariants', 'Rubble', 'Slab', 'Manhole') or
                    asset['id'] == 'SceneryKit' and part['object'].startswith('fragment_')):
                mesh = part['mesh']
                meshes.add(mesh)
                meshes.update(entry['mesh'] for entry in replacements.get(mesh, {}).get('parts', ()))
    requested = output/'grounding-meshes.json'
    report_path = output/'grounding-verification.json'
    write_json(requested, sorted(meshes))
    binaries = engine/'Engine/Binaries'
    dotnet = sorted((binaries/'ThirdParty/DotNet').glob('*/win-x64/dotnet.exe'))[-1]
    build_editor = [str(dotnet), str(binaries/'DotNET/UnrealBuildTool/UnrealBuildTool.dll'),
                    'MetaRescueEditor', 'Win64', 'Development', '-Project='+str(project/'MetaRescue.uproject'),
                    '-NoUBA', '-MaxParallelActions=2']
    script = ROOT/'unreal/tools/prepare_grounding_assets.py'
    prepare = [str(binaries/'Win64/UnrealEditor-Cmd.exe'), str(project/'MetaRescue.uproject'),
               '-run=pythonscript', '-script='+str(script), '-unattended', '-nullrhi', '-nop4', '-nosplash',
               '-LocalDataCachePath='+str(cache), '-stdout', '-FullStdOutLogOutput']
    with (output/'GroundingEditorBuild.log').open('w', encoding='utf-8') as log:
        subprocess.run(build_editor, cwd=project, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (output/'GroundingPrepare.log').open('w', encoding='utf-8') as log:
        subprocess.run(prepare, cwd=project, stdout=log, stderr=subprocess.STDOUT, check=True,
                       env={**os.environ, 'MR_GROUNDING_MESHES':str(requested), 'MR_GROUNDING_REPORT':str(report_path)})
    report = json.loads(report_path.read_bytes())
    if report.get('passed') is not True or report['meshes'] != sorted(meshes):
        raise RuntimeError('Grounding commandlet did not verify the requested mesh assets')
    report.update(build_editor=build_editor, prepare=prepare, script_sha256=file_sha256(script),
                  report_sha256=file_sha256(report_path))
    return report


def bundle_python(destination):
    source = Path(sys.base_prefix)
    library = f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    destination.mkdir()
    for filename in ('python.exe', 'python3.dll', library, 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(source/filename, destination)
    skip_cache = shutil.ignore_patterns('__pycache__', '*.pyc', '*_d.pyd', '*.pdb')
    shutil.copytree(source/'DLLs', destination/'DLLs', ignore=skip_cache)
    shutil.copytree(source/'Lib', destination/'Lib',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages', 'test', 'ensurepip'))
    packages = destination/'Lib/site-packages'
    packages.mkdir()
    versions = {}
    for name in DEPENDENCIES:
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for item in distribution.files:
            source_file = Path(distribution.locate_file(item))
            if '..' in item.parts or source_file.suffix == '.pyc' or not source_file.is_file():
                continue
            target = packages/Path(item)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
    return {'interpreter': sys.version.split()[0], 'dependencies': versions}


def check_python_entrypoints(distribution, *, python=None):
    python = distribution/'python/python.exe' if python is None else python
    # -I drops the working directory. Reinsert only the staged root for
    # module entrypoints; never load source-checkout or user-site modules.
    isolated = "import runpy,sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd())); name=sys.argv.pop(1); runpy.run_module(name,run_name='__main__')"
    commands = [(name,['tools/'+name]) for name in ENTRYPOINTS]
    commands += [(name,['-c',isolated,name]) for name in MODULE_ENTRYPOINTS]
    for name,command in commands:
        result = subprocess.run(
            [str(python), '-I', '-B', '-X', 'utf8', *command, '--help'],
            cwd=distribution, capture_output=True, text=True, encoding='utf-8', check=False, timeout=30)
        if result.returncode:
            raise RuntimeError(f'Packaged Python entry point {name} failed:\n{result.stderr.strip()}')


def stage_python_sources(distribution):
    """Copy the exact runtime modules, including independent M5 peers."""
    for part in ('controller', 'contracts', 'environment', 'analytics', 'web', 'conformance'):
        shutil.copytree(ROOT/part, distribution/part, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (distribution/'tools').mkdir()
    for name in TOOLS:
        shutil.copy2(ROOT/'tools'/name, distribution/'tools'/name)
    (distribution/'Start-M5.cmd').write_text(M5_STARTUP, encoding='ascii', newline='\r\n')
    shutil.copy2(ROOT/'docs/m5-interface.md', distribution/'M5.md')


def stage_runtime(distribution, demo, *, scenario_files, configuration='Development', fab_usage_notice=None):
    executable = find_packaged_game(distribution, configuration)
    if not executable.is_file():
        raise FileNotFoundError(f'BuildCookRun did not archive the Win64 game executable {executable.name}')
    native_scenario = executable.parents[2]/'Content/Scenario'
    verify_scenario_files(native_scenario, expected=scenario_files)
    stage_python_sources(distribution)
    shutil.copytree(native_scenario, distribution/'unreal/MetaRescue/Content/Scenario')
    details = bundle_python(distribution/'python')
    write_json(distribution/'python/dependencies.json', details)
    (distribution/'presets').mkdir()
    write_json(distribution/'presets/offline.json', default_product_request())
    if demo is not None:
        shutil.copy2(demo, distribution/'presets/recovery.private.json')
    (distribution/'Start-MetaRescue.cmd').write_text(STARTUP, encoding='ascii', newline='\r\n')
    shutil.copy2(ROOT/'docs/stage-1-interaction.md', distribution/'Core-1.0.md')
    shutil.copy2(ROOT/'docs/windows-runtime.md', distribution/'README.md')
    shutil.copy2(ROOT/'docs/core-operator.md', distribution/'Operator.md')
    shutil.copy2(ROOT/'docs/disaster-assets.md', distribution/'Asset-Sources.md')
    if fab_usage_notice is not None:
        shutil.copy2(fab_usage_notice, distribution/'Fab-Content-Notice.txt')
    check_python_entrypoints(distribution)
    details['verified_entrypoints'] = list(ENTRYPOINTS) + list(MODULE_ENTRYPOINTS)
    return details


def build(output, engine, demo, *, configuration='Shipping', fab_usage_notice=None):
    scenario_files = verify_scenario_files(PROJECT/'Content/Scenario')
    fab_packages = PROJECT/'Content/EffectsV3/Revision3/Fab'
    if any(fab_packages.rglob('*.uasset')) and fab_usage_notice is None:
        raise ValueError('Provide --fab-usage-notice for a cooked distribution containing Fab content')
    if fab_usage_notice is not None:
        fab_usage_notice = fab_usage_notice.resolve(strict=True)
        if not fab_usage_notice.is_file() or not fab_usage_notice.stat().st_size:
            raise ValueError('Fab usage notice must be a nonempty file')
        notice_sha256 = file_sha256(fab_usage_notice)
    output.mkdir(parents=True, exist_ok=False)
    project = output/'build-source/MetaRescue'
    distribution = output/'MetaEvolution-Rescue'
    copy_project(project)
    verify_scenario_files(project/'Content/Scenario', expected=scenario_files)
    distribution.mkdir()
    cache = DEFAULT_OUTPUT/'cache/ddc'
    cache.mkdir(parents=True, exist_ok=True)
    initial = project_inputs(project)
    grounding = prepare_grounding(project, engine, output, cache)
    before = project_inputs(project)
    grounding_changes = {name for name in initial.keys() | before.keys() if initial.get(name) != before.get(name)}
    expected_meshes = {'Content/'+name.removeprefix('/Game/').split('.', 1)[0]+'.uasset' for name in grounding['meshes']}
    if not grounding_changes <= expected_meshes:
        raise RuntimeError('Grounding commandlet changed unexpected inputs: '+str(sorted(grounding_changes-expected_meshes)))
    uat = engine/'Engine/Build/BatchFiles/RunUAT.bat'
    command = [str(uat), 'BuildCookRun', '-project='+str(project/'MetaRescue.uproject'),
               '-noP4', '-unattended', '-utf8output', '-targetplatform=Win64', '-clientconfig='+configuration,
               '-ubtargs=-NoUBA -MaxParallelActions=2',
               '-build', '-cook', '-stage', '-pak', '-iostore', '-prereqs', '-archive',
               '-map=/Game/Maps/RescueQuarter+/Game/Maps/RescueDistrict',
               '-stagingdirectory='+str(output/'stage'), '-archivedirectory='+str(distribution/'runtime'),
               '-AdditionalCookerOptions=-SkipZenStore -LocalDataCachePath='+str(cache)]
    manifest = {'state': 'building', 'command': command, 'source_files': before,
                'scenario_sha256': scenario_files, 'configuration': configuration,
                'grounding': grounding, 'grounding_changes': sorted(grounding_changes)}
    write_json(output/'build-manifest.json', manifest)
    with (output/'BuildCookRun.log').open('w', encoding='utf-8') as log:
        status = subprocess.run(command, cwd=project, stdout=log, stderr=subprocess.STDOUT, check=False)
    after = {name: file_sha256(project/name) if (project/name).is_file() else None for name in before}
    changed_inputs = [name for name, checksum in before.items() if after[name] != checksum]
    manifest.update(state='staging' if status.returncode == 0 and before == after else 'failed',
                    exit_code=status.returncode, frozen_inputs_preserved=not changed_inputs,
                    changed_inputs=changed_inputs)
    write_json(output/'build-manifest.json', manifest)
    if manifest['state'] == 'failed':
        raise RuntimeError('BuildCookRun or source verification failed. See '+str(output/'BuildCookRun.log'))
    try:
        manifest['python'] = stage_runtime(distribution, demo, scenario_files=scenario_files,
                                           configuration=configuration, fab_usage_notice=fab_usage_notice)
        if fab_usage_notice is not None:
            if file_sha256(distribution/'Fab-Content-Notice.txt') != notice_sha256:
                raise RuntimeError('Fab usage notice changed during packaging')
            manifest['fab_usage_notice_sha256'] = notice_sha256
    except Exception as error:
        manifest.update(state='failed', staging_error=f'{type(error).__name__}: {error}')
        write_json(output/'build-manifest.json', manifest)
        raise
    distribution_files = freeze_files(distribution)
    write_json(distribution/'SHA256.json', distribution_files)
    manifest.update(state='staged', distribution=str(distribution), files=len(distribution_files))
    write_json(output/'build-manifest.json', manifest)
    return distribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--configuration', choices=WINDOWS_GAME_BINARIES, default='Shipping',
                        help='Win64 build configuration (defaults to %(default)s)')
    parser.add_argument('--demo', type=Path)
    parser.add_argument('--fab-usage-notice', type=Path, help='recipient use conditions for incorporated Fab content')
    parser.add_argument('--engine', type=Path, default=Path(os.environ.get('MR_UNREAL_ENGINE',
                        r'C:\Program Files\Epic Games\UE_5.8')))
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info < (3, 13):
        parser.error('Packaging requires 64-bit Windows and the Python 3.13+ development environment')
    if not (args.engine/'Engine/Build/BatchFiles/RunUAT.bat').is_file():
        parser.error('RunUAT.bat is missing')
    with file_lock(DEFAULT_OUTPUT/'locks/unreal-owner.lock'):
        if existing_unreal():
            raise RuntimeError('Finish the current Unreal process before packaging')
        print(build(args.output.resolve(), args.engine.resolve(), args.demo, configuration=args.configuration,
                    fab_usage_notice=args.fab_usage_notice), flush=True)


if __name__ == '__main__':
    main()
