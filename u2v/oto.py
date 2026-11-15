"""UTAU voicebank reading: oto.ini (any folder depth), prefix.map, pitch suffixes."""
import os
import re
import unicodedata
from dataclasses import dataclass

NOTE_RE = re.compile(r'(?:_?[A-G][#b]?-?\d)$')
NOTE_ANY = re.compile(r'(?<![A-Z#])([A-G][#b]?[1-8])(?![0-9])')


def read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'cp932', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1', 'replace'), 'latin-1'


@dataclass
class OtoEntry:
    wav: str            # absolute path
    alias: str          # alias with pitch suffix/prefix stripped
    raw_alias: str
    group: str          # pitch / voice-colour group (sub folder + suffix)
    dup: int            # trailing duplicate number ("a ka2" -> 2), 0 if none
    offset: float       # all times in seconds, absolute in the wav
    consonant: float
    cutoff_raw: float   # ms as written (negative = relative to offset)
    preutter: float
    overlap: float
    pitch: str = ''     # recording pitch from the folder/suffix ("C3", "A#3"), '' if unknown
    color: str = ''     # voice colour label: group with the pitch taken out ("Soft/SF", "Standard")
    full: str = ''      # alias with only the folder tag removed: "b3", "D3" are phonemes in X-SAMPA banks

    def end(self, wav_dur):
        """Absolute end time in seconds."""
        c = self.cutoff_raw / 1000.0
        if c < 0:
            return min(wav_dur, self.offset - c)
        return max(self.offset, wav_dur - c)


def _read_prefix_map_file(path):
    """Read one prefix.map as (prefix, suffix) pairs, ignoring comments and malformed rows."""
    pairs = set()
    txt, _ = read_text(path)
    for line in txt.splitlines():
        line = line.strip('\r\n')
        if not line or line.lstrip().startswith((';', '#')):
            continue
        parts = line.split('\t')
        if len(parts) >= 3:
            pre, suf = parts[1].strip(), parts[2].strip()
            if pre or suf:
                pairs.add((pre, suf))
    return pairs


def read_prefix_map(bank_root):
    """Return every (prefix, suffix) pair in prefix.map files under bank_root."""
    pairs = set()
    for dirpath, _, files in os.walk(bank_root):
        for fn in files:
            if fn.lower() == 'prefix.map':
                pairs.update(_read_prefix_map_file(os.path.join(dirpath, fn)))
    return pairs


def _case_insensitive_relative_path(base_dir, relative_path):
    """Resolve an OTO-relative WAV path component-by-component, case-insensitively.

    Windows normally hides filename case mismatches. This keeps copied voicebanks
    portable to case-sensitive filesystems and also supports nested relative paths.
    """
    rel = relative_path.replace('\\', os.sep).replace('/', os.sep)
    current = os.path.abspath(base_dir)
    for part in rel.split(os.sep):
        if not part or part == '.':
            continue
        if part == '..':
            current = os.path.dirname(current)
            continue
        try:
            names = os.listdir(current)
        except OSError:
            return None
        key = unicodedata.normalize('NFC', part).casefold()
        match = next((name for name in names
                      if unicodedata.normalize('NFC', name).casefold() == key), None)
        if match is None:
            return None
        current = os.path.join(current, match)
    return current if os.path.isfile(current) else None


def _prefix_maps_by_dir(bank_root):
    """Map each folder containing a prefix.map to its local affixes.

    prefix.map is an append/folder-scoped file. Applying every discovered map to every
    oto.ini can strip valid phoneme text from an unrelated append, so read_bank only
    inherits maps from the OTO directory and its parents.
    """
    result = {}
    for dirpath, _, files in os.walk(bank_root):
        for fn in files:
            if fn.lower() == 'prefix.map':
                result[os.path.normcase(os.path.abspath(dirpath))] = _read_prefix_map_file(
                    os.path.join(dirpath, fn))
    return result


def _inherited_affixes(dirpath, bank_root, maps):
    pairs = set()
    current = os.path.normcase(os.path.abspath(dirpath))
    root = os.path.normcase(os.path.abspath(bank_root))
    while True:
        pairs.update(maps.get(current, ()))
        if current == root:
            break
        parent = os.path.normcase(os.path.dirname(current))
        if parent == current or not (parent == root or parent.startswith(root + os.sep)):
            break
        current = parent
    return pairs


