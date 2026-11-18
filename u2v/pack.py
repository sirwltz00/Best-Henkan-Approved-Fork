"""Pack a built singer DB into a voicebank package (.ddb + .ddi + .vvd) and install it
into a VOCALOID editor of your choice.

Pipeline (all verified against real installs on a VOCALOID machine):

1. **Pack**: the DBTool writes ``<Name>.tree`` (which IS the DDI: PHDC/STA/ART/VQM blocks)
   plus per-unit sample files under ``<Name>/voice/articulation/...`` and
   ``<Name>/voice/stationary/normal/...``. Packing copies every unit's FRM2/SND chunks
   into one ``<Name>.ddb`` and rebases the offsets inside ``<Name>.ddi``.
   (Same algorithm as ddb-tools' pack_ddb.py, ported here so there is no dependency.)
2. **VVD**: ``<Name>.vvd`` is substitution-cipher encoded text metadata (voicebank name +
   CompID). Same cipher and template as VVD Editor Plus.
3. **Install**: the package folder ``<CompID>/`` (with same-basename .ddb/.ddi/.vvd) is
   copied to the editor's bank folder and registered, e.g. for VOCALOID4::

       HKLM\\SOFTWARE\\VOCALOID4\\DATABASE41\\<CompID>  (+ the same under POCALOID4)
           PATH=<CompID folder>, DRP=0B0A51, INSTALLED=1, DBVERSION=4, NAME, TIME, BankName

   Registry writes + Program Files copies need admin rights, so install/uninstall run
   through generated .bat scripts (same shape as VVD Editor Plus') launched elevated.
   The VOCALOID4FE developer editor instead takes a ``[VVoice N]`` slot in DB_Dev.ini
   pointing at the loose built DB (no packing needed there).

    python -I henkan.py pack "<DB folder>/<Name>" --out <dir> --name <voice> [--compid X]
    python -I henkan.py install <package dir> --editor "<editor folder>"
"""
import io
import json
import os
import random
import re
import shutil
import struct
import sys

# ------------------------------------------------------------------ filename escaping

# sample-file names escape every non-lowercase-ASCII char as %<code>% (dbtool_build._decode
# reverses it): 'R' -> %82%, '#' -> %35%, "'" -> %39%
def escape_filename(name):
    return ''.join(c if 'a' <= c <= 'z' else '%%%d%%' % ord(c) for c in name)


def _decode(name):
    return re.sub(r'%(\\d+)%', lambda m: chr(int(m.group(1))), name)


# ------------------------------------------------------------------ DDI model (.tree reader)

# Port of ddb-tools' utils/ddi_utils.py read path (no yaml/temp features, so no new
# dependency). Parses the PHDC/TDB/DBV/STA/ART/VQM blocks of a DBTool .tree and records,
# for every unit, the file position of each sample offset (as "pos=offset" hex strings)
# so the packer can rebase them into the new .ddb.


def _read_str(data):
    size = int.from_bytes(data.read(4), byteorder='little')
    return data.read(size).decode('latin-1')  # the DBTool writes latin-1, not utf-8


def _read_arr(data):
    assert data.read(4).decode() == 'ARR '
    int.from_bytes(data.read(4), byteorder='little')
    assert int.from_bytes(data.read(8), byteorder='little') == 1
    return data.read(4)


def _reverse_search(data, search, offset, limit=-1):
    if limit == -1:
        limit = offset - len(search)
    offset -= len(search)
    for i in range(offset, 0, -1):
        if data[i:i + len(search)] == search:
            return i
        if offset - i > limit:
            break
    return -1


