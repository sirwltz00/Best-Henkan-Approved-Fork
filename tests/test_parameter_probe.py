import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import parameter_probe  # noqa: E402


class ParameterProbeTests(unittest.TestCase):
    def test_probe_set_writes_track_and_note_controls(self):
        original = parameter_probe.read_slots
        try:
            parameter_probe.read_slots = lambda editor_dir: [
                (3, 'BKGHF5N5PSTN4KD6', '/tmp/Test', 'Test.tree', 'Test Voice')]
            with tempfile.TemporaryDirectory() as tmp:
                paths = parameter_probe.generate_probe_set(3, tmp, editor_dir='/fake', song='en')
                self.assertEqual(len(paths), 10)
                self.assertTrue(any(path.endswith('_bre.vsqx') for path in paths))
                ns = {'v': 'http://www.yamaha.co.jp/vocaloid/schema/vsq4/'}
                bre = ET.parse(next(path for path in paths if path.endswith('_bre.vsqx'))).getroot()
                cc = bre.findall('.//v:cc', ns)
                self.assertEqual([x.find('v:id', ns).text for x in cc], ['BRE', 'BRE'])
                vel = ET.parse(next(path for path in paths if path.endswith('_vel.vsqx'))).getroot()
                notes = vel.findall('.//v:note', ns)
                self.assertEqual(notes[0].find('v:v', ns).text, '64')
                self.assertEqual(notes[len(notes) // 2].find('v:v', ns).text, '112')
                ope = ET.parse(next(path for path in paths if path.endswith('_ope.vsqx'))).getroot()
                notes = ope.findall('.//v:note', ns)
                self.assertEqual(notes[0].find('v:nStyle/v:v[@id="opening"]', ns).text, '127')
                self.assertEqual(notes[len(notes) // 2].find('v:nStyle/v:v[@id="opening"]', ns).text, '0')
                por = ET.parse(next(path for path in paths if path.endswith('_por.vsqx'))).getroot()
                por_notes = por.findall('.//v:note', ns)
                self.assertEqual(por_notes[0].find('v:nStyle/v:v[@id="risePort"]', ns).text, '0')
                self.assertEqual(por_notes[len(por_notes) // 2].find('v:nStyle/v:v[@id="risePort"]', ns).text, '1')
        finally:
            parameter_probe.read_slots = original

    def test_growl_probe_requires_explicit_opt_in(self):
        original = parameter_probe.read_slots
        try:
            parameter_probe.read_slots = lambda editor_dir: [
                (3, 'BKGHF5N5PSTN4KD6', '/tmp/Test', 'Test.tree', 'Test Voice')]
            with tempfile.TemporaryDirectory() as tmp:
                paths = parameter_probe.generate_probe_set(3, tmp, editor_dir='/fake', include_growl=True)
                self.assertEqual(len(paths), 11)
                growl = ET.parse(next(path for path in paths if path.endswith('_gwl.vsqx'))).getroot()
                ns = {'v': 'http://www.yamaha.co.jp/vocaloid/schema/vsq4/'}
                self.assertEqual(growl.find('.//v:cc/v:id', ns).text, 'GWL')
        finally:
            parameter_probe.read_slots = original


if __name__ == '__main__':
    unittest.main()
