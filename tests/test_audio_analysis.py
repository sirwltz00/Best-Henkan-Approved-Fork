"""Acoustic boundary tests for medial fricative realizations of VOCALOID dz/dZ."""
import os
import sys
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert, phonemes as P  # noqa: E402

SR = 44100


def tone(seconds, amplitude=0.3, hz=220):
    t = np.arange(round(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def noise(seconds, amplitude, seed=3):
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(round(seconds * SR))).astype(np.float32)


def affricated_vcv():
    """A V-[d closure + release]-z-V sample, as often happens when a VCV alias is sung syllabically."""
    fric = tone(0.12, 0.20) + noise(0.12, 0.035, 4)
    return np.concatenate((tone(0.18), noise(0.04, 0.0002), noise(0.004, 0.25), fric, tone(0.20)))


def continuous_vcv():
    """A connected vowel-to-z transition with no stop closure."""
    fric = tone(0.12, 0.20) + noise(0.12, 0.035, 4)
    return np.concatenate((tone(0.18), fric, tone(0.20)))


class VoicedFricativeAnalysis(unittest.TestCase):
    def test_affricated_vcv_boundary_moves_to_sibilant_not_stop_closure(self):
        x = affricated_vcv()
        ft = audio.Features(x, SR)
        boundary, detected = audio.find_boundary(ft, 'vowel', 'vcons', 0.20, 0.10, 0.29,
                                                  'z', x_ph='a')
        self.assertTrue(detected)
        # The stop closure runs around 0.18-0.22 s; the frication follows it.
        self.assertGreaterEqual(boundary, 0.21)
        self.assertLessEqual(boundary, 0.235)

    def test_z_to_vowel_boundary_uses_frication_offset(self):
        x = affricated_vcv()
        ft = audio.Features(x, SR)
        boundary, detected = audio.find_boundary(ft, 'vcons', 'vowel', 0.34, 0.27, 0.40,
                                                  'a', x_ph='z')
        self.assertTrue(detected)
        self.assertAlmostEqual(boundary, 0.344, delta=0.012)

    def test_medial_z_closure_is_bridged_with_voiced_material(self):
        x = affricated_vcv()
        ft = audio.Features(x, SR)
        boundary, detected = audio.find_boundary(ft, 'vowel', 'vcons', 0.20, 0.10, 0.29,
                                                  'z', x_ph='a')
        self.assertTrue(detected)
        out, repaired = audio.deaffricate_medial_vcv(x, SR, ft, boundary, 0.0, x.copy())
        self.assertTrue(repaired)
        before = audio.rms_db(x[int(0.18 * SR):int(0.22 * SR)])
        after = audio.rms_db(out[int(0.18 * SR):int(0.22 * SR)])
        self.assertGreater(after, before + 20.0)

    def test_already_continuous_z_is_not_modified(self):
        x = continuous_vcv()
        ft = audio.Features(x, SR)
        boundary, detected = audio.find_boundary(ft, 'vowel', 'vcons', 0.18, 0.10, 0.28,
                                                  'z', x_ph='a')
        self.assertTrue(detected)
        out, repaired = audio.deaffricate_medial_vcv(x, SR, ft, boundary, 0.0, x.copy())
        self.assertFalse(repaired)
        np.testing.assert_array_equal(out, x)

    def test_converter_repair_is_limited_to_medial_vcv_not_initial_dz(self):
        x = affricated_vcv()
        ft = audio.Features(x, SR)
        boundary, detected = audio.find_boundary(ft, 'vowel', 'vcons', 0.20, 0.10, 0.29,
                                                  'z', x_ph='a')
        self.assertTrue(detected)
        converter = convert.Converter.__new__(convert.Converter)
        converter.load = lambda _path: (x, ft)
        converter.lang = 'ja'
        converter.n_deaffricated = 0
        medial = convert.Clip(SimpleNamespace(wav='sample.wav'), ['a', 'dz', 'a'], 0.0,
                              len(x) / SR, [boundary, 0.34], [True, True])
        repaired = converter._deaffricate_medial_vcv(medial, x, x.copy())
        self.assertGreater(converter.n_deaffricated, 0)
        self.assertFalse(np.array_equal(repaired, x))

        initial = convert.Clip(SimpleNamespace(wav='sample.wav'), ['Sil', 'dz', 'a'], 0.0,
                               len(x) / SR, [boundary, 0.34], [True, True])
        untouched = converter._deaffricate_medial_vcv(initial, x, x.copy())
        np.testing.assert_array_equal(untouched, x)

    def test_vocaloid_label_is_dz_in_every_context_but_analysis_is_contextual(self):
        self.assertEqual(P.ja_syllable('za'), ['dz', 'a'])
        self.assertEqual(P.ja_syllable('ji'), ['dZ', 'i'])
        self.assertEqual(P.ja_alias('a ざ'), ['a', 'dz', 'a'])
        self.assertEqual(P.ja_alias('a じ'), ['a', 'dZ', 'i'])
        self.assertEqual(P.ja_alias('- ざ'), ['Sil', 'dz', 'a'])
        self.assertEqual(P.ja_alias('n ざ'), ['n', 'dz', 'a'])

        converter = convert.Converter.__new__(convert.Converter)
        converter.lang = 'ja'
        self.assertEqual(converter._analysis_phoneme(['a', 'dz', 'a'], 1), 'z')
        self.assertEqual(converter._analysis_phoneme(['Sil', 'dz', 'a'], 1), 'dz')
        self.assertEqual(converter._phoneme_duration(['a', 'dz', 'a'], 1), 0.07)
        self.assertEqual(converter._phoneme_duration(['Sil', 'dz', 'a'], 1), 0.10)

        converter.lang = 'en'
        self.assertFalse(converter._is_medial_ja_z(['a', 'dZ', 'a'], 1))

        converter.lang = 'ja'
        converter.v_real = False
        converter.dict = None
        expected = converter.expected_units()
        self.assertIn(('a', 'dz'), expected)
        self.assertIn(('Sil', 'dz'), expected)
        self.assertIn(('n', 'dz'), expected)
        self.assertNotIn(('a', 'z'), expected)


if __name__ == '__main__':
    unittest.main()
