import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from controller.m5_progress import Progress, read_page, read_reference_page, MAX_LINE, MAX_FILE, MAX_SCAN_ROWS


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

    def test_reference_correlation_requires_explicit_matching_ids(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reference.jsonl'
            decision={'decision_id':'решение/λ','episode_id':'эпизод','request_id':'请求','commands':[{}]}
            response={'episode_id':'эпизод','request_id':'请求'}
            entries=[{'decision':decision,'response':response},
                     {'decision':{'decision_id':'d','commands':[{}]},'response':{}},
                     {'decision':decision,'response':dict(response,request_id='other',decision_id='fake')},
                     {'decision':decision,'response':dict(response,decision_id='other')}]
            path.write_text(''.join(json.dumps(e)+'\n' for e in entries))
            rows=read_reference_page(path)['rows']
            self.assertEqual(rows[0]['decision_id'],'решение/λ')
            self.assertEqual(rows[0]['commands'],1)
            for row in rows[1:]:
                self.assertNotIn('decision_id',row)
                self.assertNotIn('commands',row)

    def test_writer_respects_existing_file_size(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            with path.open('wb') as stream:
                stream.truncate(MAX_FILE-1)
            p=Progress('interop',path)
            p.record('decision',{'decision':{'decision_id':'d'}})
            self.assertEqual(path.stat().st_size,MAX_FILE-1)
            self.assertIsNone(p.path)

    def test_exact_id_search_is_bounded_and_resumable(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'progress.jsonl'
            prefix={'kind':'decision','source':'interop','decision_id':'unrelated'}
            target=dict(prefix,decision_id='цель/λ',request_id='запрос',episode_id='эпизод')
            rows=[prefix]*(MAX_SCAN_ROWS+1)+[target,dict(target,decision_id='цель/λ-extra')]
            original=''.join(json.dumps(row)+'\n' for row in rows).encode()
            path.write_bytes(original)
            first=read_page(path,identifier='цель/λ')
            self.assertEqual(first['rows'],[])
            self.assertEqual(first['scanned'],MAX_SCAN_ROWS)
            self.assertTrue(first['more'])
            second=read_page(path,first['cursor'],identifier='цель/λ')
            self.assertEqual([row['decision_id'] for row in second['rows']],['цель/λ'])
            self.assertFalse(second['more'])
            for identifier in ('запрос','эпизод'):
                page=read_page(path,first['cursor'],identifier=identifier)
                self.assertEqual(len(page['rows']),2)
            for identifier in ('','x'*129,'\n',1):
                with self.subTest(identifier=identifier),self.assertRaises(ValueError):
                    read_page(path,identifier=identifier)
            self.assertEqual(path.read_bytes(),original)

    def test_reference_search_does_not_match_uncorrelated_decisions(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reference.jsonl'
            decision={'decision_id':'d','episode_id':'e','request_id':'r','commands':[{}]}
            entries=[{'decision':decision,'response':{'episode_id':'e','request_id':'other'}},
                     {'decision':decision,'response':{'episode_id':'e','request_id':'r'}}]
            original=''.join(json.dumps(row)+'\n' for row in entries).encode()
            path.write_bytes(original)
            page=read_reference_page(path,identifier='d')
            self.assertEqual(page['scanned'],2)
            self.assertEqual(len(page['rows']),1)
            self.assertEqual(page['rows'][0]['request_id'],'r')
            self.assertEqual(path.read_bytes(),original)
