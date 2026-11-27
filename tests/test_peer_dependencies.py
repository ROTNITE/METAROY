from copy import deepcopy
from unittest import TestCase

from tools.peer_dependencies import validate_manifests, dependency_manifest
from tools.run_interop import Evidence


class DependencyBoundary(TestCase):
    def setUp(self):
        self.manifest={'dependencies':{'vite':'^5.4.0'}}
        self.lock={'lockfileVersion':3,'packages':{'':self.manifest,
            'node_modules/vite':{'version':'5.4.0','resolved':'https://registry.npmjs.org/vite/-/vite-5.4.0.tgz',
                                 'integrity':'sha512-ZGVtbw=='}}}

    def test_dependency_only_manifest_does_not_copy_scripts_or_config(self):
        manifest=dependency_manifest({'name':'peer','version':'9','dependencies':{'vite':'^5.4.0'},
                                     'scripts':{'postinstall':'dangerous'},'config':{'token':'secret'}})
        self.assertEqual(manifest['dependencies'],{'vite':'^5.4.0'})
        self.assertNotIn('scripts',manifest)
        self.assertNotIn('config',manifest)

    def test_locked_registry_dependency(self):
        self.assertEqual(validate_manifests(self.manifest,self.lock),1)

    def test_untrusted_or_unlocked_sources_rejected(self):
        for source in ('file:../host','https://example.invalid/vite.tgz',
                       'https://registry.npmjs.org@example.invalid/vite.tgz',
                       'http://registry.npmjs.org/vite.tgz'):
            lock=deepcopy(self.lock)
            lock['packages']['node_modules/vite']['resolved']=source
            with self.subTest(source=source),self.assertRaises(ValueError):
                validate_manifests(self.manifest,lock)
        for spec in ('git+https://example.invalid/x','file:../local','npm:other@1'):
            with self.subTest(spec=spec),self.assertRaises(ValueError):
                validate_manifests({'dependencies':{'vite':spec}},self.lock)

    def test_missing_integrity_or_link_rejected(self):
        for change in ({'integrity':None},{'link':True},{'inBundle':True}):
            lock=deepcopy(self.lock)
            lock['packages']['node_modules/vite'].update(change)
            with self.assertRaises(ValueError):
                validate_manifests(self.manifest,lock)


class EvidenceTiers(TestCase):
    def test_bare_ack_and_public_transition_are_not_physical_truth(self):
        evidence=Evidence()
        decision={'episode_id':'e','decision_id':'d','request_id':'r','time':0,
                  'commands':[{'agent_id':'carrier','action':'rescue','parameters':{'target_id':'person'}}]}
        evidence.record('observation',{'episode_id':'e','time':0,'targets':[{'id':'person','status':'confirmed'}]})
        evidence.record('decision',{'decision':decision})
        receipt={'status':'applied','agent_id':'carrier','action':'rescue'}
        evidence.record('step_response',{'episode_id':'e','request_id':'r','time':1,
            'observation':{'request_id':'r','targets':[{'id':'person','status':'rescued'}]},
            'command_results':[receipt]})
        summary=evidence.summary()
        self.assertEqual(summary['correlated_public_target_count'],1)
        self.assertEqual(summary['goal_status'],'unverified')
        self.assertEqual(summary['evacuated'],0)

    def test_existing_terminal_target_and_replay_cannot_create_new_evidence(self):
        evidence=Evidence()
        initial={'episode_id':'e','time':0,'targets':[{'id':'person','status':'rescued'}]}
        evidence.record('observation',initial)
        d={'episode_id':'e','decision_id':'d','request_id':'r','time':0,
           'commands':[{'agent_id':'carrier','action':'rescue','parameters':{'target_id':'person'}}]}
        evidence.record('decision',{'decision':d})
        step={'episode_id':'e','request_id':'r','time':1,
              'observation':{**initial,'time':1,'request_id':'r'},
              'command_results':[{'status':'applied','agent_id':'carrier','action':'rescue'}]}
        for _ in range(3):
            evidence.record('step_response',step)
            evidence.record('observation',{**initial,'targets':[{'id':'person','status':'confirmed'}]})
        self.assertEqual(evidence.summary()['correlated_public_target_count'],0)

    def test_request_lookup_handles_delayed_receipts_and_episode_reuse(self):
        evidence=Evidence()
        for episode in ('e1','e2'):
            evidence.record('decision',{'decision':{'episode_id':episode,'decision_id':'d','request_id':'r','time':0,
                'commands':[{'agent_id':episode,'action':'handover','parameters':{}}]}})
        receipt={'decision_id':'d','command_index':0,'agent_id':'e1','action':'handover',
                 'validation_status':'accepted','delivery_status':'delivered','execution_status':'completed'}
        evidence.record('step_response',{'episode_id':'e2','command_results':[receipt]})
        self.assertEqual(evidence.summary()['confirmed_command_receipts'],[])
        evidence.record('step_response',{'episode_id':'e1','command_results':[receipt]})
        self.assertEqual(evidence.summary()['confirmed_command_receipts'],[receipt])
