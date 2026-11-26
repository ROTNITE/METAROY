"""Build/prepare/open the separate Unreal environment with installed engine tools."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import json

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT/'MetaRescue'/'MetaRescue.uproject'


def main():
    parser=argparse.ArgumentParser(description='MetaRescue Unreal physics environment')
    parser.add_argument('--engine',type=Path,default=Path(os.environ.get('MR_UNREAL_ENGINE',r'C:\Program Files\Epic Games\UE_5.8')))
    parser.add_argument('--build',action='store_true',help='compile the C++ Editor module')
    parser.add_argument('--setup',action='store_true',help='create materials and map if not present')
    parser.add_argument('--rebuild-scene',action='store_true',help='explicitly regenerate the code-managed district geometry')
    parser.add_argument('--smoke',action='store_true',help='offscreen physics run (90 model seconds by default)')
    parser.add_argument('--smoke-seconds',type=int,default=90,help='duration of physics smoke, 30..300 model seconds')
    parser.add_argument('--editor',action='store_true',help='open the Unreal editor')
    args=parser.parse_args()
    if not 30<=args.smoke_seconds<=300:parser.error('--smoke-seconds must be 30..300')
    engine=args.engine/'Engine'
    editor=engine/'Binaries/Win64/UnrealEditor.exe'
    commandlet=engine/'Binaries/Win64/UnrealEditor-Cmd.exe'
    if not editor.is_file():parser.error('Unreal Engine 5.8 not found; supply --engine or MR_UNREAL_ENGINE')
    logdir=ROOT/'MetaRescue'/'Saved'/'Automation';logdir.mkdir(parents=True,exist_ok=True)
    def run(command,name,hidden=False):
        log=logdir/(name+'.log');print(name+': '+str(log),flush=True)
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' and hidden else 0
        with log.open('w',encoding='utf-8') as output:
            result=subprocess.run([str(x) for x in command],cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,creationflags=flags)
        if result.returncode:
            print(log.read_text(encoding='utf-8',errors='replace')[-7000:])
            raise RuntimeError(f'{name} failed: exit {result.returncode}; {log}')
        print(name+' completed',flush=True)
    if args.build:
        runtimes=list((engine/'Binaries/ThirdParty/DotNet').glob('*/win-x64/dotnet.exe'))
        if not runtimes:parser.error('Bundled Unreal .NET runtime not found')
        run([sorted(runtimes)[-1],engine/'Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll',
             'MetaRescueEditor','Win64','Development','-Project='+str(PROJECT),
             '-WaitMutex','-NoHotReloadFromIDE','-MaxParallelActions=2'],'build',True)
    if args.setup or args.rebuild_scene:
        if not (ROOT/'MetaRescue/Content/Scenario/scenario.json').is_file():
            run([sys.executable,'-B','-X','utf8',ROOT/'tools/export_scenario.py'],'scenario',True)
        else:
            print('Preserving the existing exported scenario snapshot.',flush=True)
        if not args.rebuild_scene and (ROOT/'MetaRescue/Content/Maps/RescueDistrict.umap').is_file() and (ROOT/'MetaRescue/Content/Scenario/scene-build.json').is_file():
            print('Saved map already exists; preserving it. Use the editor for authored changes.')
        else:
            run([commandlet,PROJECT,'-run=pythonscript','-script='+str(ROOT/'tools/build_scene.py'),
                 '-unattended','-NoSound','-nosplash','-stdout','-FullStdOutLogOutput'],'scene',True)
    if args.smoke:
        (logdir/'smoke-run.json').write_text(json.dumps({'started_at':time.time(),'requested_seconds':args.smoke_seconds}),encoding='utf-8')
        run([editor,PROJECT,'/Game/Maps/RescueDistrict','-game','-RenderOffscreen','-windowed',
             '-ResX=1600','-ResY=1000','-ForceRes','-NoSound','-nosplash','-unattended','-MRSmoke','-MRQuitAfter='+str(args.smoke_seconds),
             '-stdout','-FullStdOutLogOutput'],'physics-smoke',True)
        run([sys.executable,'-B','-X','utf8',ROOT/'tools/check_smoke.py'],'physics-check',True)
    elif args.editor or not (args.build or args.setup or args.rebuild_scene):
        subprocess.Popen([str(editor),str(PROJECT)],cwd=ROOT)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError) as error:
        print(error,file=sys.stderr);raise SystemExit(1)