def strip_affixes(alias, affixes, strip_notes=True):
    """Remove a prefix.map prefix/suffix (longest first) or a trailing note name like C4 / _F#3."""
    tag = ''
    for pre, suf in sorted(affixes, key=lambda p: -len(p[0]) - len(p[1])):
        if (not pre or alias.startswith(pre)) and (not suf or alias.endswith(suf)) and \
                len(alias) > len(pre) + len(suf):
            alias = alias[len(pre):len(alias) - len(suf) if suf else None]
            return alias, (pre + suf)
    if strip_notes:
        m = NOTE_RE.search(alias)
        if m and m.start() > 0:
            tag = m.group(0).lstrip('_')
            alias = alias[:m.start()]
    return alias, tag


def common_suffix(aliases, share=0.9):
    """Trailing string (pitch/colour tag like "C3", "C3SF", "_強") shared by nearly all aliases of one oto.ini.

    A one- or two-entry OTO file does not provide enough evidence to infer a shared
    suffix: otherwise the longest common suffix can consume nearly the whole alias.
    Such small files must rely on prefix.map or folder metadata instead.
    """
    if len(aliases) < 3:
        return ''
    best = ''
    for k in range(1, 9):
        tails = [a[-k:] for a in aliases if len(a) > k]
        if not tails:
            break
        s = max(set(tails), key=tails.count)
        if tails.count(s) < share * len(aliases):
            break
        best = s
    # a lone kana/vowel shared by everything is content, not a tag
    # Lowercase alias tails (e.g. 'ka' in '- ka', 'a ka', 'i ka') are
    # much more likely to be phoneme content than a recording-group tag.
    if re.fullmatch(r'[぀-ヿー\s]*|[a-z]+', best.strip()):
        return ''
    return best.lstrip() if best.strip() else ''


def split_label(label):
    """"SoftC3/C3SF" -> ("C3", "Soft/SF"): the first note name is the pitch, the rest the colour."""
    m = NOTE_ANY.search(label)
    if not m:
        return '', label.strip(' _-/')
    pitch = m.group(1)
    color = NOTE_ANY.sub('', label)
    color = '/'.join(p.strip(' _-') for p in color.split('/') if p.strip(' _-'))
    return pitch, color


def apply_group_assignments(entries, assignments, log=None):
    """Apply manual append/pitch metadata to exact source OTO groups.

    Keys are the original group names (folder + stripped suffix), such as
    "Natural/_0". Values are {"append": "Natural", "pitch": "E3"}.
    Using the complete group key allows the same suffix to mean different
    things in different voice appends.
    """
    if assignments is None:
        return entries
    if not isinstance(assignments, dict):
        raise ValueError("group assignments must be a JSON object")

    groups = {entry.group for entry in entries}
    prepared = {}
    for group, spec in assignments.items():
        if not isinstance(spec, dict):
            raise ValueError("assignment for %r must be an object with append and pitch" % group)
        append = str(spec.get("append", spec.get("color", "main")) or "main").strip()
        pitch = str(spec.get("pitch", "") or "").strip().upper().lstrip("_")
        if not append:
            raise ValueError("append name cannot be empty for %r" % group)
        if pitch and not NOTE_RE.fullmatch(pitch):
            raise ValueError("invalid pitch %r for %r (use a note such as E3 or F#4)" % (pitch, group))
        prepared[str(group)] = (append, pitch)

    changed = 0
    missing = []
    for group, (append, pitch) in prepared.items():
        if group not in groups:
            missing.append(group)
            continue
        for entry in entries:
            if entry.group == group:
                entry.color = append
                entry.pitch = pitch
                changed += 1
    if missing and log:
        log("assignments for missing OTO groups ignored: %s" % ", ".join(sorted(missing)))
    if changed and log:
        log("manual pitch/append assignments applied to %d OTO entries" % changed)
    return entries


