import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from controller.m5_progress import Progress, read_page, read_reference_page, MAX_LINE, MAX_FILE


class ProgressTests(unittest.TestCase):
    def test_summary_is_bounded_and_excludes_payload_and_private_fields(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            p=Progress('metarescue',path)
            p.record('http_exchange',{'private':'secret'})
            p.record('decision',{'decision':{'decision_id':'d1','time':2,'commands':[{'private':'secret'}]},
                                 'planning_wall_s':0.02})
            p.record('step_response',{'status':'running','time':3,'command_results':[{}],'events':[{}],
                                      'observation':{'private':'secret'}})
            self.assertNotIn('secret',path.read_text())
            first=read_page(path,0,1)
            self.assertEqual(first['rows'][0]['commands'],1)
            self.assertTrue(first['more'])
            second=read_page(path,first['cursor'],1)
            self.assertEqual(second['rows'][0]['command_results'],1)
            self.assertFalse(second['more'])
            self.assertEqual(read_page(path,second['cursor'])['rows'],[])
            for cursor,limit in ((1,1),(-1,1),(MAX_FILE+1,1),(0,101)):
                with self.assertRaises(ValueError):
                    read_page(path,cursor,limit)

    def test_partial_record_and_missing_file(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            self.assertFalse(read_page(path)['available'])
            row=json.dumps({'kind':'decision','source':'interop','time':2}).encode()
            path.write_bytes(row)
            self.assertEqual(read_page(path)['cursor'],0)
            with path.open('ab') as stream:
                stream.write(b'\n')
            self.assertEqual(read_page(path)['rows'][0]['time'],2)

    def test_oversize_and_invalid_rows(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            path.write_bytes(b'x'*(MAX_LINE+1)+b'\n')
            with self.assertRaises(ValueError):
                read_page(path)
            path.write_text('{"kind":"private","source":"interop"}\n')
            with self.assertRaises(ValueError):
                read_page(path)

    def test_optional_writer_failure_never_changes_mission(self):
        with TemporaryDirectory() as folder:
            p=Progress('interop',Path(folder)/'missing'/'progress.jsonl')
            p.record('decision',{'decision':{}})
            self.assertIsNone(p.path)

    def test_reader_does_not_return_unrecognized_fields(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            path.write_text('{"kind":"decision","source":"reference","time":2,"private":"secret"}\n')
            self.assertNotIn('private',read_page(path)['rows'][0])


    def test_independent_journal_is_projected_without_fabricated_wall_time(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reference.jsonl'
            entry={'decision':{'decision_id':'d1','episode_id':'e','request_id':'r','commands':[{},{}]},
                   'response':{'episode_id':'e','request_id':'r','time':2,'status':'running',
                               'command_results':[{}],'observation':{'private':'secret'}}}
            original=(json.dumps(entry)+'\n').encode()
            path.write_bytes(original+original)
            first=read_reference_page(path,0,1)
            self.assertEqual(first['rows'][0]['decision_id'],'d1')
            self.assertEqual(first['rows'][0]['commands'],2)
            self.assertEqual(first['rows'][0]['command_results'],1)
            self.assertNotIn('wall_s',first['rows'][0])
            self.assertNotIn('observation',first['rows'][0])
            self.assertTrue(first['more'])
            second=read_reference_page(path,first['cursor'],1)
            self.assertFalse(second['more'])
            self.assertEqual(path.read_bytes(),original+original)
