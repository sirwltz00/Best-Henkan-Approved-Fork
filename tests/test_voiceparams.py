import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from u2v import pack  # noqa: E402
from u2v.voiceparams import (VoiceParameterError, normalize_control_changes,
                             normalize_note_controls, normalize_voice_profile)  # noqa: E402
from tools.render import build_notes, write_vsqx  # noqa: E402


class SingerProfileTests(unittest.TestCase):
    def test_profile_defaults_and_aliases(self):
        self.assertEqual(normalize_voice_profile(), {
            'breathiness': 0, 'brightness': 0, 'clearness': 0, 'gender_factor': 0})
        self.assertEqual(normalize_voice_profile({'BRE': -24, 'bri': 48, 'CLE': 7, 'GEN': -12}), {
            'breathiness': -24, 'brightness': 48, 'clearness': 7, 'gender_factor': -12})

    def test_profile_range_and_unknown_fields_are_rejected(self):
        for invalid in ({'breathiness': -128}, {'brightness': 128}, {'opening': 20},
                        {'gender_factor': 1.5}):
            with self.subTest(invalid=invalid), self.assertRaises(VoiceParameterError):
                normalize_voice_profile(invalid)

    def test_vvd_writes_singer_level_offsets(self):
        vvd = pack.decode_vvd(pack.make_vvd('BKGHF5N5PSTN4KD6', 'Test', voice_params={
            'breathiness': -24, 'brightness': 48, 'clearness': 7, 'gender_factor': -12}))
        self.assertIn('"Breathiness" = "-24"', vvd)
        self.assertIn('"Brightness" = "48"', vvd)
        self.assertIn('"Clearness" = "7"', vvd)
        self.assertIn('"Gender Factor" = "-12"', vvd)
        # OPE is note-level in V4, not a Singer Editor offset.
        self.assertIn('"Opening" = "0"', vvd)

    def test_make_package_applies_profile_and_rejects_v3_offsets(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'Voice')
            def fake_pack_db(db_path, package, log=print):
                ddb = os.path.join(package, 'Voice.ddb')
                ddi = os.path.join(package, 'Voice.ddi')
                with open(ddb, 'wb') as f:
                    f.write(b'DDB')
                with open(ddi, 'wb') as f:
                    f.write(b'DDI')
                return ddb, ddi
            with patch.object(pack, 'pack_db', side_effect=fake_pack_db), \
                 patch.object(pack, 'strip_vqm_from_ddi', return_value=False):
                package = pack.make_package(db, os.path.join(tmp, 'out'), name='Voice',
                                             compid='BKGHF5N5PSTN4KD6',
                                             voice_params={'breathiness': 15, 'brightness': -8})
            with open(os.path.join(package, 'Voice.vvd'), 'rb') as f:
                vvd = pack.decode_vvd(f.read())
            self.assertIn('"Breathiness" = "15"', vvd)
            self.assertIn('"Brightness" = "-8"', vvd)
            with patch.object(pack, 'pack_db', side_effect=fake_pack_db), \
                 patch.object(pack, 'strip_vqm_from_ddi', return_value=False):
                with self.assertRaises(pack.PackError):
                    pack.make_package(db, os.path.join(tmp, 'out_v3'), name='Voice',
                                      compid='BKGHF5N5PSTN4KD7', db_version='3',
                                      voice_params={'breathiness': 1})

    def test_dev_slot_writes_same_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'DB_Dev.ini'), 'w', encoding='latin-1') as f:
                f.write('[VVoice 0]\nProgramChange = 0\nVVoiceName = "Existing"\n')
            db = os.path.join(tmp, 'Voice')
            os.makedirs(db)
            open(db + '.tree', 'wb').close()
            pack.write_dev_slot(tmp, db, 'Voice', log=lambda *a: None, voice_params={
                'breathiness': -10, 'brightness': 17, 'clearness': 9, 'gender_factor': -7})
            slot = open(os.path.join(tmp, 'devslot_1', 'slot.txt'), encoding='latin-1').read()
            self.assertIn('Breathiness = -10', slot)
            self.assertIn('Brightness = 17', slot)
            self.assertIn('Clearness = 9', slot)
            self.assertIn('GenderFactor = -7', slot)
            self.assertIn('Opening = 0', slot)


class V4CurveTests(unittest.TestCase):
    NS = {'v': 'http://www.yamaha.co.jp/vocaloid/schema/vsq4/'}

    def test_vsqx_writes_native_cc_and_note_level_controls(self):
        notes, end = build_notes('test')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'probe.vsqx')
            write_vsqx(path, notes, end, control_changes=[('BRE', 0, 0), ('BRE', 3000, 110),
                                                          ('PBS', 3000, 12), ('PIT', 3000, 4096)],
                       note_velocities={notes[0][0]: 112}, note_openings={notes[1][0]: 0},
                       voice_params={'breathiness': -18, 'brightness': 12})
            root = ET.parse(path).getroot()
            cc = root.findall('.//v:cc', self.NS)
            self.assertEqual([(x.find('v:id', self.NS).text,
                               int(x.find('v:pos', self.NS).text),
                               int(x.find('v:value', self.NS).text)) for x in cc],
                             [('BRE', 0, 0), ('BRE', 3000, 110), ('PBS', 3000, 12), ('PIT', 3000, 4096)])
            note_nodes = root.findall('.//v:note', self.NS)
            self.assertEqual(note_nodes[0].find('v:v', self.NS).text, '112')
            self.assertEqual(note_nodes[1].find('v:nStyle/v:v[@id="opening"]', self.NS).text, '0')
            vprm = root.find('.//v:vPrm', self.NS)
            self.assertEqual(vprm.find('v:bre', self.NS).text, '-18')
            self.assertEqual(vprm.find('v:bri', self.NS).text, '12')
            self.assertFalse(root.findall('.//v:mCtrl', self.NS))

    def test_control_ranges_and_dependencies(self):
        self.assertEqual(normalize_control_changes([('PIT', 10, -8192), ('PBS', 10, 24)])[0],
                         ('PBS', 10, 24))
        with self.assertRaises(VoiceParameterError):
            normalize_control_changes([('PIT', 0, 8192)])
        with self.assertRaises(VoiceParameterError):
            normalize_control_changes([('GWL', 0, 127)])
        with self.assertRaises(VoiceParameterError):
            normalize_control_changes([('XSY', 0, 127)])
        self.assertEqual(normalize_control_changes([('GWL', 0, 127)], allow_growl=True),
                         [('GWL', 0, 127)])
        self.assertEqual(normalize_note_controls({0: 64}, {0: 127})['OPE'], {0: 127})


if __name__ == '__main__':
    unittest.main()
