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
        evidence.record('decision',{'decision':decision})
        receipt={'status':'applied','agent_id':'carrier','action':'rescue'}
        evidence.record('step_response',{'episode_id':'e','request_id':'r','time':1,
            'observation':{'request_id':'r','targets':[{'id':'person','status':'rescued'}]},
            'command_results':[receipt]})
        summary=evidence.summary()
        self.assertEqual(summary['correlated_public_target_count'],1)
        self.assertEqual(summary['goal_status'],'unverified')
        self.assertEqual(summary['evacuated'],0)
