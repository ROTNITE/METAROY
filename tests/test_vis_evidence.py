import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import struct
from tools.verify_distribution_zip import verify_zip, SCENARIOS
from tools.core_contest_check import frame_coverage
from tools.package_windows import verify_revised_assets, REVISED_ASSETS


class DistributionIntegrityTests(unittest.TestCase):
    def make_files(self):
        names=['Start-MetaRescue.cmd','python/python.exe','tools/run_product.py',
               'tools/run_mission.py','environment/main.py',
               'runtime/Windows/MetaRescue/Binaries/Win64/MetaRescue-Win64-Shipping.exe']
        files={n:b'test fixture, not runtime' for n in names}
        for name in SCENARIOS:
            files['runtime/Windows/MetaRescue/Content/Scenario/'+name]=b'{}'
            files['unreal/MetaRescue/Content/Scenario/'+name]=b'{}'
        return files

    def zip(self,path,files,*,mutation=None,extra=None,prefix=''):
        manifest={n:hashlib.sha256(data).hexdigest() for n,data in files.items()}
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr(prefix+'SHA256.json',json.dumps(manifest))
            for n,data in files.items():
                archive.writestr(prefix+n,mutation[1] if mutation and n==mutation[0] else data)
            if extra:archive.writestr(*extra)

    def test_complete_zip_with_and_without_root(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.zip'
            files=self.make_files()
            for prefix in ('','MetaEvolution-Rescue/'):
                self.zip(path,files,prefix=prefix)
                result=verify_zip(path)
                self.assertEqual(result['files'],len(files))
                self.assertFalse(result['native_execution_verified'])

    def test_tampering_and_path_collisions_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.zip';files=self.make_files()
            self.zip(path,files,mutation=('environment/main.py',b'changed'))
            with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):verify_zip(path)
            for name in ('../outside','environment/MAIN.py','runtime/CON.txt','runtime/../file','unlisted','environment/main.py/child'):
                with self.subTest(name=name):
                    self.zip(path,files,extra=(name,b'extra'))
                    with self.assertRaises(ValueError):verify_zip(path)

    def test_missing_and_divergent_scenarios_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.zip';files=self.make_files()
            name='unreal/MetaRescue/Content/Scenario/quarter.json'
            files[name]=b'changed'
            self.zip(path,files)
            with self.assertRaisesRegex(ValueError,'scenario copy'):verify_zip(path)
            del files[name]
            self.zip(path,files)
            with self.assertRaisesRegex(ValueError,'scenario copy'):verify_zip(path)

    def test_unmaterialized_assets_fail_before_cook(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaises(FileNotFoundError):verify_revised_assets(root)
            for relative in REVISED_ASSETS:
                p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'v'*80)
            verify_revised_assets(root)
            (root/REVISED_ASSETS[0]).write_bytes(b'version https://git-lfs.github.com/spec/v1\n'+b'x'*64)
            with self.assertRaisesRegex(ValueError,'Unmaterialized'):verify_revised_assets(root)


class ContestFrameTests(unittest.TestCase):
    def test_frame_coverage_checks_every_delivered_occurrence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'overview').mkdir()
            name='overview/overview-00001.png'
            (root/name).write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'+struct.pack('>II',1600,1000)+b'fixture header only')
            events=[{'id':str(i),'type':str(i),'status':'active','received_at':1.} for i in range(14)]
            rows=[{'record_type':'contest_frame_requested','payload':{'file':name,'events':events,'observation_time':1.}}]
            result=frame_coverage(root,events,rows)
            self.assertTrue(result['visual_inspection_pending'])
            rows[0]['payload']['events']=events[:-1]
            with self.assertRaisesRegex(ValueError,'Incomplete'):frame_coverage(root,events,rows)
            rows[0]['payload']['events']=events
            rows[0]['payload']['observation_time']=0.
            with self.assertRaisesRegex(ValueError,'future'):frame_coverage(root,events,rows)
