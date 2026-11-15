"""Pack a built DB -> .ddb/.ddi/.vvd, CompIDs, VVD cipher, install scripts.

    python -I -m unittest discover -s tests -v

DB-level tests need the devkit's OU sample database next to the repo and skip without it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import pack as P  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVKIT = os.path.dirname(REPO)
OU_TREE = os.path.join(DEVKIT, 'OU', 'OU.tree')
HAVE_OU = os.path.exists(OU_TREE)

# encoded with the VVD cipher: '"ID" = "VOCALOID VIRTUAL VOICE"\n"FORMAT" = "4.0.0.0"\n'
KAITO_VVD_HEAD = '8S^8 \' 8LUY[VUS^ LSHNO[V LUSY_8'


class Vvd(unittest.TestCase):
    def test_roundtrip(self):
        text = P.VVD_TEMPLATE.format(fmt='4.0.0.0', compid='BKGHF5N5PSTN4KD6', name='KAITO',
                                     growl='"Growl" = "1"\n')
        self.assertEqual(P.decode_vvd(P.encode_vvd(text)), text)

    def test_real_bank_decodes(self):
        plain = P.decode_vvd(KAITO_VVD_HEAD)
        self.assertEqual(plain, '"ID" = "VOCALOID VIRTUAL VOICE"')

    def test_make_vvd(self):
        enc = P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto', fmt='4.0.0.0', growl=True)
        self.assertIn('\r\n', enc)
        plain = P.decode_vvd(enc)
        self.assertIn('"VOICEIDSTR" = "BKGHF5N5PSTN4KD6"', plain)
        self.assertIn('"VOICENAME" = "Teto"', plain)
        self.assertIn('"Growl" = "1"', plain)
        default_plain = P.decode_vvd(P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto'))
        self.assertNotIn('"Growl"', default_plain)
        no_growl = P.decode_vvd(P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto', fmt='3.0.0.0', growl=False))
        self.assertNotIn('Growl', no_growl)

    def test_vvd_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'X.vvd')
            with open(p, 'w', encoding='latin-1', newline='') as f:
                f.write(P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto'))
            self.assertEqual(P.vvd_info(p), {'compid': 'BKGHF5N5PSTN4KD6', 'name': 'Teto'})


class CompId(unittest.TestCase):
    def test_new_compid(self):
        import random
        rng = random.Random(42)
        cids = {P.new_compid(rng) for _ in range(20)}
        self.assertEqual(len(cids), 20)
        for c in cids:
            self.assertTrue(P.COMPID_RE.match(c))
            self.assertTrue(c.startswith('B'))

    def test_check_compid(self):
        self.assertEqual(P.check_compid('bkghf5n5pstn4kd6'), 'BKGHF5N5PSTN4KD6')
        with self.assertRaises(P.PackError):
            P.check_compid('too short')
        with self.assertRaises(P.PackError):
            P.check_compid('lowercase-is-ok-but-!!')


class Editors(unittest.TestCase):
    def test_guess_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            for exe, kind in (('VOCALOID4.exe', 'V4'), ('VoiceLoader.exe', 'V4FE-dev'),
                              ('VOCALOID2.exe', 'V2')):
                d = os.path.join(tmp, kind)
                os.makedirs(d)
                open(os.path.join(d, exe), 'wb').close()
                self.assertEqual(P.guess_editor(d)['kind'], kind)
            os.makedirs(os.path.join(tmp, 'empty'))
            with self.assertRaises(P.PackError):
                P.guess_editor(os.path.join(tmp, 'empty'))

    def test_default_bank_dir(self):
        ed = {'kind': 'V4', 'root': 'C:\\e', 'label': 'x'}
        self.assertTrue(P.default_bank_dir(ed, 'Teto').endswith(os.path.join('VoiceDB', 'Teto')))
        ed2 = {'kind': 'V2', 'root': 'C:\\e2', 'label': 'x'}
        self.assertEqual(P.default_bank_dir(ed2, 'Teto'),
                         os.path.join('C:\\e2', 'voicedbdir', 'Teto'))

    def test_install_script_v4(self):
        bat = P.install_script('BKGHF5N5PSTN4KD6', 'Teto', 'C:\\V\\Teto', 'Teto', 'V4')
        self.assertIn('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE\\BKGHF5N5PSTN4KD6', bat)
        self.assertIn('HKLM\\SOFTWARE\\POCALOID4\\DATABASE\\BKGHF5N5PSTN4KD6', bat)
        self.assertIn('VOCALOID4 Library (Teto)', bat)
        self.assertIn('/reg:32', bat)
        # working commercial layout: files into a CompID subfolder, DBVERSION + real DRP, no KEYS
        self.assertIn('%DB_PATH%\\%DB_COMPID%', bat)
        self.assertIn('/V DBVERSION /T REG_DWORD /D "4"', bat)
        self.assertIn('0B0A51', bat)
        self.assertNotIn('/V KEYS', bat)  # no placeholder key value; stale KEYS subkeys are deleted
        self.assertIn('KEYS" /F', bat)
        un = P.install_script('BKGHF5N5PSTN4KD6', 'Teto', 'C:\\V\\Teto', 'Teto', 'V4', uninstall=True)
        self.assertIn('reg DELETE', un)
        self.assertNotIn('copy /Y', un)

    def test_dev_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, 'DB_Dev.ini')
            open(ini, 'w').write('[VVoice 0]\nProgramChange = 0\nVVoiceName = \"X\"\n')
            dbfolder = os.path.join(tmp, 'Singer')
            os.makedirs(dbfolder)
            open(os.path.join(tmp, 'Singer.tree'), 'wb').close()
            bat = P.write_dev_slot(tmp, os.path.join(dbfolder), 'Singer', log=lambda *a: None)
            self.assertTrue(os.path.exists(bat))
            slots = P.read_dev_slots(ini)
            self.assertEqual(slots, [(0, 'X', '')])
            snippet = open(os.path.join(tmp, 'devslot_1', 'slot.txt')).read()
            self.assertIn('[VVoice 1]', snippet)
            self.assertIn('DBName = \"Singer.tree\"', snippet)


@unittest.skipUnless(HAVE_OU, 'devkit OU sample DB not next to the repo')
class PackOU(unittest.TestCase):
    def test_ddi_reads(self):
        with open(OU_TREE, 'rb') as f:
            m = P.DDIModel(f.read())
        m.read()
        self.assertTrue(len(m.ddi_data_dict['sta']) > 0)
        self.assertTrue(len(m.ddi_data_dict['art']) > 100)

    def test_pack_and_verify(self):
        db = OU_TREE[:-5]
        with tempfile.TemporaryDirectory() as tmp:
            ddb_src, ddi_src = P.pack_db(db, os.path.join(tmp, 'pkg'), log=lambda *a: None)
            with open(ddb_src, 'rb') as f:
                ddb = f.read()
            with open(ddi_src, 'rb') as f:
                ddi = f.read()
            self.assertTrue(ddb.startswith(b'FRM2'))
            m = P.DDIModel(ddi)
            m.read()
            with open(OU_TREE, 'rb') as f:
                ref = P.DDIModel(f.read())
            ref.read()
            self.assertEqual(set(m.ddi_data_dict['art']), set(ref.ddi_data_dict['art']))
            self.assertEqual(set(m.ddi_data_dict['sta']), set(ref.ddi_data_dict['sta']))
            # every 50th offset must land in its chunk (full check is slow on 650 MB)
            bad = tot = n = 0

            def check(pos, is_snd):
                R = int.from_bytes(ddi[pos:pos + 8], 'little')
                if not is_snd:
                    return ddb[R:R + 4] == b'FRM2'
                S = ddb.rfind(b'SND ', 0, R)
                L = int.from_bytes(ddb[S + 4:S + 8], 'little') if S >= 0 else 0
                return S >= 0 and S + L > R

            for items in list(m.ddi_data_dict['art'].values()) + list(m.ddi_data_dict['sta'].values()):
                for it in items:
                    work = [(e, False) for e in it['epr']] + [(it['snd'], True)]
                    if 'snd_start' in it:
                        work.append((it['snd_start'], True))
                    for e, is_snd in work:
                        n += 1
                        if n % 50 == 0:
                            tot += 1
                            if not check(int(e.split('=')[0], 16), is_snd):
                                bad += 1
            self.assertEqual(bad, 0, '%d/%d sampled offsets bad' % (bad, tot))
            self.assertTrue(tot > 100)


if __name__ == '__main__':
    unittest.main()


class GrowlVqm(unittest.TestCase):
    @staticmethod
    def _chunk(tag, payload):
        return tag + (len(payload) + 8).to_bytes(4, 'little') + payload

    @staticmethod
    def _str(value):
        raw = value.encode('latin-1')
        return len(raw).to_bytes(4, 'little') + raw

    @classmethod
    def _vqm(cls, epr_offsets, snd_offset, marker_width=4):
        import struct
        b = bytearray(b'\xff' * 8 + b'VQM ')
        b += struct.pack('<4I', 0, 1, 0, 1)
        b += b'\xff' * 8 + b'VQMu' + struct.pack('<5I', 0, 1, 0, 1, 1)
        b += b'\xff' * 8 + b'VQMp' + struct.pack('<3I', 0, 0, 1)
        b += struct.pack('<d', 0.25)
        b += (1).to_bytes(marker_width, 'little')
        b += struct.pack('<5f', 224.0, 440.0, 0.0, 0.75, 120.0)
        b += struct.pack('<I', 0) + b'\xff' * 4
        b += len(epr_offsets).to_bytes(4, 'little')
        for offset in epr_offsets:
            b += int(offset).to_bytes(8, 'little')
        b += struct.pack('<I', 44100) + b'\x01\x00' + struct.pack('<I', 7)
        b += int(snd_offset).to_bytes(8, 'little') + b'\xff' * 0x10
        b += cls._str('0') + cls._str('GROWL') + cls._str('vqm')
        return bytes(b)

    @staticmethod
    def _ddi_prefix(dbv_count=2):
        return (b'\x00' * 8 + b'DBV ' + b'\x00' * 4 + b'\x00' * 8 +
                int(dbv_count).to_bytes(4, 'little'))

    @staticmethod
    def _fake_model(epr_offsets, snd_offset, snd_id=7):
        class Model:
            def __init__(self, _ddi_bytes):
                self.sta_data = {}

            def read(self):
                self.sta_data = {
                    0: {
                        'phoneme': 'Grw',
                        'stap': {
                            '0': {
                                'epr': ['00000000=%08x' % offset for offset in epr_offsets],
                                'snd': '00000000=%016x_%08x' % (snd_offset, snd_id),
                                'duration': 1.1,
                                'pitch1': 220.0,
                                'pitch2': 220.0,
                                'unknown2': 0.0,
                                'dynamics': 0.75,
                                'tempo': 120.0,
                                'fs': 44100,
                            }
                        },
                    }
                }
        return Model

    def test_parser_accepts_both_observed_marker_widths(self):
        for width in (2, 4):
            with self.subTest(width=width):
                vqm = self._vqm([0x20], 0x40, marker_width=width)
                prefix = self._ddi_prefix()
                start, end, records = P._parse_vqm_chunk(prefix + vqm + P._VQM_FOOTER)
                self.assertEqual(start, len(prefix))
                self.assertEqual(end, len(prefix) + len(vqm))
                self.assertEqual(records[0]['index'], '0')
                self.assertAlmostEqual(records[0]['pitch2'], 440.0)

    def test_create_vqm_from_single_grw_stationary_wav(self):
        import struct
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            ddb_path = os.path.join(tmp, 'Singer.ddb')
            ddi_path = os.path.join(tmp, 'Singer.ddi')
            ddb = bytearray()
            frame_offsets = []
            for i in range(100):
                frame_offsets.append(len(ddb))
                ddb += self._chunk(b'FRM2', ('frame-%03d' % i).encode())
            snd_header = len(ddb)
            ddb += self._chunk(b'SND ', b'one growled aaaa sample')
            # STA SND pointers may point inside the SND chunk rather than at its header.
            snd_pointer = snd_header + 0x12
            with open(ddb_path, 'wb') as f:
                f.write(ddb)
            with open(ddi_path, 'wb') as f:
                f.write(self._ddi_prefix() + b'Grw' + P._VQM_FOOTER)
            with mock.patch.object(P, 'DDIModel', self._fake_model(frame_offsets, snd_pointer)):
                count = P.add_vqm_from_grw_stationary(ddi_path, ddb_path, log=lambda *_: None, vqm_indices=[3])
            self.assertEqual(count, 1)
            with open(ddi_path, 'rb') as f:
                updated_ddi = f.read()
            with open(ddb_path, 'rb') as f:
                updated_ddb = f.read()
            self.assertEqual(struct.unpack_from('<I', updated_ddi, 24)[0], 3)
            self.assertEqual(P._validate_vqm_refs(updated_ddi, updated_ddb), 1)
            _start, _end, records = P._parse_vqm_chunk(updated_ddi)
            self.assertEqual(records[0]['index'], '3')
            self.assertEqual(len(records[0]['epr']), 100)
            self.assertTrue(all(offset >= len(ddb) for _pos, offset in records[0]['epr']))
            self.assertGreaterEqual(records[0]['snd_offset'], len(ddb))

    def test_load_vqm_index_map(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'Singer')
            with open(db_path + '.henkan-vqm.json', 'w', encoding='utf-8') as f:
                json.dump({'format_version': 1, 'samples': [{'index': 0}, {'index': 3}]}, f)
            self.assertEqual(P._load_vqm_sample_indexes(db_path), [0, 3])

    def test_vqm_index_map_rejects_duplicate_indexes(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'Singer')
            with open(db_path + '.henkan-vqm.json', 'w', encoding='utf-8') as f:
                json.dump({'format_version': 1, 'samples': [{'index': 1}, {'index': 1}]}, f)
            with self.assertRaisesRegex(P.PackError, 'unique non-negative'):
                P._load_vqm_sample_indexes(db_path)

    def test_multiple_grw_samples_keep_their_source_indexes(self):
        import struct
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            ddb_path = os.path.join(tmp, 'Singer.ddb')
            ddi_path = os.path.join(tmp, 'Singer.ddi')
            ddb = bytearray()
            sample_data = {}
            for index in range(2):
                frame_offsets = []
                for frame in range(100):
                    frame_offsets.append(len(ddb))
                    ddb += self._chunk(b'FRM2', ('sample-%d-frame-%03d' % (index, frame)).encode())
                snd_header = len(ddb)
                ddb += self._chunk(b'SND ', ('growl sample %d' % index).encode())
                sample_data[str(index)] = {
                    'epr': ['00000000=%08x' % offset for offset in frame_offsets],
                    'snd': '00000000=%016x_%08x' % (snd_header + 0x12, 7 + index),
                    'duration': 1.1, 'pitch1': 220.0 + 30 * index,
                    'pitch2': 220.0 + 30 * index, 'unknown2': 0.0,
                    'dynamics': 0.75, 'tempo': 120.0, 'fs': 44100,
                }
            with open(ddb_path, 'wb') as f:
                f.write(ddb)
            with open(ddi_path, 'wb') as f:
                f.write(self._ddi_prefix() + b'Grw' + P._VQM_FOOTER)

            class Model:
                def __init__(self, _ddi_bytes):
                    self.sta_data = {}

                def read(self):
                    self.sta_data = {0: {'phoneme': 'Grw', 'stap': sample_data}}

            with mock.patch.object(P, 'DDIModel', Model):
                count = P.add_vqm_from_grw_stationary(ddi_path, ddb_path,
                                                       log=lambda *_: None, vqm_indices=[0, 3])
            self.assertEqual(count, 2)
            with open(ddi_path, 'rb') as f:
                updated_ddi = f.read()
            with open(ddb_path, 'rb') as f:
                updated_ddb = f.read()
            self.assertEqual(P._validate_vqm_refs(updated_ddi, updated_ddb), 2)
            _start, _end, records = P._parse_vqm_chunk(updated_ddi)
            self.assertEqual([record['index'] for record in records], ['0', '3'])

    def test_short_grw_stationary_is_rejected(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            ddb_path = os.path.join(tmp, 'Singer.ddb')
            ddi_path = os.path.join(tmp, 'Singer.ddi')
            ddb = bytearray()
            frame_offsets = []
            for i in range(99):
                frame_offsets.append(len(ddb))
                ddb += self._chunk(b'FRM2', ('frame-%03d' % i).encode())
            snd_header = len(ddb)
            ddb += self._chunk(b'SND ', b'growl')
            with open(ddb_path, 'wb') as f:
                f.write(ddb)
            with open(ddi_path, 'wb') as f:
                f.write(self._ddi_prefix() + b'Grw' + P._VQM_FOOTER)
            with mock.patch.object(P, 'DDIModel', self._fake_model(frame_offsets, snd_header + 0x12)):
                with self.assertRaisesRegex(P.PackError, '100'):
                    P.add_vqm_from_grw_stationary(ddi_path, ddb_path, log=lambda *_: None)

    def test_no_grw_sample_does_not_add_vqm(self):
        with tempfile.TemporaryDirectory() as tmp:
            ddb_path = os.path.join(tmp, 'Singer.ddb')
            ddi_path = os.path.join(tmp, 'Singer.ddi')
            with open(ddb_path, 'wb') as f:
                f.write(self._chunk(b'FRM2', b'x'))
            with open(ddi_path, 'wb') as f:
                f.write(self._ddi_prefix() + b'ordinary voice' + P._VQM_FOOTER)
            self.assertEqual(P.add_vqm_from_grw_stationary(ddi_path, ddb_path, log=lambda *_: None), 0)
            with open(ddi_path, 'rb') as f:
                self.assertNotIn(P._VQM_MARKER, f.read())

    def test_invalid_chunk_pointer_is_rejected(self):
        with self.assertRaises(P.PackError):
            P._extract_ddb_chunk(b'not-a-chunk', 0, b'FRM2')

    def test_make_v4_growl_flag_requires_valid_vqm(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'DB', 'Singer')
            os.makedirs(os.path.dirname(db_path))
            with open(db_path + '.tree', 'wb') as f:
                f.write(b'placeholder')

            def fake_pack_db(_db, dst, log=print):
                ddi = os.path.join(dst, 'Singer.ddi')
                ddb = os.path.join(dst, 'Singer.ddb')
                with open(ddi, 'wb') as f:
                    f.write(self._ddi_prefix() + b'ordinary voice' + P._VQM_FOOTER)
                with open(ddb, 'wb') as f:
                    f.write(self._chunk(b'FRM2', b'x') + self._chunk(b'SND ', b'y'))
                return ddb, ddi

            with mock.patch.object(P, 'pack_db', side_effect=fake_pack_db):
                pkg = P.make_package(db_path, os.path.join(tmp, 'out'), name='NoGrowl',
                                     compid='BKGHF5N5PSTN4KD6', log=lambda *_: None)
            with open(os.path.join(pkg, 'NoGrowl.vvd'), 'rb') as f:
                plain = P.decode_vvd(f.read())
            self.assertNotIn('"Growl"', plain)

    def test_make_package_builds_growl_from_grw_not_donor(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'DB', 'Singer')
            os.makedirs(os.path.dirname(db_path))
            with open(db_path + '.tree', 'wb') as f:
                f.write(b'placeholder')
            ddb_path = os.path.join(tmp, 'dummy.ddb')
            ddb = bytearray()
            frame_offsets = []
            for i in range(100):
                frame_offsets.append(len(ddb))
                ddb += self._chunk(b'FRM2', ('frame-%03d' % i).encode())
            snd_header = len(ddb)
            ddb += self._chunk(b'SND ', b'one growled aaaa sample')

            def fake_pack_db(_db, dst, log=print):
                ddi = os.path.join(dst, 'Singer.ddi')
                packed_ddb = os.path.join(dst, 'Singer.ddb')
                with open(ddi, 'wb') as f:
                    f.write(self._ddi_prefix() + b'Grw' + P._VQM_FOOTER)
                with open(packed_ddb, 'wb') as f:
                    f.write(ddb)
                return packed_ddb, ddi

            with mock.patch.object(P, 'pack_db', side_effect=fake_pack_db), \
                    mock.patch.object(P, 'DDIModel', self._fake_model(frame_offsets, snd_header + 0x12)):
                pkg = P.make_package(db_path, os.path.join(tmp, 'out'), name='WithGrowl',
                                     compid='BKGHF5N5PSTN4KD6', log=lambda *_: None)
            with open(os.path.join(pkg, 'WithGrowl.vvd'), 'rb') as f:
                plain = P.decode_vvd(f.read())
            self.assertIn('"Growl" = "1"', plain)
            with open(os.path.join(pkg, 'WithGrowl.ddi'), 'rb') as f:
                final_ddi = f.read()
            with open(os.path.join(pkg, 'WithGrowl.ddb'), 'rb') as f:
                final_ddb = f.read()
            self.assertEqual(P._validate_vqm_refs(final_ddi, final_ddb), 1)

    def test_growl_stationary_requires_v4_package(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'DB', 'Singer')
            os.makedirs(os.path.dirname(db_path))
            with open(db_path + '.tree', 'wb') as f:
                f.write(b'placeholder')

            def fake_pack_db(_db, dst, log=print):
                ddi = os.path.join(dst, 'Singer.ddi')
                ddb = os.path.join(dst, 'Singer.ddb')
                with open(ddi, 'wb') as f:
                    f.write(self._ddi_prefix() + b'Grw' + P._VQM_FOOTER)
                with open(ddb, 'wb') as f:
                    f.write(self._chunk(b'FRM2', b'x'))
                return ddb, ddi

            with mock.patch.object(P, 'pack_db', side_effect=fake_pack_db):
                with self.assertRaisesRegex(P.PackError, 'VOCALOID4-only'):
                    P.make_package(db_path, os.path.join(tmp, 'out'), db_version='3',
                                   compid='BKGHF5N5PSTN4KD6', log=lambda *_: None)

