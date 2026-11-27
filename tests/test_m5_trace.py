import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from controller.m5_trace import Trace, MAX_DETAILS, MAX_SCAN, read_trace


class TraceTests(unittest.TestCase):
    def test_delayed_receipt_and_event_keep_own_decision_scope(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            trace=Trace('interop',path)
            trace.record('decision',{'decision':{'episode_id':'e','request_id':'r','decision_id':'d-old',
                'commands':[{'agent_id':'a','action':'handover','parameters':{'secret':'never copy'}}]}})
            trace.record('step_response',{'episode_id':'e','request_id':'new','observation':{'secret':'private'},
                'command_results':[{'decision_id':'d-old','command_index':0,'agent_id':'a','action':'handover',
                    'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'},
                    {'decision_id':'d-new','command_index':0,'agent_id':'b','action':'move'},
                    {'agent_id':'a','action':'handover','status':'applied'}],
                'events':[{'id':'out','type':'target_state_changed','source':'a','targets':['p'],
                    'parameters':{'decision_id':'d-old','command_index':0,'evacuation_state':'evacuated',
                                  'secret':'never copy'}}]})
            before=path.read_bytes()
            page=read_trace(path,query='d-old')
            self.assertEqual([r['kind'] for r in page['rows']],['decision','step_response'])
            self.assertEqual(page['rows'][0]['details'][0]['command_index'],0)
            response=page['rows'][1]
            self.assertNotIn('decision_id',response)
            self.assertEqual([r['type'] for r in response['details']],['receipt','reported_event'])
            self.assertEqual(response['request_id'],'new')
            self.assertEqual(response['details'][0]['execution_status'],'completed')
            self.assertEqual(response['details'][1]['reported_state'],'evacuated')
            self.assertNotIn(b'secret',before)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(read_trace(path,query='new')['rows'][0]['details']),4)

    def test_bounded_scan_partial_record_and_omissions(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            trace=Trace('metarescue',path)
            for _ in range(MAX_SCAN+1):
                trace.record('observation',{'episode_id':'e','request_id':'other'})
            trace.record('decision',{'decision':{'decision_id':'цель',
                'commands':[{'agent_id':'a','action':'move'}]*(MAX_DETAILS+3)}})
            with path.open('ab') as stream:
                stream.write(b'{"kind":')
            page=read_trace(path,query='цель')
            self.assertEqual(page['rows'],[])
            self.assertEqual(page['scanned'],MAX_SCAN)
            self.assertTrue(page['more'])
            page=read_trace(path,page['cursor'],query='цель')
            self.assertEqual(len(page['rows'][0]['details']),MAX_DETAILS)
            self.assertEqual(page['rows'][0]['omitted'],3)
            self.assertTrue(page['more'])
            self.assertEqual(read_trace(path,page['cursor'])['cursor'],page['cursor'])
            with self.assertRaises(ValueError):
                read_trace(path,1)
            with self.assertRaises(ValueError):
                read_trace(path,limit=11)

    def test_reference_projection_never_invents_ack_correlation(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'reference.jsonl'
            raw={'decision':{'decision_id':'d','episode_id':'e','request_id':'r',
                'commands':[{'agent_id':'a','action':'move'}]},
                'response':{'episode_id':'e','request_id':'other','command_results':[
                    {'agent_id':'a','action':'move','status':'applied'}]}}
            before=(json.dumps(raw)+'\n').encode()
            path.write_bytes(before)
            page=read_trace(path,reference=True,query='d')
            self.assertEqual([r['kind'] for r in page['rows']],['decision'])
            self.assertEqual(len(read_trace(path,reference=True)['rows']),2)
            self.assertEqual(path.read_bytes(),before)

    def test_storage_cap_marker_and_unavailable_writer(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            trace=Trace('interop',path)
            with patch('controller.m5_trace.MAX_FILE',256):
                for _ in range(10):
                    trace.record('observation',{'request_id':'x'*70})
                self.assertLessEqual(path.stat().st_size,256)
                self.assertIsNone(trace.path)
                page=read_trace(path,query='absent')
                self.assertTrue(page['capped'])
                self.assertFalse(page['more'])
            bad=Trace('interop',Path(folder)/'missing'/'trace.jsonl')
            bad.record('observation',{'request_id':'r'})
            self.assertIsNone(bad.path)

    def test_corrupt_storage_does_not_expose_extra_fields(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            row={'kind':'decision','source':'metarescue','decision_id':'d','secret':'private',
                 'details':[{'type':'command','command_index':0,'agent_id':'a','action':'move','secret':'private'}]}
            path.write_text(json.dumps(row)+'\n')
            page=read_trace(path)
            self.assertNotIn('secret',json.dumps(page))
