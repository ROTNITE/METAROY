"""Pure source transformation shared by the material authoring and tests."""
def remembered_source(code):
    marker = 'float3 Delta = Surface - Camera;'
    if code.count(marker) != 1:
        raise ValueError('Smoke clipping marker changed')
    return code.replace(marker,
        'Lo=max(Lo,ClipMin-Source); Hi=min(Hi,ClipMax-Source);\n'
        'if(any(Hi<=Lo))return 0;\n' + marker)
