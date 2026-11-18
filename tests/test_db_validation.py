"""End-to-end validation tests for the DBTool sidecar folder contract."""
import os
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_io  # noqa: E402


class DBFolderValidation(unittest.TestCase):
    def make_folder(self, root, channels=1, sample_width=2):
        base = os.path.join(root, 'unit_00001')
        with wave.open(base + '.wav', 'wb') as w:
            w.setnchannels(channels)
            w.setsampwidth(sample_width)
            w.setframerate(44100)
            w.writeframes(b'\x00' * (44100 * channels * sample_width))
        dbtool_io.write_trans(base + '.trans', ['Sil', 'k', 'a'], [(['k', 'a'], 1)])
        dbtool_io.write_seg(base + '.seg', [('Sil', 0.0, 0.08), ('k', 0.08, 0.15), ('a', 0.15, 1.0)])
        dbtool_io.write_as(base + '.as0', dbtool_io.ArtSeg(
            ['k', 'a'], 0, 44100, [0.10, 0.15, 0.50], [False, True]))
        dictionary = dbtool_io.PhoneticDict(
            voiced={'Sil': False, 'k': False, 'a': True}, groups={})
        return base, dictionary

    def test_valid_folder_passes(self):
        with tempfile.TemporaryDirectory() as root:
            _, dictionary = self.make_folder(root)
            self.assertEqual(dbtool_io.validate_folder(root, dictionary), [])

    def test_missing_as_file_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            base, dictionary = self.make_folder(root)
            os.remove(base + '.as0')
            errors = dbtool_io.validate_folder(root, dictionary)
            self.assertTrue(any('missing .as0' in error for error in errors), errors)

    def test_orphan_as_file_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            base, dictionary = self.make_folder(root)
            dbtool_io.write_as(base + '.as1', dbtool_io.ArtSeg(
                ['k', 'a'], 0, 44100, [0.10, 0.15, 0.50], [False, True]))
            errors = dbtool_io.validate_folder(root, dictionary)
            self.assertTrue(any('orphan .as sidecar' in error for error in errors), errors)

    def test_stereo_wav_is_rejected_for_dbtool(self):
        with tempfile.TemporaryDirectory() as root:
            _, dictionary = self.make_folder(root, channels=2)
            errors = dbtool_io.validate_folder(root, dictionary)
            self.assertTrue(any('must be mono' in error for error in errors), errors)

    def test_dictionary_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            _, dictionary = self.make_folder(root)
            dictionary.voiced.pop('k')
            errors = dbtool_io.validate_folder(root, dictionary)
            self.assertTrue(any('not in dictionary' in error for error in errors), errors)


if __name__ == '__main__':
    unittest.main()
