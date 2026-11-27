import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import package_windows


class WindowsScenarioPackagingTests(unittest.TestCase):
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
