"""Triphone mode: a VCV recording can also go into the DB whole, as one [V C V] unit.

    python -I -m unittest discover -s tests -v
"""
import csv
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert, dbtool_build as B, dbtool_io as IO  # noqa: E402

SR = 44100
QUIET = lambda *a: None  # noqa: E731
BURST = 0.89            # where the b's release starts in the recording


def make_bank(root, include_vcv=True):
    """One VCV "a ba" (vowel, closure, burst, vowel) and one CV "ba"."""
    bank = os.path.join(root, 'bank')
    os.makedirs(bank)
    rng = np.random.default_rng(3)

    def tone(sec, hz=220):
        t = np.arange(int(SR * sec)) / SR
        return (0.5 * np.sin(2 * np.pi * hz * t) + 0.2 * np.sin(4 * np.pi * hz * t)).astype(np.float32)

    sil = lambda s: (1e-5 * rng.standard_normal(int(SR * s))).astype(np.float32)  # noqa: E731
    burst = (0.4 * rng.standard_normal(int(SR * 0.03))).astype(np.float32)
    vcv = np.concatenate([sil(0.3), tone(0.5), sil(0.06), burst, tone(0.6), sil(0.3)])
    if include_vcv:
        audio.write_wav(os.path.join(bank, 'aka.wav'), vcv, SR)
    cv = np.concatenate([sil(0.3), burst, tone(0.6), sil(0.3)])
    audio.write_wav(os.path.join(bank, 'ka.wav'), cv, SR)
    with open(os.path.join(bank, 'oto.ini'), 'w', encoding='utf-8') as f:
        if include_vcv:
            f.write('aka.wav=a ba,500,420,-900,390,120\n')
        f.write('ka.wav=ba,250,120,-500,80,30\n')
    return bank




def make_z_bank(root):
    """A Japanese VCV recording whose middle z has a short accidental stop closure."""
    bank = os.path.join(root, 'bank')
    os.makedirs(bank)

    def tone(sec, amplitude=0.3, hz=220):
        t = np.arange(round(sec * SR)) / SR
        return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)

    def noise(sec, amplitude, seed=3):
        rng = np.random.default_rng(seed)
        return (amplitude * rng.standard_normal(round(sec * SR))).astype(np.float32)

    fricative = tone(0.12, 0.20) + noise(0.12, 0.035, 4)
    x = np.concatenate((noise(0.30, 0.00001), tone(0.50), noise(0.04, 0.0002),
                        noise(0.004, 0.25), fricative, tone(0.35), noise(0.30, 0.00001)))
    audio.write_wav(os.path.join(bank, 'aza.wav'), x, SR)
    with open(os.path.join(bank, 'oto.ini'), 'w', encoding='utf-8') as f:
        f.write('aza.wav=a ざ,500,420,-900,390,120\n')
    return bank


def convert_bank(triphones, include_vcv=True):
    root = tempfile.TemporaryDirectory()
    bank, out = make_bank(root.name, include_vcv), os.path.join(root.name, 'out')
    c = convert.Converter(bank, out, 'ja', None, log=QUIET, fill=False, derive=False, triphones=triphones)
    c.run()
    return root, out


def units(out):
    got = {}
    for fn in sorted(os.listdir(out)):
        if fn.endswith('.trans'):
            got[fn[:-6]] = IO.read_trans(os.path.join(out, fn))
    return got


