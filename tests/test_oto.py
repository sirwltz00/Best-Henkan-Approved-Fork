"""Regression tests for reading UTAU banks and mapping aliases (no audio needed).

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import oto, phonemes as P  # noqa: E402


def make_bank(root, folders, prefix_map=None):
    """folders: {relative folder: [(wav name, alias), ...]}; wavs are empty files."""
    for folder, rows in folders.items():
        d = os.path.join(root, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
            for wav, alias in rows:
                f.write('%s=%s,100,50,-200,30,10\n' % (wav, alias))
                open(os.path.join(d, wav), 'wb').close()
    if prefix_map:
        with open(os.path.join(root, 'prefix.map'), 'w', encoding='utf-8') as f:
            f.write(prefix_map)


class ReadBank(unittest.TestCase):
    def read(self, folders, prefix_map=None):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root, folders, prefix_map)
            return oto.read_bank(root)

    def test_folder_pitch_suffix(self):
        es = self.read({'C3': [('a.wav', '- かC3'), ('b.wav', 'a かC3'), ('c.wav', 'かC3')]})
        self.assertEqual(sorted(e.alias for e in es), ['- か', 'a か', 'か'])
        self.assertTrue(all(e.pitch == 'C3' for e in es))

    def test_colour_and_pitch(self):
        es = self.read({'SoftC3': [('a.wav', 'かC3SF'), ('b.wav', 'a かC3SF')],
                        'StandardA#3': [('a.wav', 'かA#3'), ('b.wav', 'a かA#3')]})
        by = {e.group: e for e in es}
        soft = [e for e in es if e.group.startswith('SoftC3')][0]
        std = [e for e in es if e.group.startswith('StandardA#3')][0]
        self.assertEqual((soft.pitch, std.pitch), ('C3', 'A#3'))
        self.assertNotEqual(soft.color, std.color)
        self.assertTrue(by)

    def test_kanji_tag_stripped_kana_kept(self):
        es = self.read({'weak': [('a.wav', '- か弱'), ('b.wav', 'a か弱'), ('c.wav', 'か弱')]})
        self.assertEqual(sorted(e.alias for e in es), ['- か', 'a か', 'か'])
        es = self.read({'v': [('a.wav', 'a あ'), ('b.wav', 'i あ'), ('c.wav', 'u あ')]})
        self.assertEqual(sorted(e.alias for e in es), ['a あ', 'i あ', 'u あ'])

    def test_common_lowercase_phoneme_tail_is_not_stripped_as_a_group(self):
        es = self.read({'main': [('a.wav', '- ka'), ('b.wav', 'a ka'), ('c.wav', 'i ka')]})
        self.assertEqual(sorted(e.alias for e in es), ['- ka', 'a ka', 'i ka'])

    def test_prefix_map_suffix_is_pitch(self):
        es = self.read({'A4': [('a.wav', 'かF'), ('b.wav', 'a かF')], 'G4': [('a.wav', 'か'), ('b.wav', 'a か')]},
                       prefix_map='C5\t\tF\nC4\t\t\n')
        self.assertTrue(all('F' not in e.color for e in es), [e.color for e in es])
        self.assertEqual(len({e.color for e in es}), 1)

    def test_manual_group_assignments_are_scoped_to_full_source_group(self):
        es = self.read({
            'Natural': [('a.wav', '- か_0'), ('b.wav', 'a き_0'), ('c.wav', 'i く_0')],
            'Power': [('a.wav', '- か_0'), ('b.wav', 'a き_0'), ('c.wav', 'i く_0')],
        }, prefix_map='C4\t\t_0\n')
        oto.apply_group_assignments(es, {
            'Natural/_0': {'append': 'Natural', 'pitch': 'E3'},
            'Power/_0': {'append': 'Power', 'pitch': 'G3'},
        })
        natural = [entry for entry in es if entry.group == 'Natural/_0']
        power = [entry for entry in es if entry.group == 'Power/_0']
        self.assertTrue(natural and power)
        self.assertEqual({(entry.color, entry.pitch) for entry in natural}, {('Natural', 'E3')})
        self.assertEqual({(entry.color, entry.pitch) for entry in power}, {('Power', 'G3')})

    def test_manual_group_assignments_reject_invalid_pitch(self):
        es = self.read({'Natural': [('a.wav', '- か_0'), ('b.wav', 'a き_0'), ('c.wav', 'i く_0')]},
                       prefix_map='C4\t\t_0\n')
        with self.assertRaises(ValueError):
            oto.apply_group_assignments(es, {
                'Natural/_0': {'append': 'Natural', 'pitch': 'not-a-note'},
            })

    def test_nfd_file_names(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, 'v')
            os.makedirs(d)
            nfd = unicodedata.normalize('NFD', '_が.wav')
            open(os.path.join(d, nfd), 'wb').close()
            with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('_が.wav=- が,100,50,-200,30,10\n')
            es = oto.read_bank(root)
            self.assertEqual(len(es), 1)
            self.assertTrue(os.path.exists(es[0].wav))

    def test_alias_may_contain_commas(self):
        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, 'take.wav'), 'wb').close()
            with open(os.path.join(root, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('take.wav=ka, alternate,100,50,-200,30,10\n')
            entries = oto.read_bank(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].raw_alias, 'ka, alternate')

    def test_sibling_prefix_map_does_not_classify_another_append_as_that_pitch(self):
        with tempfile.TemporaryDirectory() as root:
            for folder in ('AppendA', 'AppendB'):
                d = os.path.join(root, folder)
                os.makedirs(d)
                with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                    f.write('a.wav=kaF,100,50,-200,30,10\n')
                    f.write('b.wav=kiF,100,50,-200,30,10\n')
                    f.write('c.wav=kuF,100,50,-200,30,10\n')
                for wav in ('a.wav', 'b.wav', 'c.wav'):
                    open(os.path.join(d, wav), 'wb').close()
            with open(os.path.join(root, 'AppendA', 'prefix.map'), 'w', encoding='utf-8') as f:
                f.write('F4\t\tF\n')
            entries = oto.read_bank(root)
            append_a = [e for e in entries if e.group.startswith('AppendA')]
            append_b = [e for e in entries if e.group.startswith('AppendB')]
            self.assertTrue(append_a and append_b)
            self.assertEqual({e.pitch for e in append_a}, {'F'})
            self.assertEqual({e.pitch for e in append_b}, {''})

    def test_prefix_map_is_scoped_to_its_append(self):
        with tempfile.TemporaryDirectory() as root:
            for folder in ('AppendA', 'AppendB'):
                d = os.path.join(root, folder)
                os.makedirs(d)
                open(os.path.join(d, 'take.wav'), 'wb').close()
                with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                    f.write('take.wav=kaF,100,50,-200,30,10\n')
            with open(os.path.join(root, 'AppendA', 'prefix.map'), 'w', encoding='utf-8') as f:
                f.write('C4\t\tF\n')
            entries = oto.read_bank(root)
            by_folder = {e.group.split('/')[0]: e for e in entries}
            self.assertEqual(by_folder['AppendA'].alias, 'ka')
            self.assertEqual(by_folder['AppendB'].alias, 'kaF')

    def test_nested_relative_wav_path_resolves_case_insensitively(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, 'append')
            os.makedirs(os.path.join(d, 'Samples'))
            open(os.path.join(d, 'Samples', 'TAKE.WAV'), 'wb').close()
            with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('samples\\take.wav=ka,100,50,-200,30,10\n')
            diagnostics = []
            entries = oto.read_bank(root, diagnostics=diagnostics)
            self.assertEqual(len(entries), 1, diagnostics)
            self.assertTrue(entries[0].wav.endswith(os.path.join('Samples', 'TAKE.WAV')))

    def test_bad_oto_rows_are_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, 'ok.wav'), 'wb').close()
            with open(os.path.join(root, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('ok.wav=ka,100,50,-200,30,10\n')
                f.write('missing.wav=ki,100,50,-200,30,10\n')
                f.write('ok.wav=bad,not-a-number,50,-200,30,10\n')
            diagnostics = []
            entries = oto.read_bank(root, diagnostics=diagnostics)
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(diagnostics), 2)
            self.assertTrue(any('WAV file not found' in item for item in diagnostics))
            self.assertTrue(any('non-numeric' in item for item in diagnostics))


class Phonemes(unittest.TestCase):
    def test_numbering(self):
        self.assertEqual(P.NUMBERING.sub(r'\1', '4. F3/_F3'), 'F3/_F3')
        self.assertEqual(P.NUMBERING.sub(r'\1', '11. CONSONANT RELEASES/-'), 'CONSONANT RELEASES/-')
        self.assertEqual(P.NUMBERING.sub(r'\1', 'A/12. B'), 'A/B')

    def test_ja_alias(self):
        self.assertEqual(P.ja_alias('a か'), ['a', 'k', 'a'])
        self.assertEqual(P.ja_alias('- か'), ['Sil', 'k', 'a'])
        self.assertEqual(P.ja_alias('n か'), ['N', 'k', 'a'])
        self.assertEqual(P.ja_alias('か'), ['k', 'a'])
        self.assertEqual(P.ja_alias('- ん'), ['Sil', 'N\\'])
        self.assertEqual(P.ja_alias('u きゃ'), ['M', "k'", 'a'])
        self.assertEqual(P.ja_alias('a ざ'), ['a', 'dz', 'a'])
        self.assertEqual(P.ja_alias('- ざ'), ['Sil', 'dz', 'a'])
        self.assertEqual(P.ja_alias('n ざ'), ['n', 'dz', 'a'])
        self.assertEqual(P.ja_syllable('za'), ['dz', 'a'])
        self.assertEqual(P.ja_syllable('ji'), ['dZ', 'i'])


if __name__ == '__main__':
    unittest.main()
