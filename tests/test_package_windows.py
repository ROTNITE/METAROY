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