class Convert(unittest.TestCase):
    def test_diphone_mode_is_the_default_and_has_no_triphones(self):
        root, out = convert_bank(False)
        with root:
            self.assertFalse([u for _, dirs in units(out).values() for u, _ in dirs if len(u) > 2])

    def test_vcv_is_kept_whole_next_to_its_diphones(self):
        root, out = convert_bank(True)
        with root:
            got = [(n, phs, dirs) for n, (phs, dirs) in units(out).items() if any(len(u) == 3 for u, _ in dirs)]
            self.assertEqual(len(got), 1)
            name, phs, dirs = got[0]
            self.assertEqual(phs, ['a', 'b', 'a'])
            self.assertEqual(dirs, [(['a', 'b', 'a'], 1)])
            art = IO.read_as(os.path.join(out, name + '.as0'))
            self.assertEqual(art.phns, ['a', 'b', 'a'])
            self.assertEqual(art.voiced, [True, True, True, True])
            self.assertEqual(IO.validate_folder(out), [])
            two = {tuple(u) for _, dirs2 in units(out).values() for u, _ in dirs2 if len(u) == 2}
            self.assertLessEqual({('a', 'b'), ('b', 'a')}, two)

    def test_japanese_z_vcv_keeps_dz_label_while_the_medial_audio_is_fricative(self):
        with tempfile.TemporaryDirectory() as root:
            bank = make_z_bank(root)
            out = os.path.join(root, 'out')
            c = convert.Converter(bank, out, 'ja', None, log=QUIET, fill=False,
                                  derive=False, triphones=True)
            c.run()
            found = []
            for name, (phs, dirs) in units(out).items():
                if any(len(unit) == 3 for unit, _count in dirs):
                    found.append((name, phs, dirs))
            self.assertEqual(len(found), 1)
            name, phs, dirs = found[0]
            self.assertEqual(phs, ['a', 'dz', 'a'])
            self.assertEqual(dirs, [(['a', 'dz', 'a'], 1)])
            segs = IO.read_seg(os.path.join(out, name + '.seg'))[0]
            self.assertEqual([p for p, _b, _e in segs], ['a', 'dz', 'a'])

    def test_vcv_alias_does_not_need_both_diphones_to_be_candidates(self):
        """The oto's whole VCV is independent of the diphone candidate list."""
        with tempfile.TemporaryDirectory() as root:
            bank = make_bank(root)
            c = convert.Converter(bank, os.path.join(root, 'out'), 'ja', None,
                                  log=QUIET, fill=False, derive=False, triphones=True)
            entry = next(e for e in c.entries if e.alias == 'a ba')
            phs, why = c.map_phonemes(entry)
            self.assertIsNone(why)
            clip, why = c.segment(entry, phs)
            self.assertIsNone(why)
            c.typical = {}
            takes = c._triphone_takes([clip])
            self.assertEqual([take.phns for take in takes], [['a', 'b', 'a']])

    def test_missing_vcv_alias_falls_back_to_normal_diphones(self):
        root, out = convert_bank(True, include_vcv=False)
        with root:
            self.assertFalse([u for _, dirs in units(out).values() for u, _ in dirs if len(u) > 2])
            two = {tuple(u) for _, dirs in units(out).values() for u, _ in dirs if len(u) == 2}
            self.assertIn(('b', 'a'), two)

    def test_the_release_stays_in_the_triphone(self):
        """[a k] alone has its release muted; the whole unit must keep it."""
        root, out = convert_bank(True)
        with root:
            name = next(n for n, (_p, dirs) in units(out).items() if any(len(u) == 3 for u, _ in dirs))
            segs = IO.read_seg(os.path.join(out, name + '.seg'))[0]
            y, sr = audio.read_wav(os.path.join(out, name + '.wav'))
            b, e = segs[1][1], segs[1][2]
            self.assertGreater(audio.rms_db(y[int(b * sr):int(min(e + 0.02, len(y) / sr) * sr)]), -35)

    def test_only_recorded_vowel_consonant_vowel(self):
        root, out = convert_bank(True)
        with root:
            for _n, (phs, dirs) in units(out).items():
                for u, _ in dirs:
                    if len(u) == 3:
                        self.assertNotIn('Sil', u)

    def test_manifest_and_report_name_them(self):
        root, out = convert_bank(True)
        with root:
            with open(os.path.join(out, 'manifest.csv'), encoding='utf-8-sig', newline='') as f:
                rows = [r for r in csv.DictReader(f) if r['derived'] == 'triphone']
            self.assertEqual([r['unit'] for r in rows], ['a b a'])
            self.assertEqual(rows[0]['kind'], 'art')
            self.assertIn('triphones: 1', open(os.path.join(out, 'report.txt'), encoding='utf-8').read())


class BuiltDatabase(unittest.TestCase):
    """The DBTool files a triphone under a/^k/a; everything else speaks of a k a."""

    def test_progress_finds_nested_units(self):
        with tempfile.TemporaryDirectory() as db:
            base = os.path.join(db, 'voice', 'articulation')
            os.makedirs(os.path.join(base, 'a', '%94%k'))
            for rel in ('a/%94%k/a', 'a/k'):
                with open(os.path.join(base, *rel.split('/')), 'wb') as f:
                    f.write(b'x' * 8)
            t = B.DBTool.__new__(B.DBTool)
            t.db_path = db
            self.assertEqual(set(t._db_units('articulation')), {('a', 'k', 'a'), ('a', 'k')})

    def test_expected_units_includes_triphones(self):
        with tempfile.TemporaryDirectory() as seg:
            IO.write_as(os.path.join(seg, 'x.as0'),
                        IO.ArtSeg(['a', 'k', 'a'], 0, 44100, [0.1, 0.2, 0.25, 0.3, 0.4], [True, False, False, True]))
            self.assertEqual(B.expected_units(seg), ({('a', 'k', 'a')}, set()))

    def test_pitch_check_reads_nested_units(self):
        with tempfile.TemporaryDirectory() as db:
            d = os.path.join(db, 'voice', 'articulation', 'a', '%94%k')
            os.makedirs(d)
            frame = b'FRM2' + b'\0' * 24 + struct.pack('<If', 1, 220.0)
            with open(os.path.join(d, 'a'), 'wb') as f:
                f.write(frame * 3)
            self.assertEqual(IO.db_pitches(db), {('art', 'a', 'k', 'a'): 220.0})

    def test_a_phoneme_named_with_a_caret_stays_in_a_diphone(self):
        self.assertEqual(IO.art_key(['^', 'a']), ('^', 'a'))
        self.assertEqual(IO.art_key(['a', '^k', 'a']), ('a', 'k', 'a'))
        self.assertEqual(IO.art_key(['a', '^b', 'a']), ('a', 'b', 'a'))


if __name__ == '__main__':
    unittest.main()