def read_bank(bank_root, extra_suffixes=(), diagnostics=None):
    """Collect every oto.ini entry under bank_root.

    `diagnostics`, when supplied, receives human-readable warnings for malformed
    rows and missing WAV references. Prefix maps are inherited only by OTO files
    beneath the map that defines them; unrelated appends cannot strip one another's
    alias suffixes.
    """
    diagnostics = diagnostics if diagnostics is not None else []
    bank_root = os.path.abspath(bank_root)
    maps = _prefix_maps_by_dir(bank_root)
    extra_affixes = {('', s) for s in extra_suffixes}
    entries = []
    for dirpath, _, files in os.walk(bank_root):
        for fn in files:
            if fn.lower() != 'oto.ini':
                continue
            oto_path = os.path.join(dirpath, fn)
            sub = os.path.relpath(dirpath, bank_root)
            sub = '' if sub == '.' else sub.replace('\\', '/')
            affixes = _inherited_affixes(dirpath, bank_root, maps) | extra_affixes
            # Only suffixes declared for this OTO's own append (plus explicit user
            # suffixes) can be treated as pitch metadata. A sibling prefix.map must
            # not turn an unrelated append tag into a pitch.
            pitch_tags = {pre + suf for pre, suf in affixes if pre + suf}
            txt, _ = read_text(oto_path)
            # OTO-relative paths are resolved component-by-component below, including
            # Unicode NFC and case-folding on case-sensitive filesystems.
            rows = []
            for line_no, line in enumerate(txt.splitlines(), 1):
                if not line.strip() or line.lstrip().startswith((';', '#')) or '=' not in line:
                    continue
                wav, rest = line.split('=', 1)
                # Only the final five commas are structural. Aliases may themselves
                # contain commas, and splitting from the left silently corrupted them.
                parts = rest.rsplit(',', 5)
                if len(parts) != 6:
                    diagnostics.append('%s:%d: expected alias plus five numeric OTO fields' % (oto_path, line_no))
                    continue
                raw_alias = parts[0].strip() or os.path.splitext(os.path.basename(wav.strip()))[0]
                try:
                    vals = tuple(float(x.strip() or 0) for x in parts[1:])
                except ValueError:
                    diagnostics.append('%s:%d: non-numeric OTO timing value' % (oto_path, line_no))
                    continue
                if not all(__import__('math').isfinite(v) for v in vals):
                    diagnostics.append('%s:%d: non-finite OTO timing value' % (oto_path, line_no))
                    continue
                rows.append((line_no, wav.strip(), unicodedata.normalize('NFC', raw_alias), vals))
            folder_tag = common_suffix([r[2] for r in rows])
            for line_no, wav, raw_alias, (off, con, cut, pre, ovl) in rows:
                if folder_tag and raw_alias.endswith(folder_tag) and len(raw_alias) > len(folder_tag):
                    alias, tag = raw_alias[:-len(folder_tag)], folder_tag
                    full = alias
                else:
                    alias, tag = strip_affixes(raw_alias, affixes)
                    full = raw_alias
                dup = 0
                m = re.search(r'(?<=[^\s\d])(\d+)$', alias)
                if m:
                    dup = int(m.group(1))
                    alias = alias[:m.start()]
                # UTAU permits relative paths and both slash styles in oto.ini.
                rel_wav = wav.replace('\\', os.sep).replace('/', os.sep)
                path = rel_wav if os.path.isabs(rel_wav) else os.path.join(dirpath, rel_wav)
                if not os.path.isfile(path) and not os.path.isabs(rel_wav):
                    path = _case_insensitive_relative_path(dirpath, rel_wav)
                if not path or not os.path.isfile(path):
                    diagnostics.append('%s:%d: WAV file not found: %s' % (oto_path, line_no, wav))
                    continue
                group = '/'.join(x for x in (sub, tag) if x) or 'main'
                # A prefix.map suffix is normally a pitch/recording tag, not a phoneme.
                label = sub if tag and tag in pitch_tags else group
                pitch, color = split_label(label if label != 'main' else '')
                if not pitch and tag in pitch_tags:
                    pitch = tag
                # OTO values are milliseconds relative to the source WAV. Keep the
                # absolute source-time markers as used by the segmentation pipeline.
                entries.append(OtoEntry(path, alias.strip(), raw_alias, group, dup,
                                        off / 1000.0, (off + con) / 1000.0, cut,
                                        (off + pre) / 1000.0, (off + ovl) / 1000.0,
                                        pitch, color, full.strip()))
    return entries
