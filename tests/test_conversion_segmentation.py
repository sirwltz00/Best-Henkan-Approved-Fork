"""Tests for OTO marker selection in the converter's first phoneme boundary."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import convert, oto  # noqa: E402


class OtoBoundaryPriors(unittest.TestCase):
    def converter(self):
        c = convert.Converter.__new__(convert.Converter)
        c.extra_phonemes = lambda: set()
        c._extra = lambda _entry: None
        c._analysis_phoneme = lambda phs, i: phs[i]
        c._phoneme_duration = lambda _phs, _i: 0.1
        c.load = lambda _wav: (np.zeros(44100, dtype=np.float32), object())
        return c

    def entry(self, overlap=0.35):
        return oto.OtoEntry(
            wav='unit.wav', alias='unit', raw_alias='unit', group='main', dup=0,
            offset=0.0, consonant=0.50, cutoff_raw=-1000.0,
            preutter=0.70, overlap=overlap)

    def test_consonant_marker_guides_consonant_to_vowel_boundary(self):
        c = self.converter()
        with mock.patch.object(convert.audio, 'find_boundary', side_effect=lambda _ft, _x, _y, hint, *_a, **_k: (hint, False)) as finder, \
             mock.patch.object(convert.audio, 'boundary_confidence', return_value=0.12):
            clip, error = c.segment(self.entry(), ['k', 'a'])
        self.assertIsNone(error)
        self.assertAlmostEqual(clip.bounds[0], 0.50)
        self.assertAlmostEqual(finder.call_args.args[3], 0.50)

    def test_overlap_marker_guides_vowel_to_consonant_boundary(self):
        c = self.converter()
        with mock.patch.object(convert.audio, 'find_boundary', side_effect=lambda _ft, _x, _y, hint, *_a, **_k: (hint, False)) as finder, \
             mock.patch.object(convert.audio, 'boundary_confidence', return_value=0.12):
            clip, error = c.segment(self.entry(overlap=0.35), ['a', 'k'])
        self.assertIsNone(error)
        self.assertAlmostEqual(clip.bounds[0], 0.35)
        self.assertAlmostEqual(finder.call_args.args[3], 0.35)

    def test_vcv_entry_uses_overlap_for_outgoing_and_consonant_for_incoming_boundary(self):
        c = self.converter()
        calls = []

        def find(_ft, _x, _y, hint, *_a, **_k):
            calls.append(hint)
            return hint, False

        with mock.patch.object(convert.audio, 'find_boundary', side_effect=find), \
             mock.patch.object(convert.audio, 'boundary_confidence', return_value=0.12):
            clip, error = c.segment(self.entry(overlap=0.35), ['a', 'k', 'a'])
        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0], 0.50)  # C -> V
        self.assertAlmostEqual(calls[1], 0.35)  # V -> C
        self.assertAlmostEqual(clip.bounds[0], 0.35)
        self.assertAlmostEqual(clip.bounds[1], 0.50)

    def test_invalid_overlap_falls_back_to_preutterance(self):
        c = self.converter()
        with mock.patch.object(convert.audio, 'find_boundary', side_effect=lambda _ft, _x, _y, hint, *_a, **_k: (hint, False)) as finder, \
             mock.patch.object(convert.audio, 'boundary_confidence', return_value=0.12):
            clip, error = c.segment(self.entry(overlap=-0.20), ['a', 'k'])
        self.assertIsNone(error)
        self.assertAlmostEqual(clip.bounds[0], 0.70)
        self.assertAlmostEqual(finder.call_args.args[3], 0.70)


if __name__ == '__main__':
    unittest.main()
