"""Generate paired baseline/parameter-test VSQX4 files for a Henkan dev-kit voice.

Usage:
    python tools/parameter_probe.py <ProgramChange> <output-folder> [--editor-dir <copy>] [--song test]

Open the generated VSQX files in VOCALOID4 Editor and render/listen to the first (baseline)
phrase and second (changed-parameter) phrase. This diagnoses editor/voicebank response; it does
not alter or synthesize the voicebank database itself.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.render import INSTALLED, SONGS, SONGS_EN, TPQ, build_notes, read_slots, write_vsqx  # noqa: E402
from u2v.voiceparams import V4_CONTROL_DEFAULTS  # noqa: E402


def _shift_notes(notes, offset):
    return [(t + offset, dur, pitch, lyric, phonemes)
            for t, dur, pitch, lyric, phonemes in notes]


def generate_probe_set(pc, out_dir, *, editor_dir=INSTALLED, song='test', pitch=64,
                       include_growl=False):
    """Write and return paths for baseline-vs-changed V4 parameter probes."""
    if song not in set(SONGS) | set(SONGS_EN):
        raise ValueError('unknown test song: %s' % song)
    slot_matches = [slot for slot in read_slots(editor_dir) if slot[0] == pc]
    if not slot_matches:
        raise ValueError('ProgramChange %d is not present in %s DB_Dev.ini' % (pc, editor_dir))
    slot = slot_matches[0]
    os.makedirs(out_dir, exist_ok=True)
    # Remove old generated files only by filename pattern owned by this tool.
    for filename in os.listdir(out_dir):
        if filename.startswith('henkan_param_') and filename.lower().endswith('.vsqx'):
            os.remove(os.path.join(out_dir, filename))
    base_notes, end = build_notes(song, pitch)
    cases = [
        ('BRE', 110, None, None),
        ('BRI', 110, None, None),
        ('CLE', 110, None, None),
        ('GEN', 110, None, None),
        ('DYN', 110, None, None),
        ('POR', 0, None, None),
        ('PIT', 8191, None, None),
        ('PBS', 12, None, None),
        ('VEL', None, 'high', None),
        ('OPE', None, None, 'low'),
    ]
    if include_growl:
        cases.append(('GWL', 127, None, None))

    generated = []
    for index, (param, value, velocity_mode, opening_mode) in enumerate(cases, 1):
        second_offset = end + TPQ
        second_notes = _shift_notes(base_notes, second_offset)
        combined = list(base_notes) + second_notes
        second_note_start = second_offset + TPQ * 4
        cc = []
        if param in V4_CONTROL_DEFAULTS:
            cc = [(param, 0, V4_CONTROL_DEFAULTS[param]), (param, second_note_start, value)]
        if param == 'PIT':
            # Make pitch bending conspicuous without changing the baseline phrase.
            cc.extend([('PBS', second_note_start, 12),
                       ('PIT', second_note_start + TPQ * 2, -8192)])
        elif param == 'PBS':
            # Keep PIT active so a sensitivity change is actually audible.
            cc.extend([('PIT', second_note_start, 4096),
                       ('PIT', second_note_start + TPQ * 2, -4096)])
        velocity_map = ({note[0]: 112 for note in second_notes}
                        if velocity_mode == 'high' else {})
        opening_map = ({note[0]: 0 for note in second_notes}
                       if opening_mode == 'low' else {})
        portamento_map = ({note[0]: True for note in second_notes} if param == 'POR' else {})
        path = os.path.join(out_dir, 'henkan_param_%02d_%s.vsqx' % (index, param.lower()))
        write_vsqx(path, combined, second_offset + end, pc=slot[0], comp_id=slot[1], name=slot[4],
                   control_changes=cc, note_velocities=velocity_map, note_openings=opening_map,
                   note_portamentos=portamento_map, allow_growl=include_growl)
        generated.append(path)
    return generated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pc', type=int, help='ProgramChange number from DB_Dev.ini')
    parser.add_argument('out_dir', help='folder in which the V4 VSQX probe projects will be written')
    parser.add_argument('--editor-dir', default=INSTALLED, help='VOCALOID4 Developer editor folder')
    parser.add_argument('--song', default='test', choices=sorted(set(SONGS) | set(SONGS_EN)))
    parser.add_argument('--pitch', type=int, default=64)
    parser.add_argument('--include-growl', action='store_true',
                        help='include GWL test; only meaningful with a VQM-enabled compatible V4 bank')
    args = parser.parse_args(argv)
    try:
        paths = generate_probe_set(args.pc, args.out_dir, editor_dir=args.editor_dir,
                                   song=args.song, pitch=args.pitch, include_growl=args.include_growl)
    except (OSError, ValueError, IndexError) as ex:
        parser.error(str(ex))
    print('Generated %d VOCALOID4 parameter probe projects:' % len(paths))
    for path in paths:
        print('  %s' % path)
    print('Each project contains the unmodified baseline phrase first and the parameter test second.')
    if not args.include_growl:
        print('GWL omitted. Use --include-growl only with a voicebank containing validated native VQM data.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
