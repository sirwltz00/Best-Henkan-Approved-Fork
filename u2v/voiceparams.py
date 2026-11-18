"""VOCALOID4 voice-profile offsets and per-part control-curve validation.

Singer Editor profile values are signed offsets (-127..127) for BRE/BRI/CLE/GEN.
The sequence's note/track controls use a different coordinate system; keep these
schemas separate so a singer default is never confused with a time-varying curve.
"""

VOICE_PROFILE_DEFAULTS = {
    'breathiness': 0,
    'brightness': 0,
    'clearness': 0,
    'gender_factor': 0,
}

VOICE_PROFILE_VVD_KEYS = {
    'breathiness': 'Breathiness',
    'brightness': 'Brightness',
    'clearness': 'Clearness',
    'gender_factor': 'Gender Factor',
}

VOICE_PROFILE_INI_KEYS = {
    'breathiness': 'Breathiness',
    'brightness': 'Brightness',
    'clearness': 'Clearness',
    'gender_factor': 'GenderFactor',
}

VOICE_PROFILE_MIN = -127
VOICE_PROFILE_MAX = 127

# Default values and ranges for the classic VOCALOID4 part-level control curves.
# OPE and VEL intentionally are not here: Yamaha treats them as note properties.
V4_CONTROL_DEFAULTS = {
    'DYN': 64,
    'BRE': 0,
    'BRI': 64,
    'CLE': 0,
    'GEN': 64,
    'POR': 64,
    'XSY': 0,
    'GWL': 0,
    'PIT': 0,
    'PBS': 2,
}

V4_CONTROL_RANGES = {
    'DYN': (0, 127),
    'BRE': (0, 127),
    'BRI': (0, 127),
    'CLE': (0, 127),
    'GEN': (0, 127),
    'POR': (0, 127),
    'XSY': (0, 127),
    'GWL': (0, 127),
    'PIT': (-8192, 8191),
    'PBS': (0, 24),
}

NOTE_CONTROL_DEFAULTS = {'VEL': 64, 'OPE': 127}
NOTE_CONTROL_RANGES = {'VEL': (0, 127), 'OPE': (0, 127)}


class VoiceParameterError(ValueError):
    """A parameter name, value, or range is invalid for the selected VOCALOID4 control."""


def _strict_int(value, label):
    if isinstance(value, bool):
        raise VoiceParameterError('%s must be an integer' % label)
    try:
        # Avoid silently accepting 1.5 as 1 and avoid accepting strings like '2.0'.
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        raw = str(value).strip()
        if not raw or raw.lstrip('+-').isdigit() is False:
            raise ValueError
        return int(raw, 10)
    except (TypeError, ValueError):
        raise VoiceParameterError('%s must be an integer' % label) from None


def normalize_voice_profile(values=None):
    """Return all supported Singer Editor offsets, filling unspecified values with zero."""
    values = values or {}
    result = dict(VOICE_PROFILE_DEFAULTS)
    for key, value in values.items():
        key = str(key).strip().lower().replace('-', '_').replace(' ', '_')
        aliases = {
            'bre': 'breathiness', 'bri': 'brightness', 'cle': 'clearness', 'gen': 'gender_factor',
            'genderfactor': 'gender_factor',
        }
        key = aliases.get(key, key)
        if key not in result:
            raise VoiceParameterError('unsupported Singer Editor voice-profile parameter: %s' % key)
        number = _strict_int(value, key)
        if not VOICE_PROFILE_MIN <= number <= VOICE_PROFILE_MAX:
            raise VoiceParameterError('%s must be between %d and %d' % (
                key, VOICE_PROFILE_MIN, VOICE_PROFILE_MAX))
        result[key] = number
    return result


def normalize_control_changes(events, *, allow_growl=False, allow_xsy=False):
    """Validate and sort V4 ``<cc>`` events as ``(id, pos, value)`` tuples."""
    normalized = []
    for index, event in enumerate(events or []):
        if isinstance(event, dict):
            ident, pos, value = event.get('id'), event.get('pos'), event.get('value')
        else:
            try:
                ident, pos, value = event
            except (TypeError, ValueError):
                raise VoiceParameterError('control change #%d must have id, pos and value' % index) from None
        ident = str(ident).strip().upper()
        if ident not in V4_CONTROL_RANGES:
            raise VoiceParameterError('unsupported VOCALOID4 control curve: %s' % ident)
        if ident == 'GWL' and not allow_growl:
            raise VoiceParameterError('GWL requires explicit growl enablement and a VQM-capable voicebank')
        if ident == 'XSY' and not allow_xsy:
            raise VoiceParameterError('XSY requires a configured secondary singer')
        p = _strict_int(pos, '%s position' % ident)
        v = _strict_int(value, '%s value' % ident)
        if p < 0:
            raise VoiceParameterError('%s position must be zero or greater' % ident)
        low, high = V4_CONTROL_RANGES[ident]
        if not low <= v <= high:
            raise VoiceParameterError('%s value must be between %d and %d' % (ident, low, high))
        normalized.append((ident, p, v))
    return sorted(normalized, key=lambda item: (item[1], item[0]))


def normalize_note_controls(velocities=None, openings=None):
    """Validate note-level VEL/OPE maps keyed by note start tick."""
    result = {}
    for ident, values in (('VEL', velocities), ('OPE', openings)):
        checked = {}
        for pos, value in (values or {}).items():
            p = _strict_int(pos, '%s note position' % ident)
            v = _strict_int(value, '%s value' % ident)
            if p < 0:
                raise VoiceParameterError('%s note position must be zero or greater' % ident)
            if not NOTE_CONTROL_RANGES[ident][0] <= v <= NOTE_CONTROL_RANGES[ident][1]:
                raise VoiceParameterError('%s value must be between 0 and 127' % ident)
            checked[p] = v
        result[ident] = checked
    return result
