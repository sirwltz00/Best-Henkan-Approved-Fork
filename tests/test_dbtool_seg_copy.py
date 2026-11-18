"""The build copies the converted folder; a subfolder in it (e.g. the source bank) must not stop it.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build as B  # noqa: E402


class SegCopy(unittest.TestCase):
    def test_subfolder_is_skipped(self):
        with tempfile.TemporaryDirectory() as seg, tempfile.TemporaryDirectory() as work:
            for fn in ('a.wav', 'a.trans', 'dictionary.txt'):
                with open(os.path.join(seg, fn), 'w') as f:
                    f.write(fn)
            os.mkdir(os.path.join(seg, 'Tsukine Sora'))
            with open(os.path.join(seg, 'Tsukine Sora', 'oto.ini'), 'w') as f:
                f.write('x')
            B._copy_seg(seg, work)
            self.assertEqual(sorted(os.listdir(work)), ['a.trans', 'a.wav', 'dictionary.txt'])


<<<<<<< HEAD
class FreshBuildRetry(unittest.TestCase):
    def test_hidden_retry_uses_a_fresh_dictionary_copy(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as seg, tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(seg, 'sample.wav'), 'wb') as f:
                f.write(b'wave')
            source_dictionary = os.path.join(tmp, 'external-dictionary.txt')
            with open(source_dictionary, 'w', encoding='latin-1') as f:
                f.write('dictionary contents')
            calls = []

            def fake_build(_exe, work, _db_path, _name, dictionary, _language, _log, desktop=None):
                calls.append((work, dictionary, desktop))
                self.assertTrue(os.path.isfile(dictionary))
                with open(dictionary, encoding='latin-1') as f:
                    self.assertEqual(f.read(), 'dictionary contents')
                if len(calls) == 1:
                    raise B.DBToolError('forced hidden-build retry')
                return None

            with (patch.object(B, '_on_hidden_desktop', side_effect=lambda callback: callback('hidden')),
                  patch.object(B, '_build', side_effect=fake_build)):
                B._build_fresh('dbtool.exe', seg, os.path.join(tmp, 'Singer'), 'Singer',
                               source_dictionary, 'Japanese', lambda *_a: None, {}, True)

            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0][0], calls[1][0])
            self.assertFalse(os.path.exists(calls[0][0]))
            self.assertFalse(os.path.exists(calls[1][0]))


=======
>>>>>>> 3de923520b35cdd8cb2a82b06e83695ece8b6b86
class StartError(unittest.TestCase):
    def test_side_by_side_names_the_runtime(self):
        ex = OSError(14001, 'CreateProcess', 'The application has failed to start because its side-by-side '
                                             'configuration is incorrect.')
        msg = str(B._start_error(ex, r'C:\VOCALOID Developer\VocaloidDBTool3.exe'))
        self.assertIn('Visual C++ 2008', msg)
        self.assertIn('x86', msg)

    def test_other_errors_pass_through(self):
        msg = str(B._start_error(OSError(2, 'not found'), r'C:\x\VocaloidDBTool3.exe'))
        self.assertIn('not found', msg)
        self.assertNotIn('Visual C++', msg)


if __name__ == '__main__':
    unittest.main()
