"""Custom extras and their transitions: an alias that starts with a vowel ("a R", "o -") keeps the transition
it was recorded with, several aliases can share one phoneme, ':end' ones only end a phrase, and lone ones get
joined to the vowels around them. Breaths, rr and hh are not built in any more.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert, dbtool_io  # noqa: E402
from u2v import phonemes as P, select as Sel  # noqa: E402

SR = 44100
QUIET = lambda *a: None  # noqa: E731


def make_bank(root):
    """Sung vowels a / i, their endings "a R" / "i R" (vowel, breathy tail, silence) and a cough."""
    bank = os.path.join(root, 'bank')
    os.makedirs(bank)
    rng = np.random.default_rng(5)
    sil = np.zeros(int(SR * 0.3), dtype=np.float32)

    def tone(sec, hz=220):
        t = np.arange(int(SR * sec)) / SR
        return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)

    def noise(sec, amp=0.15):
        return (amp * rng.standard_normal(int(SR * sec))).astype(np.float32)

    rows = []
    for i in range(6):
        audio.write_wav(os.path.join(bank, 'a%d.wav' % i), np.concatenate([sil, tone(1.0), sil]), SR)
        rows.append('a%d.wav=a,250,50,-1100,30,10\n' % i)
        audio.write_wav(os.path.join(bank, 'i%d.wav' % i), np.concatenate([sil, tone(1.0, 260), sil]), SR)
        rows.append('i%d.wav=i,250,50,-1100,30,10\n' % i)
    for v in ('a', 'i'):
        audio.write_wav(os.path.join(bank, '%sR.wav' % v), np.concatenate([sil, tone(0.6), noise(0.2), sil]), SR)
        rows.append('%sR.wav=%s R,200,50,-1200,30,10\n' % (v, v))
    # CVVC writes the ending "o -"; "a r" is the a -> r join, not an ending
    audio.write_wav(os.path.join(bank, 'oR.wav'), np.concatenate([sil, tone(0.6, 200), noise(0.2), sil]), SR)
    rows.append('oR.wav=o -,200,50,-1200,30,10\n')
    rows.append('a0.wav=a r,250,50,-1100,30,10\n')
    audio.write_wav(os.path.join(bank, 'cough.wav'), np.concatenate([sil, noise(0.25, 0.4), sil]), SR)
    rows.append('cough.wav=cough,200,50,-500,30,10\n')
    with open(os.path.join(bank, 'oto.ini'), 'w', encoding='utf-8') as f:
        f.writelines(rows)
    dpath = os.path.join(root, 'dict.txt')
    with open(dpath, 'w', encoding='latin-1') as f:
        f.write('phonetic_group(name="Vowels", voiced=true):\n\ta\n\ti\n\tM\n\te\n\to\n')
        f.write('phonetic_group(name="Silence", voiced=false):\n\tSil\n')
    return bank, dpath


class Builtins(unittest.TestCase):
    def test_only_vowel_endings_built_in(self):
        self.assertEqual(P.EXTRA_CHOICES, ['vend'])
        self.assertEqual(P.parse_extras_want('vend'), {'vend'})
        for gone in ('br1', 'brE', 'rr', 'hh'):
            with self.assertRaises(ValueError) as cm:
                P.parse_extras_want(gone)
            self.assertIn('custom extra', str(cm.exception))
        self.assertEqual(P.extra_alias('a R'), ['a', 'Sil'])
        for alias in ('息1', 'a R息', 'a R吸', '巻', 'rr', 'breath2'):
            self.assertIsNone(P.extra_alias(alias), alias)

    def test_end_flag(self):
        c = Sel.parse_custom_extras('a R=hh:Breaths:end; i R=hh:Breaths:end; cough=ch:UnvoicedPlosives')
        self.assertEqual(c.ends, {'hh'})
        self.assertEqual(c['a R'], ('hh', False, 'Breaths'))


class Converting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bank, self.dpath = make_bank(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_conv(self, extras, custom=None, name='out'):
        c = convert.Converter(self.bank, os.path.join(self.tmp.name, name), lang='ja', dict_path=self.dpath,
                              extras=extras, custom_extras=custom, log=QUIET)
        m = c.run()
        return c, {r[2] for r in m if r[1] == 'art'}, m

    def test_shared_phoneme_units(self):
        spec = 'a R=hh:Breaths:end; i R=hh:Breaths:end; o -=hh:Breaths:end'
        c, units, m = self.run_conv('all', spec)
        self.assertEqual(c.extras_found, ['hh'])
        rec = {(r[2], r[4]) for r in m if r[1] == 'art' and not r[8]}
        for u, alias in (('a hh', 'a R'), ('i hh', 'i R'), ('o hh', 'o -'), ('hh Sil', 'a R')):
            self.assertIn((u, alias), rec)
        # the vowel endings themselves stay: [a Sil] from "a R", [o Sil] from the CVVC "o -"
        self.assertIn(('a Sil', 'a R'), rec)
        self.assertIn(('o Sil', 'o -'), rec)
        self.assertFalse([u for u in units if u.startswith('hh ') and u != 'hh Sil'])   # :end
        d = dbtool_io.load_dictionary(os.path.join(c.out, 'dictionary.txt'))
        self.assertIn('hh', d)
        self.assertFalse(d.is_voiced('hh'))
        self.assertEqual(dbtool_io.validate_folder(c.out, c.dict_out), [])

    def test_leads_into_singing_without_end(self):
        _c, units, _m = self.run_conv('none', 'a R=hh:Breaths; i R=hh:Breaths')
        self.assertIn('a hh', units)
        self.assertIn('hh a', units)                        # no :end -> joins into vowels too

    def test_custom_extra_keeps_recorded_transition(self):
        # "a R" as a custom extra: the vowel it was recorded with stays, [a hx] is the real recording
        c, units, m = self.run_conv('all', custom='a R=hx:Whisper; i R=hx:Whisper')
        self.assertEqual(c._custom_extra(next(e for e in c.entries if e.alias == 'a R')), ['a', 'hx', 'Sil'])
        rec = {r[2] for r in m if r[1] == 'art' and not r[8]}
        for u in ('a hx', 'i hx', 'hx Sil'):
            self.assertIn(u, rec)
        self.assertFalse([u for u in units if 'hh' in u.split()])   # nothing built in any more
        self.assertEqual(dbtool_io.validate_folder(c.out, c.dict_out), [])

    def test_custom_extra_case(self):
        c = convert.Converter(self.bank, os.path.join(self.tmp.name, 'o'), lang='ja', dict_path=self.dpath,
                              custom_extras='a R=hx:Whisper; COUGH=ch:UnvoicedPlosives', log=QUIET)
        by = {e.alias: e for e in c.entries}
        self.assertIsNone(c._custom_extra(by['a r']))       # the a -> r join stays a join
        self.assertEqual(c._custom_extra(by['a R']), ['a', 'hx', 'Sil'])
        self.assertEqual(c._custom_extra(by['cough']), ['Sil', 'ch', 'Sil'])   # case-insensitive otherwise

    def test_lead_vowel(self):
        self.assertEqual(P.lead_vowel('a R'), 'a')
        self.assertEqual(P.lead_vowel('う laugh'), 'M')
        self.assertIsNone(P.lead_vowel('cough'))
        self.assertIsNone(P.lead_vowel('a'))

    def test_custom_extra_joins_vowels(self):
        c, units, m = self.run_conv('none', custom='cough=ch:UnvoicedPlosives')
        self.assertIn('Sil ch', units)
        self.assertIn('a ch', units)        # vowel -> custom
        self.assertIn('ch a', units)        # custom -> vowel
        self.assertEqual(dbtool_io.validate_folder(c.out, c.dict_out), [])
        # [a ch] really is the vowel, then the cough (it used to come out backwards: cough, then vowel)
        name = next(r[0] for r in m if r[2] == 'a ch')
        x, sr = audio.read_wav(os.path.join(c.out, name + '.wav'))
        segs = {p: (b, e) for p, b, e in dbtool_io.read_seg(os.path.join(c.out, name + '.seg'))[0]}

        def zcr(p):                         # crossings per second: a 220 Hz tone ~440, noise thousands
            b, e = segs[p]
            y = x[int(b * sr):int(e * sr)]
            return np.count_nonzero(np.diff(np.signbit(y))) / max(len(y) / sr, 1e-6)
        self.assertLess(zcr('a'), 1500)
        self.assertGreater(zcr('ch'), 3000)


class GrowlWavConversion(Converting):
    def test_imported_growl_wav_becomes_one_grw_stationary(self):
        import csv
        growl_path = os.path.join(self.tmp.name, 'growled-aaaa.wav')
        t = np.arange(int(SR * 1.5), dtype=np.float32) / SR
        # A deterministic, roughened tone is only a test fixture, not a growl synthesis algorithm.
        tone = 0.35 * np.sin(2 * np.pi * 180 * t) + 0.04 * np.sin(2 * np.pi * 730 * t)
        audio.write_wav(growl_path, tone.astype(np.float32), SR)
        out = os.path.join(self.tmp.name, 'growl-out')
        c = convert.Converter(self.bank, out, lang='ja', dict_path=self.dpath,
                              growl_wav=growl_path, log=QUIET)
        manifest = c.run()
        grw_rows = [row for row in manifest if row[1] == 'stationary' and row[2] == 'Grw']
        self.assertEqual(len(grw_rows), 1)
        self.assertIn('Grw', c.dict_out)
        grw_base = os.path.join(out, grw_rows[0][0])
        self.assertTrue(os.path.isfile(grw_base + '.wav'))
        self.assertTrue(os.path.isfile(grw_base + '.seg'))
        self.assertTrue(os.path.isfile(grw_base + '.trans'))
        self.assertTrue(os.path.isfile(grw_base + '.as0'))
        phonemes, directives = dbtool_io.read_trans(grw_base + '.trans')
        self.assertEqual(phonemes, ['Sil', 'Grw', 'Sil'])
        self.assertEqual(directives, [(['Grw'], 1)])
        segments, stationary, _revised = dbtool_io.read_seg(grw_base + '.seg')
        self.assertTrue(stationary)
        self.assertEqual([row[0] for row in segments], ['Sil', 'Grw', 'Sil'])
        with open(os.path.join(out, 'manifest.csv'), encoding='utf-8-sig') as f:
            written = list(csv.DictReader(f))
        self.assertEqual(sum(1 for row in written if row['unit'] == 'Grw'), 1)

    def test_commercial_vqm_folder_imports_all_numbered_samples_and_preserves_indexes(self):
        import csv
        import json
        root = os.path.join(self.tmp.name, 'CommercialSinger')
        vqm = os.path.join(root, 'vqm')
        for folder in ('art', 'sta', 'vqm'):
            os.makedirs(os.path.join(root, folder), exist_ok=True)
        t = np.arange(int(SR * 1.2), dtype=np.float32) / SR
        for index, hz in ((0, 170), (3, 235)):
            tone = 0.28 * np.sin(2 * np.pi * hz * t) + 0.035 * np.sin(2 * np.pi * 690 * t)
            audio.write_wav(os.path.join(vqm, 'vqm_%d.wav' % index), tone.astype(np.float32), SR)
        out = os.path.join(self.tmp.name, 'commercial-vqm-out')
        c = convert.Converter(self.bank, out, lang='ja', dict_path=self.dpath,
                              growl_wav=root, log=QUIET)
        manifest = c.run()
        rows = [row for row in manifest if row[1] == 'stationary' and row[2] == 'Grw']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(os.path.isfile(os.path.join(out, row[0] + '.wav')) for row in rows))
        self.assertEqual([row[4] for row in rows], ['Imported VQM sample 0', 'Imported VQM sample 3'])
        with open(os.path.join(out, 'vqm_samples.json'), encoding='utf-8') as f:
            mapping = json.load(f)
        self.assertEqual([sample['index'] for sample in mapping['samples']], [0, 3])
        self.assertEqual([sample['source_file'] for sample in mapping['samples']],
                         ['vqm_0.wav', 'vqm_3.wav'])
        with open(os.path.join(out, 'manifest.csv'), encoding='utf-8-sig') as f:
            written = list(csv.DictReader(f))
        self.assertEqual(sum(1 for row in written if row['unit'] == 'Grw'), 2)

    def test_growl_wav_requires_a_real_wav_and_sustained_duration(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            convert.Converter(self.bank, os.path.join(self.tmp.name, 'missing'), lang='ja',
                              dict_path=self.dpath, growl_wav=os.path.join(self.tmp.name, 'absent.wav'),
                              log=QUIET)
        short = os.path.join(self.tmp.name, 'short.wav')
        audio.write_wav(short, np.zeros(int(SR * 0.5), dtype=np.float32), SR)
        with self.assertRaisesRegex(ValueError, '0.50'):
            convert.Converter(self.bank, os.path.join(self.tmp.name, 'short-out'), lang='ja',
                              dict_path=self.dpath, growl_wav=short, log=QUIET)


if __name__ == '__main__':
    unittest.main()
