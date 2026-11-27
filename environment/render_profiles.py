"""Explicit visual-only launch options. Never modify simulation settings."""

RENDER_PROFILES = {
    'balanced': ('sg.GlobalIlluminationQuality 2', 'sg.ReflectionQuality 1',
                 'sg.ShadowQuality 2', 'sg.EffectsQuality 2',
                 'r.ScreenPercentage 75', 'r.VSync 0'),
    'economy': ('sg.GlobalIlluminationQuality 1', 'sg.ReflectionQuality 0',
                'sg.ShadowQuality 1', 'sg.EffectsQuality 1',
                'r.ScreenPercentage 60', 'r.VSync 0'),
}
DEMO_COMMANDS = ('sg.GlobalIlluminationQuality 2', 'sg.ReflectionQuality 1',
                 'sg.ShadowQuality 2', 'r.ScreenPercentage 75', 'r.VSync 0')


def render_commands(*, profile=None, demo_render=False, headless=False,
                    visible=False, fps_cap=None):
    """Build a single ExecCmds option, or None for the legacy default.

    The FPS limit is an interactive convenience, not a profiling option.
    Keep the historical demo preset byte-for-byte identical.
    """
    if profile is not None and profile not in RENDER_PROFILES:
        raise ValueError('Unknown render profile')
    if profile is not None and demo_render:
        raise ValueError('Choose --render-profile or --demo-render, not both')
    if profile is not None and headless:
        raise ValueError('Render profiles require GPU rendering')
    if fps_cap is not None:
        if type(fps_cap) is not int or not 15 <= fps_cap <= 120:
            raise ValueError('FPS cap must be an integer from 15 to 120')
        if headless or not visible:
            raise ValueError('FPS cap requires visible rendering')
    if headless:
        return None
    commands = list(RENDER_PROFILES[profile] if profile is not None
                    else DEMO_COMMANDS if demo_render else ())
    if fps_cap is not None:
        commands.append(f't.MaxFPS {fps_cap}')
    return '-ExecCmds=' + ','.join(commands) if commands else None
