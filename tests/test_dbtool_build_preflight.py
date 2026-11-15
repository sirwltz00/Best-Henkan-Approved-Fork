"""Ensure unsafe/invalid inputs are rejected before opening the Windows DBTool."""
import os
import sys
import tempfile
import unittest
import wave
import struct
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build, dbtool_io  # noqa: E402


class BuildPreflight(unittest.TestCase):
    def dictionary(self, root):
        path = os.path.join(root, 'dictionary.txt')
        with open(path, 'w', encoding='latin-1') as fh:
            fh.write('phonetic_group(name="Vowels", voiced=true):\n\ta\n'
                     'phonetic_group(name="UnvoicedPlosives", voiced=false):\n\tk\n'
                     'phonetic_group(name="Silence", voiced=false):\n\tSil\n')
        return path

    def folder(self, root, channels=1):
        base = os.path.join(root, 'unit_00001')
        with wave.open(base + '.wav', 'wb') as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b'\x00' * (44100 * channels * 2))
        dbtool_io.write_trans(base + '.trans', ['Sil', 'k', 'a'], [(['k', 'a'], 1)])
        dbtool_io.write_seg(base + '.seg', [('Sil', 0.0, .08), ('k', .08, .15), ('a', .15, 1.0)])
        dbtool_io.write_as(base + '.as0', dbtool_io.ArtSeg(
            ['k', 'a'], 0, 44100, [.10, .15, .50], [False, True]))

    def test_missing_converted_folder_is_rejected_before_dbtool(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.dictionary(root)
            with mock.patch.object(dbtool_build, '_build_passes') as build_passes:
                with self.assertRaises(dbtool_build.DBToolError):
                    dbtool_build.build('missing.exe', os.path.join(root, 'missing'),
                                       os.path.join(root, 'out', 'Singer'), dictionary=d)
                build_passes.assert_not_called()

    def test_invalid_wav_is_rejected_before_dbtool(self):
        with tempfile.TemporaryDirectory() as root:
            seg = os.path.join(root, 'converted')
            os.makedirs(seg)
            self.folder(seg, channels=2)
            d = self.dictionary(root)
            with mock.patch.object(dbtool_build, '_build_passes') as build_passes:
                with self.assertRaisesRegex(dbtool_build.DBToolError, 'preflight'):
                    dbtool_build.build('missing.exe', seg, os.path.join(root, 'out', 'Singer'), dictionary=d)
                build_passes.assert_not_called()

    def test_post_build_artifacts_are_verified(self):
        with tempfile.TemporaryDirectory() as root:
            seg = os.path.join(root, 'converted')
            os.makedirs(seg)
            self.folder(seg)
            d = self.dictionary(root)
            final = os.path.join(root, 'db', 'Singer')
            logs = []

            def fake_build(_exe, _seg, db_path, *_args, **_kwargs):
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                # Minimal PHDC dictionary chunk understood by dbtool_io.load_dictionary_dat.
                rec = b'a' + b'\x00' * 29 + b'\x00'
                payload = b'PHDC' + struct.pack('<III', 31, 0, 1) + rec + b'\x00' * 64
                with open(db_path + '.dat', 'wb') as fh:
                    fh.write(payload)
                with open(db_path + '.tree', 'wb') as fh:
                    fh.write(b'T' * 128)
                os.makedirs(db_path, exist_ok=True)
                with open(os.path.join(db_path, 'singer.inf'), 'w', encoding='ascii') as fh:
                    fh.write('name=Singer\n')
                return []

            with mock.patch.object(dbtool_build, '_build_passes', side_effect=fake_build):
                result = dbtool_build.build('fake.exe', seg, final, dictionary=d, log=logs.append)
            self.assertEqual(result, [])
            self.assertTrue(os.path.isfile(final + '.dat'))
            self.assertTrue(os.path.isfile(final + '.tree'))
            self.assertTrue(any('database artifacts verified' in line for line in logs), logs)

    def test_existing_db_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            seg = os.path.join(root, 'converted')
            os.makedirs(seg)
            d = self.dictionary(root)
            final = os.path.join(root, 'db', 'Singer')
            os.makedirs(os.path.dirname(final))
            with open(final + '.tree', 'wb') as fh:
                fh.write(b'existing')
            with mock.patch.object(dbtool_build, '_build_passes') as build_passes:
                with self.assertRaisesRegex(dbtool_build.DBToolError, 'already exists'):
                    dbtool_build.build('missing.exe', seg, final, dictionary=d)
                build_passes.assert_not_called()


if __name__ == '__main__':
    unittest.main()