class DDIModel:
    def __init__(self, ddi_bytes):
        self.ddi_bytes = ddi_bytes
        self.ddi_data = None
        self.sta_data = {}
        self.art_data = {}
        self.vqm_data = {}
        self.ddi_data_dict = {}

    def read(self):
        self.ddi_data = io.BytesIO(self.ddi_bytes)
        phdc_offset = self.ddi_bytes.find(b'PHDC')
        if phdc_offset >= 0:
            self.ddi_data.seek(phdc_offset)
            self.read_phdc()
        tdb_offset = self.ddi_bytes.find(b'\xff' * 8 + b'TDB ')
        if tdb_offset >= 0:
            try:
                self.ddi_data.seek(tdb_offset)
                self.read_tdb()
            except AssertionError:
                pass  # TDB timbre map is not needed for packing; some DBs vary here
        dbv_offset = self.ddi_bytes.find(b'\x00' * 8 + b'DBV ')
        if dbv_offset < 0:
            raise ValueError('no DBV block: not a singer database .tree?')
        self.ddi_data.seek(dbv_offset)
        self.read_dbv()
        sta_offset = self.ddi_bytes.find(b'\x00' * 8 + b'STA ')
        if sta_offset < 0:
            raise ValueError('no STA block: the DB has no stationaries?')
        sta_offset = _reverse_search(self.ddi_bytes, b'ARR ', sta_offset) - 8
        self.ddi_data.seek(sta_offset)
        self.sta_data = self.read_sta()
        art_offset = self.ddi_bytes.find(b'\x00' * 8 + b'ART ')
        if art_offset < 0:
            raise ValueError('no ART block: the DB has no articulations?')
        art_offset = _reverse_search(self.ddi_bytes, b'ARR ', art_offset) - 8
        self.ddi_data.seek(art_offset)
        self.art_data = self.read_art()
        vqm_offset = self.ddi_bytes.find(b'\xff' * 8 + b'VQM ')
        self.vqm_data = None
        if vqm_offset != -1:
            self.ddi_data.seek(vqm_offset)
            self.vqm_data = self.read_vqm()
        self.ddi_data_dict = {'sta': {}, 'art': {}}
        if self.vqm_data is not None:
            self.ddi_data_dict = {'vqm': {}, 'sta': {}, 'art': {}}
            vqm_dict = []
            for _idx, vqmp in self.vqm_data.items():
                vqm_dict.append({'snd': vqmp['snd'], 'epr': vqmp['epr'], 'pitch': vqmp['pitch1']})
            self.ddi_data_dict['vqm'] = vqm_dict
        sta_dict = {}
        for stau in self.sta_data.values():
            stau_dict = []
            for _idx, stap in stau['stap'].items():
                stau_dict.append({'snd': stap['snd'], 'epr': stap['epr'], 'pitch': stap['pitch1']})
            sta_dict[stau['phoneme']] = stau_dict
        self.ddi_data_dict['sta'] = {key: sta_dict[key] for key in sorted(sta_dict)}
        art_dict = {}
        for art in self.art_data.values():
            if 'artu' in art:
                for artu in art['artu'].values():
                    key = art['phoneme'] + ' ' + artu['phoneme']
                    art_dict[key] = []
                    for artp in artu['artp'].values():
                        art_dict[key].append({'snd': artp['snd'], 'snd_start': artp['snd_start'],
                                              'epr': artp['epr'], 'pitch': artp['pitch1']})
            if 'art' in art:
                for sub_art in art['art'].values():
                    if 'artu' in sub_art:
                        for artu in sub_art['artu'].values():
                            key = art['phoneme'] + ' ' + sub_art['phoneme'] + ' ' + artu['phoneme']
                            art_dict[key] = []
                            for artp in artu['artp'].values():
                                art_dict[key].append({'snd': artp['snd'],
                                                      'snd_start': artp['snd_start'],
                                                      'epr': artp['epr'], 'pitch': artp['pitch1']})
        self.ddi_data_dict['art'] = {key: art_dict[key] for key in sorted(art_dict)}

    def read_phdc(self):
        assert self.ddi_data.read(4).decode() == 'PHDC'
        phdc_size = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 4
        phoneme_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(phoneme_num):
            bytes_str = self.ddi_data.read(0x1F)
            assert bytes_str[-1] in [0, 1]
        assert self.ddi_data.read(4).decode() == 'PHG2'
        phg2_size = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        phg2_epr_guide_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(phg2_epr_guide_num):
            _read_str(self.ddi_data)
            temp_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(temp_num):
                self.ddi_data.read(4)
                _read_str(self.ddi_data)
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        epr_guide_size = phdc_size - phg2_size - 0x10 - 0x1F * phoneme_num - 4
        epr_guide_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        epr_guide_bytes = self.ddi_data.read(epr_guide_size)
        offset = 0
        for _i in range(epr_guide_num):
            offset += 0x24
            while offset < len(epr_guide_bytes) and epr_guide_bytes[offset] == 0:
                offset += 8
        assert offset == len(epr_guide_bytes)

    def read_tdb(self):
        assert self.ddi_data.read(8) == b'\xff' * 8
        assert self.ddi_data.read(4).decode() == 'TDB '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        tmm_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(tmm_num):
            assert self.ddi_data.read(8) == b'\xff' * 8
            assert self.ddi_data.read(4).decode() == 'TMM '
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
            self.ddi_data.read(4)
            str_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert str_num == 3
            for _j in range(str_num):
                assert self.ddi_data.read(8) == b'\xff' * 8
                assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') == 0
                _read_str(self.ddi_data)
                _read_str(self.ddi_data)
            assert _read_str(self.ddi_data) == 'timbre'

    def read_dbv(self):
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert self.ddi_data.read(4).decode() == 'DBV '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        self.ddi_data.read(4)

    def read_sta(self):
        sta_data = {}
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert self.ddi_data.read(4).decode() == 'STA '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        stau_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(stau_num):
            stau_data = {'phoneme': '', 'stap': {}}
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
            assert self.ddi_data.read(4).decode() == 'STAu'
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            stau_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert self.ddi_data.read(8) == b'\xff' * 8
            stap_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(stap_num):
                stap_data = {'snd': '', 'snd_length': '', 'epr': []}
                assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'STAp'
                self.ddi_data.read(4)
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
                stap_data['duration'] = struct.unpack('<d', self.ddi_data.read(8))[0]
                assert int.from_bytes(self.ddi_data.read(2), byteorder='little') == 1
                stap_data['pitch1'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['pitch2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['unknown2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['dynamics'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['tempo'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 2
                assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0x3D
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'SND'
                stap_data['snd_length'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'EpR'
                self.ddi_data.read(4)
                epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                epr_list = []
                for _k in range(epr_num):
                    epr_offset_pos = self.ddi_data.tell()
                    epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                    epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                stap_data['epr'] = epr_list
                stap_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                assert self.ddi_data.read(2) == b'\x01\x00'
                snd_identifier = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                snd_offset_pos = self.ddi_data.tell()
                snd_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                stap_data['snd'] = f'{snd_offset_pos:0>8x}={snd_offset:016x}_{snd_identifier:08x}'
                self.ddi_data.read(0x10)
                stap_idx = _read_str(self.ddi_data)
                assert stap_idx not in stau_data['stap']
                stau_data['stap'][stap_idx] = stap_data
                stau_data['stap'] = {k: stau_data['stap'][k] for k in sorted(stau_data['stap'])}
            stau_data['phoneme'] = _read_str(self.ddi_data)
            sta_data[stau_idx] = stau_data
        sta_data = {k: sta_data[k] for k in sorted(sta_data)}
        assert _read_str(self.ddi_data) == 'normal'
        assert _read_str(self.ddi_data) == 'stationary'
        return sta_data

    def read_art(self):
        total_art_data = {}
        int.from_bytes(self.ddi_data.read(8), byteorder='little')
        assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') != 0
        while True:
            start = self.ddi_data.read(8)
            if start not in [b'\x00' * 8, b'\xff' * 8]:
                offset = self.ddi_data.tell() - 8
                self.ddi_data.seek(offset)
                assert _read_str(self.ddi_data) == 'articulation'
                break
            assert self.ddi_data.read(4).decode() == 'ART '
            art_idx, art_data = self.read_art_block()
            total_art_data[art_idx] = art_data
        return {key: total_art_data[key] for key in sorted(total_art_data)}

    def read_art_block(self):
        art_data = {'phoneme': '', 'artu': {}, 'art': {}}
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        art_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        artu_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(artu_num):
            artu_data = {'phoneme': '', 'artp': {}}
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
            block_type = self.ddi_data.read(4).decode()
            if block_type == 'ART ':
                sub_art_idx, sub_art_data = self.read_art_block()
                art_data['art'][sub_art_idx] = sub_art_data
                continue
            assert block_type == 'ARTu'
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            artu_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') in [0, 1]
            self.ddi_data.read(4)
            assert self.ddi_data.read(4) == b'\xff' * 4
            artp_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(artp_num):
                artp_data = {'snd': '', 'snd_unknown': '', 'epr': []}
                dev_artp_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['dev_artp'] = f'{dev_artp_offset:0>8x}'
                assert self.ddi_data.read(4).decode() == 'ARTp'
                self.ddi_data.read(4)
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
                artp_data['duration'] = struct.unpack('<d', self.ddi_data.read(8))[0]
                assert int.from_bytes(self.ddi_data.read(2), byteorder='little') == 1
                artp_data['pitch1'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['pitch2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['unknown2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['dynamics'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['tempo'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 2
                artp_idx = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'SND'
                snd_len_phoneme = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                artp_data['snd_len_phoneme'] = f'{snd_len_phoneme:08x}'
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'EpR'
                loc = self.ddi_data.tell()
                try:
                    epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    epr_list = []
                    for _k in range(epr_num):
                        epr_offset_pos = self.ddi_data.tell()
                        epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                        epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                    artp_data['epr'] = epr_list
                    artp_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    assert self.ddi_data.read(2) == b'\x01\x00'
                except AssertionError:
                    self.ddi_data.seek(loc)
                    self.ddi_data.read(4)
                    epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    epr_list = []
                    for _k in range(epr_num):
                        epr_offset_pos = self.ddi_data.tell()
                        epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                        epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                    artp_data['epr'] = epr_list
                    artp_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    assert self.ddi_data.read(2) == b'\x01\x00'
                snd_identifier = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                snd_offset_pos = self.ddi_data.tell()
                snd_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['snd'] = f'{snd_offset_pos:08x}={snd_offset - 0x12:016x}_{snd_identifier:08x}'
                snd_offset2_pos = self.ddi_data.tell()
                snd_offset2 = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['snd_start'] = \
                    f'{snd_offset2_pos:08x}={snd_offset2 - 0x12:016x}_{snd_identifier:08x}'
                ddi_bytes = self.ddi_bytes[self.ddi_data.tell():self.ddi_data.tell() + 1024]
                align_length = ddi_bytes.find(b'default') - 4
                align_bytes = self.ddi_data.read(align_length)
                frame_align = []
                if align_length > 4:
                    align_group_num = int.from_bytes(align_bytes[0:4], byteorder='little')
                    align_io = io.BytesIO(align_bytes[4:])
                    for _ in range(align_group_num):
                        frame_align.append({
                            'start': int.from_bytes(align_io.read(4), byteorder='little'),
                            'end': int.from_bytes(align_io.read(4), byteorder='little'),
                            'start2': int.from_bytes(align_io.read(4), byteorder='little'),
                            'end2': int.from_bytes(align_io.read(4), byteorder='little'),
                        })
                else:
                    frame_align_group = []
                    for i in range(0, len(align_bytes), 4):
                        frame_align_group.append(int.from_bytes(align_bytes[i:i + 4], byteorder='little'))
                    frame_align.append(frame_align_group)
                artp_data['frame_align'] = frame_align
                assert _read_str(self.ddi_data) == 'default'
                assert artp_idx not in artu_data['artp']
                artu_data['artp'][artp_idx] = artp_data
                artu_data['artp'] = {k: artu_data['artp'][k] for k in sorted(artu_data['artp'])}
            artu_data['phoneme'] = _read_str(self.ddi_data)
            art_data['artu'][artu_idx] = artu_data
            art_data['artu'] = {k: art_data['artu'][k] for k in sorted(art_data['artu'])}
        art_data['art'] = {k: art_data['art'][k] for k in sorted(art_data['art'])}
        art_data['phoneme'] = _read_str(self.ddi_data)
        if not art_data['art']:
            del art_data['art']
        if not art_data.get('artu'):
            del art_data['artu']
        return art_idx, art_data

    def read_vqm(self):
        """Read VQM metadata, accepting both the DBTool and ddb-tools field widths.

        VQM records exist in two observed layouts: a 16-bit marker before the five floats,
        and a 32-bit marker used by ddb-tools' VQM stream writer. The old reader assumed
        only the former and could misalign every following field in imported growl banks.
        """
        start, end, records = _parse_vqm_chunk(self.ddi_bytes)
        self.ddi_data.seek(end)
        vqm_data = {}
        for rec in records:
            epr_list = ['%08x=%08x' % (pos, offset) for pos, offset in rec['epr']]
            vqm_data[int(rec['index'])] = {
                'snd': '%08x=%016x_%08x' % (rec['snd_pos'], rec['snd_offset'], rec['snd_id']),
                'epr': epr_list,
                'duration': rec['duration'],
                'pitch1': rec['pitch1'],
                'pitch2': rec['pitch2'],
                'unknown2': rec['unknown2'],
                'dynamics': rec['dynamics'],
                'tempo': rec['tempo'],
                'fs': rec['fs'],
            }
        return vqm_data


# ------------------------------------------------------------------ VOCALOID4 Growl (VQM) import

_VQM_MARKER = b'\xff' * 8 + b'VQM '
_VQM_FOOTER = b'\x05\x00\x00\x00voice'


def _parse_vqm_chunk(ddi_bytes):
    """Parse the VQM block without depending on the rest of a singer DDI.

    Returns (start, end, records). Pointer positions are absolute offsets in the DDI;
    offsets are absolute positions in the corresponding DDB. The parser deliberately
    tries both observed marker widths rather than trusting a single undocumented variant.
    """
    start = ddi_bytes.find(_VQM_MARKER)
    if start < 0:
        raise PackError('the DDI has no VQM (Growl) block')

    def parse(marker_width):
        stream = io.BytesIO(ddi_bytes)
        stream.seek(start)

        def take(n):
            b = stream.read(n)
            if len(b) != n:
                raise ValueError('truncated VQM block')
            return b

        def u32():
            return struct.unpack('<I', take(4))[0]

        def string():
            n = u32()
            if n > 4096:
                raise ValueError('unreasonable VQM string length')
            return take(n).decode('latin-1')

        if take(8) != b'\xff' * 8 or take(4) != b'VQM ':
            raise ValueError('invalid VQM header')
        if (u32(), u32(), u32(), u32()) != (0, 1, 0, 1):
            raise ValueError('unsupported VQM header')
        if take(8) != b'\xff' * 8 or take(4) != b'VQMu':
            raise ValueError('invalid VQMu header')
        if (u32(), u32(), u32()) != (0, 1, 0):
            raise ValueError('unsupported VQMu header')
        count = u32()
        if count > 100000 or u32() != count:
            raise ValueError('invalid VQM record count')
        records = []
        for _ in range(count):
            if take(8) != b'\xff' * 8 or take(4) != b'VQMp':
                raise ValueError('invalid VQMp header')
            if (u32(), u32(), u32()) != (0, 0, 1):
                raise ValueError('unsupported VQMp header')
            duration = struct.unpack('<d', take(8))[0]
            marker = take(marker_width)
            if int.from_bytes(marker, 'little') != 1:
                raise ValueError('invalid VQMp marker')
            pitch1, pitch2, unknown2, dynamics, tempo = struct.unpack('<5f', take(20))
            if u32() != 0 or take(4) != b'\xff' * 4:
                raise ValueError('invalid VQMp EpR header')
            epr_count = u32()
            if epr_count > 100000:
                raise ValueError('unreasonable VQM EpR count')
            epr = []
            for _j in range(epr_count):
                pos = stream.tell()
                epr.append((pos, struct.unpack('<Q', take(8))[0]))
            fs = u32()
            if take(2) != b'\x01\x00':
                raise ValueError('invalid VQM SND marker')
            snd_id = u32()
            snd_pos = stream.tell()
            snd_offset = struct.unpack('<Q', take(8))[0]
            if take(0x10) != b'\xff' * 0x10:
                raise ValueError('invalid VQM trailer marker')
            index = string()
            if string() != 'GROWL' or string() != 'vqm':
                raise ValueError('unexpected VQM record labels')
            records.append({
                'index': index, 'duration': duration, 'pitch1': pitch1, 'pitch2': pitch2,
                'unknown2': unknown2, 'dynamics': dynamics, 'tempo': tempo,
                'epr': epr, 'fs': fs, 'snd_id': snd_id, 'snd_pos': snd_pos,
                'snd_offset': snd_offset,
            })
        return stream.tell(), records

    errors = []
    for width in (4, 2):
        try:
            end, records = parse(width)
            return start, end, records
        except (AssertionError, UnicodeDecodeError, ValueError, struct.error) as ex:
            errors.append('%d-byte marker: %s' % (width, ex))
    raise PackError('could not parse VQM block (%s)' % '; '.join(errors))


def _extract_ddb_chunk(ddb_bytes, offset, tag):
    """Read and validate a length-prefixed FRM2 or SND chunk from a DDB."""
    if offset < 0 or offset + 8 > len(ddb_bytes):
        raise PackError('%s pointer 0x%x is outside the .ddb' % (tag.decode('ascii'), offset))
    if ddb_bytes[offset:offset + 4] != tag:
        raise PackError('%s pointer 0x%x does not point to a %s chunk' %
                        (tag.decode('ascii'), offset, tag.decode('ascii')))
    length = struct.unpack_from('<I', ddb_bytes, offset + 4)[0]
    if length < 8 or offset + length > len(ddb_bytes):
        raise PackError('invalid %s chunk length at 0x%x' % (tag.decode('ascii'), offset))
    return ddb_bytes[offset:offset + length]


def _validate_vqm_refs(ddi_bytes, ddb_bytes):
    """Return the number of VQM records, validating all referenced DDB chunks."""
    try:
        _start, _end, records = _parse_vqm_chunk(ddi_bytes)
    except PackError as ex:
        if 'has no VQM' in str(ex):
            return 0
        raise
    for rec in records:
        for _pos, offset in rec['epr']:
            _extract_ddb_chunk(ddb_bytes, offset, b'FRM2')
        _extract_ddb_chunk(ddb_bytes, rec['snd_offset'], b'SND ')
    return len(records)


def _write_vqm_string(value):
    raw = str(value).encode('latin-1')
    return struct.pack('<I', len(raw)) + raw


def _make_vqm_stream(records):
    """Serialize the VQM/VQMu/VQMp layout used by the open ddb-tools sta2vqm path."""
    out = bytearray(b'\xff' * 8 + b'VQM ')
    out += struct.pack('<4I', 0, 1, 0, 1)
    out += b'\xff' * 8 + b'VQMu' + struct.pack('<5I', 0, 1, 0, len(records), len(records))
    for rec in records:
        epr = rec['epr']
        out += b'\xff' * 8 + b'VQMp' + struct.pack('<3I', 0, 0, 1)
        out += struct.pack('<dI', float(rec['duration']), 1)
        # The reference VQM writer uses 224.0 in this opaque field, followed by the
        # DBTool stationary's remaining metadata values.
        out += struct.pack('<5f', 224.0, float(rec['pitch2']), float(rec['unknown2']),
                           float(rec['dynamics']), float(rec['tempo']))
        out += struct.pack('<I', 0) + b'\xff' * 4 + struct.pack('<I', len(epr))
        for offset in epr:
            out += struct.pack('<Q', int(offset))
        out += struct.pack('<I', int(rec['fs'])) + b'\x01\x00'
        out += struct.pack('<IQ', int(rec['snd_id']), int(rec['snd_offset']))
        out += b'\xff' * 0x10
        out += _write_vqm_string(rec['index'])
        out += _write_vqm_string('GROWL')
        out += _write_vqm_string('vqm')
    return bytes(out)


def add_vqm_from_grw_stationary(ddi_path, ddb_path, phoneme='Grw', log=print, vqm_indices=None):
    """Create native VQM data from Henkan FE's imported Grw stationary WAV.

    The DBTool has already analysed the WAV into an ordinary stationary entry. This
    routine copies only its first 100 FRM2 analysis frames and its one SND audio chunk
    into the packed DDB, then writes VQM metadata pointing at those compact payloads.
    No donor bank or donor DDB is read.
    """
    with open(ddi_path, 'rb') as f:
        ddi_bytes = f.read()
    # A dictionary mention alone does not mean a growl sample was actually built.
    if phoneme.encode('latin-1') not in ddi_bytes:
        return 0
    with open(ddb_path, 'rb') as f:
        ddb_bytes = f.read()

    try:
        model = DDIModel(ddi_bytes)
        model.read()
    except (AssertionError, ValueError, UnicodeDecodeError, struct.error) as ex:
        raise PackError('could not inspect the built DB for the %s growl stationary: %s' % (phoneme, ex)) from ex

    sta_entries = [entry for entry in model.sta_data.values() if entry.get('phoneme') == phoneme]
    if not sta_entries:
        raise PackError('the converted DB contains the %s dictionary phoneme but no stationary sample; '
                        'reconvert with a sustained growl WAV selected in Convert' % phoneme)

    if vqm_indices is not None:
        try:
            vqm_indices = [int(index) for index in vqm_indices]
        except (TypeError, ValueError) as ex:
            raise PackError('the VQM sample index map contains a non-integer index') from ex
        if any(index < 0 for index in vqm_indices) or len(vqm_indices) != len(set(vqm_indices)):
            raise PackError('the VQM sample index map must contain unique non-negative indexes')

    if vqm_indices is not None:
        stationary_count = sum(len(entry.get('stap', {})) for entry in sta_entries)
        if stationary_count != len(vqm_indices):
            raise PackError('VQM index map has %d source sample(s), but the DB contains %d Grw stationary '
                            'record(s); refusing to shift pitch indexes. Rebuild the DB from the same VQM folder.' %
                            (len(vqm_indices), stationary_count))

    appended = bytearray()
    records = []
    skipped = []
    for sta_entry in sta_entries:
        # DDIModel sorts these keys; use a numeric-aware order so source file indexes
        # 2 and 10 do not become 10 and 2 after DBTool serialization.
        sta_items = sorted(sta_entry.get('stap', {}).items(),
                           key=lambda item: (0, int(item[0])) if str(item[0]).isdigit()
                           else (1, str(item[0])))
        for sta_idx, sta_item in sta_items:
            epr_info = sta_item.get('epr', [])
            if len(epr_info) < 100:
                skipped.append('%s (%d EpR frames)' % (sta_idx, len(epr_info)))
                continue
            new_epr = []
            for info in epr_info[:100]:
                try:
                    _ddi_pos, offset_text = info.split('=', 1)
                    source_offset = int(offset_text, 16)
                except (ValueError, TypeError) as ex:
                    raise PackError('invalid %s EpR pointer in stationary %s: %r' %
                                    (phoneme, sta_idx, info)) from ex
                chunk = _extract_ddb_chunk(ddb_bytes, source_offset, b'FRM2')
                new_epr.append(len(ddb_bytes) + len(appended))
                appended.extend(chunk)

            try:
                _snd_pos, snd_name = sta_item['snd'].split('=', 1)
                snd_offset_text, snd_id_text = snd_name.split('_', 1)
                source_snd_offset = int(snd_offset_text, 16)
                snd_id = int(snd_id_text, 16)
            except (KeyError, ValueError, TypeError) as ex:
                raise PackError('invalid %s SND pointer in stationary %s' % (phoneme, sta_idx)) from ex
            real_snd_offset = _reverse_search(ddb_bytes, b'SND ', source_snd_offset)
            if real_snd_offset < 0:
                raise PackError('%s stationary %s SND pointer does not resolve to an SND chunk' %
                                (phoneme, sta_idx))
            snd_chunk = _extract_ddb_chunk(ddb_bytes, real_snd_offset, b'SND ')
            new_snd_offset = len(ddb_bytes) + len(appended)
            appended.extend(snd_chunk)
            records.append({
                'index': str(vqm_indices[len(records)] if vqm_indices is not None else len(records)),
                'duration': sta_item['duration'],
                'pitch2': sta_item['pitch2'],
                'unknown2': sta_item['unknown2'],
                'dynamics': sta_item['dynamics'],
                'tempo': sta_item['tempo'],
                'fs': sta_item['fs'],
                'snd_id': snd_id,
                'snd_offset': new_snd_offset,
                'epr': new_epr,
            })

    if not records:
        detail = ', '.join(skipped) or 'no usable stationary entries'
        raise PackError('the imported VQM WAV(s) did not produce the required 100 DBTool EpR analysis frames (%s). '
                        'Use sustained growled vowel samples of about 1–2 seconds, then rebuild the DB.' % detail)
    if vqm_indices is not None and len(records) != len(vqm_indices):
        raise PackError('VQM index map has %d source sample(s), but DBTool produced %d usable Grw stationary '
                        'record(s); refusing to shift pitch indexes. Rebuild the DB from the same VQM folder.' %
                        (len(vqm_indices), len(records)))
    if skipped:
        log('skipped %d short %s stationary record(s): %s' % (len(skipped), phoneme, ', '.join(skipped)))

    vqm_chunk = _make_vqm_stream(records)
    footer_pos = ddi_bytes.find(_VQM_FOOTER)
    if footer_pos < 0:
        raise PackError('the built DDI has no recognised voice footer; cannot insert native VQM data')
    dbv_start = ddi_bytes.find(b'\x00' * 8 + b'DBV ')
    dbv_count_pos = dbv_start + 0x18 if dbv_start >= 0 else -1
    if dbv_count_pos < 0 or dbv_count_pos + 4 > len(ddi_bytes):
        raise PackError('the built DDI has no valid DBV count field; cannot add VQM data')
    dbv_count = struct.unpack_from('<I', ddi_bytes, dbv_count_pos)[0]
    if dbv_count == 0xFFFFFFFF:
        raise PackError('the built DDI DBV count would overflow')
    ddi_bytes = (ddi_bytes[:dbv_count_pos] + struct.pack('<I', dbv_count + 1) +
                 ddi_bytes[dbv_count_pos + 4:])
    footer_pos = ddi_bytes.find(_VQM_FOOTER)
    ddi_new = ddi_bytes[:footer_pos] + vqm_chunk + ddi_bytes[footer_pos:]
    ddb_new = ddb_bytes + bytes(appended)

    try:
        actual = _validate_vqm_refs(ddi_new, ddb_new)
    except PackError as ex:
        raise PackError('generated VQM failed its post-write validation: %s' % ex) from ex
    if actual != len(records):
        raise PackError('generated VQM verification mismatch: expected %d records, found %d' %
                        (len(records), actual))

    def atomic_write(path, data):
        import tempfile
        directory = os.path.dirname(os.path.abspath(path))
        fd, temp_path = tempfile.mkstemp(prefix='.henkan-vqm-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    atomic_write(ddb_path, ddb_new)
    atomic_write(ddi_path, ddi_new)
    log('created %d native VQM Growl record(s) from %s stationary (%d FRM2 frames per record; %d bytes added)' %
        (actual, phoneme, 100, len(appended)))
    return actual


def strip_vqm_from_ddi(ddi_path, log=print):
    """Remove an unusable or version-incompatible VQM node from a DDI."""
    with open(ddi_path, 'rb') as f:
        ddi_bytes = f.read()
    try:
        start, end, records = _parse_vqm_chunk(ddi_bytes)
    except PackError as ex:
        if 'has no VQM' in str(ex):
            return 0
        raise
    dbv_start = ddi_bytes.find(b'\x00' * 8 + b'DBV ')
    count_pos = dbv_start + 0x18 if dbv_start >= 0 else -1
    if count_pos < 0 or count_pos + 4 > len(ddi_bytes):
        raise PackError('cannot remove VQM: the DDI DBV count field is missing')
    count = struct.unpack_from('<I', ddi_bytes, count_pos)[0]
    if count < 1:
        raise PackError('cannot remove VQM: the DDI DBV count is already zero')
    ddi_bytes = ddi_bytes[:start] + ddi_bytes[end:]
    ddi_bytes = ddi_bytes[:count_pos] + struct.pack('<I', count - 1) + ddi_bytes[count_pos + 4:]
    with open(ddi_path, 'wb') as f:
        f.write(ddi_bytes)
    log('removed %d VQM entry/entries from a package that cannot use them' % len(records))
    return len(records)


class PackError(RuntimeError):
    pass


def pack_db(db_path, dst_dir=None, log=print, progress=None):
    """Pack a built singer DB (folder/Name: Name.tree + Name/voice/...) into Name.ddb + Name.ddi.

    Returns (ddb_path, ddi_path). progress(done_units, total_units) is called per unit."""
    db_path = os.path.normpath(os.path.abspath(db_path))
    singer_path, singer_name = db_path, os.path.basename(db_path)
    tree_path = singer_path + '.tree'
    if not os.path.exists(tree_path):
        raise PackError('%s.tree does not exist: build the singer DB first' % singer_path)
    if dst_dir is None:
        dst_dir = os.path.join(os.path.dirname(singer_path), 'package')
    os.makedirs(dst_dir, exist_ok=True)
    ddi_path = os.path.join(dst_dir, singer_name + '.ddi')
    ddb_path = os.path.join(dst_dir, singer_name + '.ddb')
    for p in (ddi_path, ddb_path):
        if os.path.exists(p):
            os.remove(p)
    with open(tree_path, 'rb') as f:
        ddi_bytes = f.read()
    ddi_data = io.BytesIO(ddi_bytes)
    log('reading the .tree (DDI)...')
    try:
        model = DDIModel(ddi_bytes)
        model.read()
    except (AssertionError, ValueError) as ex:
        raise PackError('could not read %s as a singer database: %s' % (tree_path, ex))
    art = model.ddi_data_dict['art']
    n_units = sum(len(v) for v in art.values()) + sum(len(v) for v in model.ddi_data_dict['sta'].values())
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], n_units)

    log('packing %d units...' % n_units)
    with open(ddb_path, 'wb') as ddb_f:
        for cvvc, art_items in art.items():
            phonemes = cvvc.split(' ')
            if len(phonemes) == 3:
                art_file = os.path.join(singer_path, 'voice', 'articulation', escape_filename(phonemes[0]),
                                        escape_filename(phonemes[1]), escape_filename(phonemes[2]))
            elif len(phonemes) == 2:
                art_file = os.path.join(singer_path, 'voice', 'articulation', escape_filename(phonemes[0]),
                                        escape_filename(phonemes[1]))
            else:
                raise PackError('odd articulation key %r in the .tree' % cvvc)
            if not os.path.exists(art_file):
                raise PackError('articulation file missing: %s (the DB build left units out?)' % art_file)
            with open(art_file, 'rb') as art_f:
                art_bytes = art_f.read()
            with io.BytesIO(art_bytes) as art_data:
                for art_item in art_items:
                    for epr_info in art_item['epr']:
                        ddi_epr_pos, epr_offset = epr_info.split('=')
                        ddb_epr_offset = ddb_f.tell()
                        ddi_epr_pos, epr_offset = int(ddi_epr_pos, 16), int(epr_offset, 16)
                        art_data.seek(epr_offset)
                        if art_data.read(4) != b'FRM2':
                            raise PackError('articulation file is broken: %s' % art_file)
                        frm_len = int.from_bytes(art_data.read(4), byteorder='little')
                        ddb_f.write(art_bytes[epr_offset:epr_offset + frm_len])
                        ddi_data.seek(ddi_epr_pos)
                        ddi_data.write(ddb_epr_offset.to_bytes(8, byteorder='little'))
                    ddi_snd_pos, t = art_item['snd'].split('=')
                    snd_offset, _ = t.split('_')
                    ddi_snd_pos2, t = art_item['snd_start'].split('=')
                    snd_offset2, _ = t.split('_')
                    ddi_snd_pos, snd_offset = int(ddi_snd_pos, 16), int(snd_offset, 16)
                    ddi_snd_pos2, snd_offset2 = int(ddi_snd_pos2, 16), int(snd_offset2, 16)
                    offset2_delta = snd_offset2 - snd_offset
                    art_data.seek(snd_offset)
                    if art_data.read(4) != b'SND ':
                        raise PackError('articulation file is broken: %s' % art_file)
                    snd_len = int.from_bytes(art_data.read(4), byteorder='little')
                    snd_start = snd_offset + snd_len
                    ddb_snd_offset = ddb_f.tell() + 0x12
                    snd_bytes = art_bytes[snd_offset:snd_start]
                    if snd_bytes[0:4] != b'SND ':
                        raise PackError('articulation file is broken: %s' % art_file)
                    ddb_f.write(snd_bytes)
                    ddi_data.seek(ddi_snd_pos)
                    ddi_data.write(ddb_snd_offset.to_bytes(8, byteorder='little'))
                    ddi_data.write((ddb_snd_offset + offset2_delta).to_bytes(8, byteorder='little'))
                    tick()
        for sta_info in model.sta_data.values():
            phoneme = sta_info['phoneme']
            for sta_idx, sta_item in sta_info['stap'].items():
                sta_file = os.path.join(singer_path, 'voice', 'stationary', 'normal',
                                        escape_filename(phoneme), escape_filename(str(sta_idx)))
                if not os.path.exists(sta_file):
                    raise PackError('stationary file missing: %s' % sta_file)
                with open(sta_file, 'rb') as sta_f:
                    sta_bytes = sta_f.read()
                with io.BytesIO(sta_bytes) as sta_data:
                    for epr_info in sta_item['epr']:
                        ddi_epr_pos, epr_offset = epr_info.split('=')
                        ddb_epr_offset = ddb_f.tell()
                        ddi_epr_pos, epr_offset = int(ddi_epr_pos, 16), int(epr_offset, 16)
                        sta_data.seek(epr_offset)
                        if sta_data.read(4) != b'FRM2':
                            raise PackError('stationary file is broken: %s' % sta_file)
                        frm_len = int.from_bytes(sta_data.read(4), byteorder='little')
                        ddb_f.write(sta_bytes[epr_offset:epr_offset + frm_len])
                        ddi_data.seek(ddi_epr_pos)
                        ddi_data.write(ddb_epr_offset.to_bytes(8, byteorder='little'))
                    ddi_snd_pos, snd_name = sta_item['snd'].split('=')
                    snd_offset, _snd_id = snd_name.split('_')
                    ddi_snd_pos, snd_offset = int(ddi_snd_pos, 16), int(snd_offset, 16)
                    real_snd_offset = _reverse_search(sta_bytes, b'SND ', snd_offset)
                    sta_data.seek(real_snd_offset)
                    if sta_data.read(4) != b'SND ':
                        raise PackError('stationary file is broken: %s' % sta_file)
                    snd_len = int.from_bytes(sta_data.read(4), byteorder='little')
                    snd_start = real_snd_offset + snd_len
                    delta_snd_offset = snd_offset - real_snd_offset
                    ddb_snd_offset = ddb_f.tell() + delta_snd_offset
                    ddb_f.write(sta_bytes[real_snd_offset:snd_start])
                    ddi_data.seek(ddi_snd_pos)
                    ddi_data.write(ddb_snd_offset.to_bytes(8, byteorder='little'))
                    tick()
    with open(ddi_path, 'wb') as f:
        f.write(ddi_data.getbuffer())
    log('packed: %s + %s' % (ddb_path, ddi_path))
    return ddb_path, ddi_path

# ------------------------------------------------------------------ VVD files

# Substitution cipher from VVD Editor Plus (atonyxu/vvdeditor): plaintext char -> file char.
# Chars outside the map pass through unchanged. Verified: decoding the real KAITO.vvd from
# a VOCALOID install gives '"ID" = "VOCALOID VIRTUAL VOICE"' etc.
_VVD_ENC = {
    '[': 'A', 'A': '[', 'E': '_', '_': 'E', 'R': 'H', 'I': 'S', 'U': 'O', 'P': 'J',
    'J': 'P', 'Q': 'K', 'K': 'Q', 'M': 'W', 'W': 'M', 'S': 'I', 'D': '^', '^': 'D',
    'F': '\\', 'G': ']', ']': 'G', '\\': 'F', 'H': 'R', 'O': 'U', 'Z': '@', '@': 'Z',
    'X': 'B', 'B': 'X', 'Y': 'C', 'C': 'Y', 'V': 'L', 'L': 'V', 'N': 'T', 'T': 'N',
    '1': '+', '2': '(', '3': ')', '4': '.', '5': '/', '6': ',', '7': '-', '8': '"',
    '9': '#', '0': '*', '+': '1', '(': '2', ')': '3', '.': '4', '/': '5', ',': '6',
    '-': '7', '"': '8', '#': '9', '*': '0', '=': "'", "'": '=', 'a': '{', '{': 'a',
    'r': 'h', 'h': 'r', 't': 'n', 'n': 't', 'u': 'o', 'o': 'u', '}': 'g', 'g': '}',
    'p': 'j', 'j': 'p', 'q': 'k', 'k': 'q', 's': 'i', 'i': 's', '\x7f': 'e', 'e': '\x7f',
    'd': '~', '~': 'd', 'x': 'b', 'b': 'x', 'y': 'c', 'c': 'y', 'v': 'l', 'l': 'v',
    'z': '`', '`': 'z', 'w': 'm', 'm': 'w', '|': 'f', 'f': '|',
}
_VVD_DEC = {v: k for k, v in _VVD_ENC.items()}

VVD_TEMPLATE = ('"ID" = "VOCALOID VIRTUAL VOICE"\n'
                '"FORMAT" = "{fmt}"\n'
                '"VOICEIDSTR" = "{compid}"\n'
                '"VOICENAME" = "{name}"\n'
                '"Breathiness" = "0"\n'
                '"Brightness" = "0"\n'
                '"Clearness" = "0"\n'
                '"Opening" = "0"\n'
                '"Gender Factor" = "0"\n'
                '{growl}')


def encode_vvd(text):
    """Plaintext VVD metadata -> encoded file text."""
    return ''.join(_VVD_ENC.get(c, c) for c in text)


def decode_vvd(data):
    """Encoded .vvd file text/bytes -> plaintext metadata."""
    if isinstance(data, bytes):
        data = data.decode('latin-1')
    return ''.join(_VVD_DEC.get(c, c) for c in data)


def make_vvd(compid, name, fmt='4.0.0.0', growl=False, voice_params=None):
    """Encoded .vvd metadata. Singer Editor offsets are optional; Growl needs verified VQM data."""
    from .voiceparams import normalize_voice_profile, VOICE_PROFILE_VVD_KEYS
    profile = normalize_voice_profile(voice_params)
    text = VVD_TEMPLATE.format(fmt=fmt, compid=compid, name=name,
                               growl='"Growl" = "1"\n' if growl else '')
    for key, vvd_key in VOICE_PROFILE_VVD_KEYS.items():
        text = text.replace('\"%s\" = \"0\"' % vvd_key,
                            '\"%s\" = \"%d\"' % (vvd_key, profile[key]))
    return encode_vvd(text).replace('\n', '\r\n')


def vvd_info(path):
    """{'compid': ..., 'name': ...} from a .vvd file."""
    plain = decode_vvd(open(path, 'rb').read())
    info = {}
    m = re.search(r'"VOICEIDSTR"\s*=\s*"([^"]*)"', plain)
    if m:
        info['compid'] = m.group(1)
    m = re.search(r'"VOICENAME"\s*=\s*"([^"]*)"', plain)
    if m:
        info['name'] = m.group(1)
    return info

# ------------------------------------------------------------------ CompIDs and editors

from .compid import (COMPID_RE, COMPID_ALPHABET, CompIDError, compids_in_use, registered_name,  # noqa: F401
                     normalize as _normalize_compid, random_compid, status_of)


def new_compid(rng=None):
    """A fresh unique 16-char CompID: 'B' + 15 unambiguous chars, checked against the registry."""
    try:
        return random_compid(rng)
    except CompIDError as ex:
        raise PackError(str(ex)) from ex


def check_compid(compid, name=None):
    """compid, normalized; raises PackError if malformed or taken. An ID registered to a bank of this same
    name is that bank's own: repackaging an update reuses it."""
    ok, msg = status_of(compid)
    cid = (compid or '').strip().upper()
    if not ok and not (name and COMPID_RE.match(cid) and registered_name(cid) == name):
        raise PackError(msg)
    return cid


# editor kinds: reg roots are (files branch, mirror branch or None)
EDITOR_KINDS = {
    'V4': {'label': 'VOCALOID4 editor', 'exes': ('VOCALOID4.exe',),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE41', 'HKLM\\SOFTWARE\\POCALOID4\\DATABASE41')},
    'V4FE-dev': {'label': 'VOCALOID4FE developer editor', 'exes': ('VoiceLoader.exe', 'VOCALOID4_Dev.exe'),
                 'reg': ('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE41', 'HKLM\\SOFTWARE\\POCALOID4\\DATABASE41')},
    'V3': {'label': 'VOCALOID3 editor', 'exes': ('VOCALOID3.exe',),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID3\\DATABASE\\VOICE3', None)},
    'V2': {'label': 'VOCALOID2 editor', 'exes': ('VOCALOID2.exe', 'SingerEditor2.exe'),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID2\\DATABASE\\VOICE', None)},
}

KNOWN_EDITOR_ROOTS = [
    os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'), d)
    for d in ('VOCALOID4\\Editor', 'VOCALOID4FE', 'VOCALOID3', 'VOCALOID2', 'VOCALOID')
]


def guess_editor(path):
    """{'kind', 'root', 'label'} for an editor folder you point at."""
    path = os.path.normpath(os.path.abspath(path))
    if not os.path.isdir(path):
        raise PackError('editor folder does not exist: %s' % path)
    try:
        files = {f.lower() for f in os.listdir(path)}
    except OSError:
        files = set()
    for kind, spec in EDITOR_KINDS.items():
        if any(e.lower() in files for e in spec['exes']):
            return {'kind': kind, 'root': path, 'label': spec['label']}
    low = path.lower()
    if 'vocaloid4' in low:
        kind = 'V4FE-dev' if 'voice' in files and 'loader' in low else 'V4'
    elif 'vocaloid3' in low:
        kind = 'V3'
    elif 'vocaloid2' in low or low.rstrip('\\/').endswith('vocaloid'):
        kind = 'V2'
    else:
        raise PackError('no VOCALOID editor recognized in %s (point at its install folder)' % path)
    return {'kind': kind, 'root': path, 'label': EDITOR_KINDS[kind]['label']}


def find_editors():
    """Detected editor installs, dev editor first (it is this tool's home turf)."""
    found, seen = [], set()
    for root in KNOWN_EDITOR_ROOTS:
        if os.path.isdir(root) and root not in seen:
            try:
                found.append(guess_editor(root))
                seen.add(root)
            except PackError:
                pass
    fe = [e for e in found if e['kind'] == 'V4FE-dev']
    return fe + [e for e in found if e not in fe]


def v4_bank_dir_candidates():
    """Known VOCALOID4 library roots, ordered by current Henkan FE preference.

    Most installations use ``Program Files (x86)/VoiceDB``. Some installations
    (and older setups) use ``Common Files / VOCALOID4 / voicelib``; keep that
    location as a supported fallback instead of forcing users to move banks.
    """
    pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    return (
        os.path.join(pf86, 'VoiceDB'),
        os.path.join(pf86, 'Common Files', 'VOCALOID4', 'voicelib'),
    )


def default_bank_dir(editor, name):
    """Return a suitable library root; installed banks live in CompID subfolders.

    For V4, prefer the standard VoiceDB directory when it exists. If it does
    not, reuse an existing Common Files / VOCALOID4 / voicelib directory. On a clean
    system, default to VoiceDB. The destination field remains editable so users
    can choose either supported root explicitly.
    """
    if editor['kind'] in ('V4', 'V4FE-dev'):
        candidates = v4_bank_dir_candidates()
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return candidates[0]
    return os.path.join(editor['root'], 'voicedbdir')

# ------------------------------------------------------------------ packaging and install

INSTALL_BAT = ('set DB_COMPID={compid}\r\n'
               'set DB_NAME={name}\r\n'
               'set DB_VERSION={version}\r\n'
               '\r\n'
               'cd /d %~dp0\r\n'
               'set PKG=%cd%\r\n'
               'set DB_PATH={dest}\r\n'
               'echo installing to %DB_PATH%\\%DB_COMPID%\r\n'
               'if not exist "%DB_PATH%\\%DB_COMPID%" mkdir "%DB_PATH%\\%DB_COMPID%"\r\n'
               'copy /Y "%PKG%\\{base}.ddb" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               'copy /Y "%PKG%\\{base}.ddi" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               'copy /Y "%PKG%\\{base}.vvd" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               '{slot_lines}'
               '\r\n'
               'if %PROCESSOR_ARCHITECTURE%==x86 (set reg3264= ) else (set reg3264=/reg:32)\r\n'
               '{reg_lines}'
               '{cleanup_lines}'
               'echo done.\r\n')

UNINSTALL_BAT = ('set DB_COMPID={compid}\r\n'
                 '\r\n'
                 'if %PROCESSOR_ARCHITECTURE%==x86 (set reg3264= ) else (set reg3264=/reg:32)\r\n'
                 '{reg_lines}'
                 'echo registration removed. The bank folder was left in place.\r\n')

DEV_SLOT_APPEND_BAT = ('@echo off\r\n'
                       'set INI={ini}\r\n'
                       'type "{slot_file}" >> "%INI%"\r\n'
                       'echo dev slot added to %INI%\r\n')


def _reg_add_lines(branches, compid, values, delete=False):
    lines = []
    for branch in branches:
        key = '%s\\%s' % (branch, compid)
        if delete:
            lines.append('reg DELETE "%s" /F %%reg3264%%\r\n' % key)
            continue
        for value, typ, data in values:
            if value is None:
                lines.append('reg ADD "%s" /F /ve /T %s /D "%s" %%reg3264%%\r\n' % (key, typ, data))
            else:
                lines.append('reg ADD "%s" /F /V %s /T %s /D "%s" %%reg3264%%\r\n'
                             % (key, value, typ, data))
    return ''.join(lines)


def install_script(compid, name, dest, files_base, kind, db_version='4', uninstall=False):
    """The elevated .bat that copies the bank and writes the registry (same shape as VVD Editor's)."""
    if kind == 'V2':
        branches = (EDITOR_KINDS['V2']['reg'][0],)
        values = [('INSTALLDIR', 'REG_SZ', dest), ('NAME', 'REG_SZ', 'VOCALOID2 Voice DB (%s)' % name),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'), ('INSTALLED', 'REG_DWORD', '1'),
                  ('DRP', 'REG_SZ', '050013'), ('KEYS\\default', 'REG_SZ', '1145141919810')]
        slot_lines = ''
    elif kind == 'V3':
        branches = (EDITOR_KINDS['V3']['reg'][0],)
        voice_folder = dest.rstrip('\\/') + '\\' + compid
        values = [('PATH', 'REG_SZ', voice_folder), ('NAME', 'REG_SZ', 'VOCALOID3 Library (%s)' % name),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'), ('INSTALLED', 'REG_DWORD', '1'),
                  ('DRP', 'REG_SZ', '0B0032'), ('KEYS\\default', 'REG_SZ', '1145141919810')]
        slot_lines = ''
    else:
        # mirror a working commercial install (MIKU_V4X): DBVERSION dword, DRP 0B0A51, no KEYS
        branches = EDITOR_KINDS['V4']['reg']
        voice_folder = dest.rstrip('\\/') + '\\' + compid
        values = [('PATH', 'REG_SZ', voice_folder), ('DRP', 'REG_SZ', '0B0A51'),
                  ('INSTALLED', 'REG_DWORD', '1'), ('DBVERSION', 'REG_DWORD', '4'),
                  ('NAME', 'REG_SZ', 'VOCALOID%s Library (%s)' % (db_version, name)),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'),
                  ('BankName', 'REG_SZ', name)]
        slot_lines = ''
    if uninstall:
        return UNINSTALL_BAT.format(compid=compid,
                                    reg_lines=_reg_add_lines(branches, compid, values, delete=True))
    cleanup_lines = ''
    if kind not in ('V2', 'V3'):
        # self-heal the first-generation layout: files sat in the bank folder root (the editor
        # only looks in the CompID subfolder) and a placeholder KEYS value was written
        cleanup_lines = ('del "%DB_PATH%\\{base}.ddb" "%DB_PATH%\\{base}.ddi" '
                         '"%DB_PATH%\\{base}.vvd" 2>nul\r\n').format(DB_PATH=dest, base=files_base)
        cleanup_lines += ''.join('reg DELETE "%s\\%s\\KEYS" /F %%reg3264%% 2>nul\r\n'
                                 % (b, compid) for b in branches)
    return INSTALL_BAT.format(compid=compid, name=name, version=db_version, dest=dest,
                              base=files_base, slot_lines=slot_lines, cleanup_lines=cleanup_lines,
                              reg_lines=_reg_add_lines(branches, compid, values))


def _load_vqm_sample_indexes(db_path):
    """Read the source vqm_N.wav indexes carried through DBTool build, if present."""
    path = os.path.abspath(db_path) + '.henkan-vqm.json'
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as ex:
        raise PackError('could not read the VQM sample index map %s: %s' % (path, ex)) from ex
    if not isinstance(data, dict) or data.get('format_version') != 1 or not isinstance(data.get('samples'), list):
        raise PackError('the VQM sample index map has an unsupported format: %s' % path)
    indexes = []
    for sample in data['samples']:
        if not isinstance(sample, dict) or 'index' not in sample:
            raise PackError('the VQM sample index map contains an invalid sample entry')
        try:
            indexes.append(int(sample['index']))
        except (TypeError, ValueError) as ex:
            raise PackError('the VQM sample index map contains a non-integer index') from ex
    if not indexes or any(index < 0 for index in indexes) or len(indexes) != len(set(indexes)):
        raise PackError('the VQM sample index map must contain unique non-negative indexes')
    return indexes


def make_package(db_path, out_dir=None, name=None, compid=None, db_version='4', log=print,
                 voice_params=None):
    """Pack a built DB, converting an imported Grw stationary WAV into native V4 VQM data.

    The WAV is selected in Convert and is processed by the DBTool as the special stationary
    phoneme ``Grw``. The package step copies only that stationary's frame-analysis and audio
    chunks into VQM; it never imports a donor bank or donor DDB.

    Returns the package folder. Also writes install.bat/uninstall.bat next to the files."""
    from .voiceparams import normalize_voice_profile, VOICE_PROFILE_DEFAULTS
    try:
        voice_params = normalize_voice_profile(voice_params)
    except ValueError as ex:
        raise PackError(str(ex)) from ex
    db_version = str(db_version)
    if db_version not in ('2', '3', '4'):
        raise PackError('unsupported database version: %s' % db_version)
    if db_version != '4' and any(voice_params.values()):
        raise PackError('BRE/BRI/CLE/GEN Singer Editor offsets are only supported for VOCALOID4 packages')
    db_path = os.path.normpath(os.path.abspath(db_path))
    name = name or os.path.basename(db_path.rstrip('\\/')) or 'Singer'
    name = re.sub(r'[^A-Za-z0-9 _-]+', '_', name).strip() or 'Singer'
    compid = check_compid(compid, name) if compid else new_compid()
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(db_path), 'package')
    package = os.path.join(os.path.abspath(out_dir), compid)
    if os.path.exists(package):
        raise PackError('package folder already exists: %s' % package)
    os.makedirs(package)
    try:
        vqm_indices = _load_vqm_sample_indexes(db_path)
        ddb_src, ddi_src = pack_db(db_path, package, log=log)
        # pack_db handles articulation/stationary payloads, not source-tree VQM offsets.
        # Remove any inherited VQM node, then rebuild it only from Henkan FE's own Grw
        # stationary WAV after DBTool has analysed that recording.
        stripped = strip_vqm_from_ddi(ddi_src, log=log)
        if stripped:
            log('discarded inherited VQM metadata; only the converted Grw sample is used')
        with open(ddi_src, 'rb') as f:
            packed_ddi = f.read()
        has_grw = b'Grw' in packed_ddi
        if has_grw and db_version != '4':
            raise PackError('the converted DB contains a Grw growl sample, but native VQM is VOCALOID4-only; '
                            'pack this database with --db-version 4')
        vqm_count = add_vqm_from_grw_stationary(ddi_src, ddb_src, phoneme='Grw', log=log, vqm_indices=vqm_indices) if has_grw else 0
        if vqm_count:
            with open(ddi_src, 'rb') as f:
                final_ddi = f.read()
            with open(ddb_src, 'rb') as f:
                final_ddb = f.read()
            vqm_count = _validate_vqm_refs(final_ddi, final_ddb)
        else:
            log('Growl disabled: no imported Grw WAV stationary was found in the converted DB')
        base = os.path.splitext(os.path.basename(ddb_src))[0]
        for ext in ('.ddb', '.ddi'):
            os.rename(os.path.join(package, base + ext), os.path.join(package, name + ext))
        with open(os.path.join(package, name + '.vvd'), 'w', encoding='latin-1', newline='') as f:
            f.write(make_vvd(compid, name, fmt='%s.0.0.0' % db_version,
                             growl=(db_version == '4' and vqm_count > 0),
                             voice_params=voice_params))
        if any(voice_params[k] for k in VOICE_PROFILE_DEFAULTS):
            log('Singer Editor offsets: %s' % ', '.join('%s=%d' % (k, v) for k, v in voice_params.items()))
        log('voicebank package: %s' % package)
        return package
    except Exception:
        shutil.rmtree(package, ignore_errors=True)
        raise

def _package_files(package):
    """(compid, base, {ext: path}) for a package folder."""
    compid = os.path.basename(os.path.normpath(os.path.abspath(package)))
    if not COMPID_RE.match(compid):
        raise PackError('%s is not a package folder (it should be named by its CompID)' % package)
    found = {}
    for fn in os.listdir(package):
        _base, ext = os.path.splitext(fn)
        if ext.lower() in ('.ddb', '.ddi', '.vvd'):
            found.setdefault(_base, {})[ext.lower()] = os.path.join(package, fn)
    if not found:
        raise PackError('no .ddb/.ddi/.vvd in %s' % package)
    base = sorted(found)[0]
    got = found[base]
    if '.ddb' not in got or '.vvd' not in got:
        raise PackError('%s has no %s.ddb/.vvd pair' % (package, base))
    info = vvd_info(got['.vvd'])
    return compid, base, got, info


def write_install_scripts(package, editor, dest=None, compid=None, name=None, db_version='4', log=print):
    """Write install.bat/uninstall.bat into the package. Returns (install_bat, dest)."""
    compid0, base, _got, info = _package_files(package)
    compid = check_compid(compid, name or info.get('name') or base) if compid else compid0
    if compid != compid0:
        log('warning: installing under a different CompID than the package folder name')
    name = name or info.get('name') or base
    if dest is None:
        dest = default_bank_dir(editor, name)
    dest = os.path.normpath(os.path.abspath(dest))
    install_bat = os.path.join(package, 'install.bat')
    uninstall_bat = os.path.join(package, 'uninstall.bat')
    with open(install_bat, 'w', encoding='ascii', newline='') as f:
        f.write(install_script(compid, name, dest, base, editor['kind'], db_version))
    with open(uninstall_bat, 'w', encoding='ascii', newline='') as f:
        f.write(install_script(compid, name, dest, base, editor['kind'], db_version, uninstall=True))
    return install_bat, dest


def install_package(package, editor, dest=None, compid=None, name=None, db_version='4', log=print):
    """Stage install.bat/uninstall.bat and report what the elevated install will do."""
    install_bat, dest = write_install_scripts(package, editor, dest, compid, name, db_version, log)
    compid0, base, _got, _info = _package_files(package)
    log('bank folder: %s' % dest)
    log('registry: %s\\%s (+ mirror)' % (EDITOR_KINDS[editor['kind']]['reg'][0], compid or compid0))
    log('run as administrator: %s' % install_bat)
    return install_bat, dest


def dev_ini_path(editor_root):
    return os.path.join(editor_root, 'DB_Dev.ini')


def read_dev_slots(ini_path):
    """[(n, voice_name, tree_path)] from a dev editor DB_Dev.ini."""
    slots = []
    try:
        text = open(ini_path, encoding='latin-1').read()
    except OSError:
        return slots
    for m in re.finditer(r'\[VVoice (\d+)\]([^\[]*)', text):
        body = m.group(2)
        gv = lambda k: re.search(r'^%s\s*=\s*"?([^"]*)"?\s*$' % k, body, re.M)
        vv = gv('VVoiceName')
        dp, dn = gv('DBPath'), gv('DBName')
        tree = os.path.join(dp.group(1), dn.group(1)) if dp and dn else ''
        slots.append((int(m.group(1)), vv.group(1) if vv else '', tree))
    return slots


def write_dev_slot(editor_root, db_path, name=None, log=print, voice_params=None):
    """Register a loose built DB as a new [VVoice N] slot in the dev editor's DB_Dev.ini.

    The ini lives in Program Files, so this writes a snippet + an elevated .bat that appends it.
    ``voice_params`` are signed BRE/BRI/CLE/GEN Singer Editor offsets (-127..127)."""
    from .voiceparams import normalize_voice_profile, VOICE_PROFILE_INI_KEYS
    try:
        voice_params = normalize_voice_profile(voice_params)
    except ValueError as ex:
        raise PackError(str(ex)) from ex
    ini = dev_ini_path(editor_root)
    if not os.path.exists(ini):
        raise PackError('no DB_Dev.ini in %s: not a developer editor?' % editor_root)
    db_path = os.path.normpath(os.path.abspath(db_path))
    if not os.path.exists(db_path + '.tree'):
        raise PackError('%s.tree does not exist: build the singer DB first' % db_path)
    name = name or os.path.basename(db_path.rstrip('\\/')) or 'Singer'
    # BankSelect is the language (0 Japanese, 1 English, 4 Chinese ...): the DB's singer.inf says which
    bank = 0
    try:
        m = re.search(r'Language\s*=\s*"?(\w+)', open(os.path.join(db_path, 'singer.inf'), encoding='latin-1').read())
        bank = {'japanese': 0, 'english': 1, 'korean': 2, 'spanish': 3, 'chinese': 4}.get(m.group(1).lower(), 0)
    except (OSError, AttributeError):
        pass
    slots = read_dev_slots(ini)
    n = max([s[0] for s in slots] + [-1]) + 1
    pcs = set()
    for m in re.finditer(r'ProgramChange\s*=\s*(\d+)', open(ini, encoding='latin-1').read()):
        pcs.add(int(m.group(1)))
    pc = max(pcs) + 1 if pcs else 0
    folder, tree = os.path.dirname(db_path), os.path.basename(db_path)
    profile_lines = ''.join('%s = %d\r\n' % (ini_key, voice_params[key])
                           for key, ini_key in VOICE_PROFILE_INI_KEYS.items())
    snippet = ('\r\n[VVoice %d]\r\nBankSelect = %d\r\nProgramChange = %d\r\nComponentID = "%016d"\r\n'
               'DBPath = "%s"\r\nDBName = "%s.tree"\r\nVVoiceName = "%s"\r\n'
               '%sOpening = 0\r\n'
               % (n, bank, pc, n + 1, folder, tree, name, profile_lines))
    work = os.path.join(os.path.dirname(db_path), 'devslot_%d' % n)
    os.makedirs(work, exist_ok=True)
    slot_file = os.path.join(work, 'slot.txt')
    with open(slot_file, 'w', encoding='latin-1', newline='') as f:
        f.write(snippet)
    bat = os.path.join(work, 'add_dev_slot.bat')
    with open(bat, 'w', encoding='ascii', newline='') as f:
        f.write(DEV_SLOT_APPEND_BAT.format(ini=ini, slot_file=slot_file))
    log('dev slot staged: run as administrator: %s' % bat)
    return bat