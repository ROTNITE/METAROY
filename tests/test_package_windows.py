import tempfile
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import package_windows


class WindowsScenarioPackagingTests(unittest.TestCase):
    def test_cook_log_detects_required_material_fallback_in_both_path_formats(self):
        expected = [
            'Failed to compile Material /Game/EffectsV3/WaterR6/M_WetFringe.M_WetFringe',
            r'[AssetLog] Content\EffectsV3\Revision6\M_PhysicalSmoke.uasset: Failed to compile Material',
        ]
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp)/'BuildCookRun.log'
            log.write_text('\n'.join([
                'Failed to compile Material /Game/RescueAssets/M_Tree',
                'Missing cached shadermap for /Game/EffectsV3/Revision6/M_PhysicalSmoke',
                *expected,
            ]), encoding='utf-8')
            self.assertEqual(package_windows.required_material_shader_failures(log), expected)

    def test_build_rejects_missing_watercraft_profile_before_cook(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)/'project'
            scenario = project/'Content/Scenario'
            scenario.mkdir(parents=True)
            for name in ('core-scenarios-v1.json', 'quarter.json', 'scenario.json'):
                (scenario/name).write_text('{}', encoding='utf-8')
            output = Path(temp)/'output'
            with patch.object(package_windows, 'PROJECT', project):
                with self.assertRaisesRegex(FileNotFoundError, 'watercraft-hydrostatics-r1.json'):
                    package_windows.build(output, Path(temp)/'engine', None)
            self.assertFalse(output.exists())

    def test_shipping_stage_requires_its_win64_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            distribution = Path(temp)
            runtime = distribution/'runtime/MetaRescue'
            binaries = runtime/'Binaries/Win64'
            binaries.mkdir(parents=True)
            (binaries/'MetaRescue.exe').touch()
            with self.assertRaisesRegex(FileNotFoundError, 'MetaRescue-Win64-Shipping.exe'):
                package_windows.stage_runtime(distribution, None, scenario_files={}, configuration='Shipping')

    def test_shipper_finds_the_platform_subdirectory_written_by_unreal(self):
        with tempfile.TemporaryDirectory() as temp:
            distribution = Path(temp)
            executable = distribution/'runtime/Windows/MetaRescue/Binaries/Win64/MetaRescue-Win64-Shipping.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            self.assertEqual(package_windows.find_packaged_game(distribution), executable)
            self.assertEqual(package_windows.find_packaged_game(distribution, 'Shipping'), executable)

    def test_native_stage_must_match_frozen_source_scenario_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in package_windows.REQUIRED_SCENARIO:
                (directory/name).write_text('{}', encoding='utf-8')
            source = package_windows.verify_scenario_files(directory)
            self.assertEqual(package_windows.verify_scenario_files(directory, expected=source), source)
            profile = directory/'watercraft-hydrostatics-r1.json'
            profile.write_text('{"different":true}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'watercraft-hydrostatics-r1.json'):
                package_windows.verify_scenario_files(directory, expected=source)
            profile.unlink()
            with self.assertRaisesRegex(FileNotFoundError, 'watercraft-hydrostatics-r1.json'):
                package_windows.verify_scenario_files(directory, expected=source)


    def test_isolated_staged_sources_run_actual_m5_trial(self):
        # No fake Win64 binary or native evidence: this exercises precisely
        # the Python part copied by stage_runtime on both CI platforms.
        with tempfile.TemporaryDirectory() as temp:
            distribution=Path(temp)/'stage with spaces'
            distribution.mkdir()
            package_windows.stage_python_sources(distribution)
            scenario=distribution/'unreal/MetaRescue/Content/Scenario'
            shutil.copytree(package_windows.ROOT/'unreal/MetaRescue/Content/Scenario',scenario)
            self.assertTrue((distribution/'Start-M5.cmd').is_file())
            self.assertTrue((distribution/'M5.md').is_file())
            self.assertTrue((distribution/'web/m5/index.html').is_file())
            package_windows.check_python_entrypoints(distribution,python=sys.executable)
            output=Path(temp)/'trial'
            env={**os.environ,'PYTHONPATH':'','PYTHONNOUSERSITE':'1',
                 'METARESCUE_OUTPUT':str(Path(temp)/'offline'),
                 'METARESCUE_LIVE_BUDGET':str(Path(temp)/'offline-budget.json')}
            for key in ('METARESCUE_M5_TRACE_FILE','METARESCUE_M5_PROGRESS_FILE'):
                env.pop(key,None)
            done=subprocess.run([sys.executable,'-B','-X','utf8','-m','tools.m5_trial',
                '--controller','metarescue','--engine','reference','--profile','K',
                '--seed','89725161','--timeout','40','--output',str(output)],
                cwd=distribution,env=env,capture_output=True,text=True,encoding='utf-8',timeout=70)
            self.assertEqual(done.returncode,0,done.stdout+'\n'+done.stderr)
            report=json.loads((output/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['exchange'],'passed')
            self.assertTrue(report['inputs_preserved'])
            self.assertEqual(report['independent_truth']['physical_handovers'],2)
            self.assertTrue(report['independent_truth']['all_evacuated'])
            self.assertFalse((Path(temp)/'offline-budget.json').exists())
