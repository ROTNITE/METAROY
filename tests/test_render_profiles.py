import unittest

from environment.render_profiles import RENDER_PROFILES, render_commands


class RenderProfileTests(unittest.TestCase):
    def test_legacy_defaults_and_demo_unchanged(self):
        self.assertIsNone(render_commands())
        self.assertIsNone(render_commands(headless=True, demo_render=True))
        self.assertEqual(render_commands(demo_render=True),
                         '-ExecCmds=sg.GlobalIlluminationQuality 2,sg.ReflectionQuality 1,sg.ShadowQuality 2,r.ScreenPercentage 75,r.VSync 0')

    def test_profiles_only_emit_visual_scalability_settings(self):
        allowed = {'sg.GlobalIlluminationQuality', 'sg.ReflectionQuality',
                   'sg.ShadowQuality', 'sg.EffectsQuality', 'r.ScreenPercentage', 'r.VSync'}
        for name in RENDER_PROFILES:
            option = render_commands(profile=name)
            self.assertTrue(option.startswith('-ExecCmds='))
            settings = option.split('=', 1)[1].split(',')
            self.assertEqual({s.split()[0] for s in settings}, allowed)
            self.assertNotIn('t.MaxFPS', option)
        self.assertIn('r.ScreenPercentage 60', render_commands(profile='economy'))

    def test_cap_uses_single_exec_option(self):
        option = render_commands(profile='economy', visible=True, fps_cap=45)
        self.assertEqual(option.count('-ExecCmds='), 1)
        self.assertTrue(option.endswith(',t.MaxFPS 45'))
        self.assertEqual(render_commands(visible=True, fps_cap=30), '-ExecCmds=t.MaxFPS 30')

    def test_incompatible_inputs_rejected(self):
        cases = (
            {'profile': 'unknown'}, {'profile': 'economy', 'demo_render': True},
            {'profile': 'economy', 'headless': True},
            {'fps_cap': 30}, {'fps_cap': 30, 'visible': True, 'headless': True},
            {'fps_cap': True, 'visible': True}, {'fps_cap': 14, 'visible': True},
            {'fps_cap': 121, 'visible': True}, {'fps_cap': 30.5, 'visible': True},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                render_commands(**kwargs)
