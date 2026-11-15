"""WAV I/O, resampling and the frame features used to place phoneme boundaries (numpy only)."""
import struct
import wave

import numpy as np

HOP = 0.005      # feature hop in seconds


def read_wav(path):
    """Read a RIFF/WAVE file as mono float32 in [-1, 1].

    Supports little-endian PCM (8/16/24/32-bit), IEEE float (32/64-bit),
    WAVE_FORMAT_EXTENSIBLE PCM/float, odd-sized padded chunks and arbitrary
    chunk ordering.  Stereo downmixing has a phase-cancellation fallback: if
    averaging channels nearly erases the signal, the strongest channel wins.
    """
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a RIFF/WAVE file: %s' % path)

    riff_size = struct.unpack_from('<I', data, 4)[0]
    riff_end = 8 + riff_size
    if riff_end > len(data) or riff_end < 12:
        raise ValueError('truncated RIFF/WAVE file: %s' % path)

    fmt = None
    raw = None
    pos = 12
    while pos + 8 <= riff_end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from('<I', data, pos + 4)[0]
        body_start, body_end = pos + 8, pos + 8 + size
        if body_end > riff_end:
            raise ValueError('truncated %r chunk in %s' % (cid, path))
        body = data[body_start:body_end]
        if cid == b'fmt ':
            if len(body) < 16:
                raise ValueError('invalid fmt chunk in %s' % path)
            tag, channels, sr, byte_rate, align, bits = struct.unpack_from('<HHIIHH', body)
            valid_bits = bits
            if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE
                if len(body) < 40 or struct.unpack_from('<H', body, 16)[0] < 22:
                    raise ValueError('invalid WAVE_FORMAT_EXTENSIBLE fmt chunk in %s' % path)
                valid_bits = struct.unpack_from('<H', body, 18)[0] or bits
                if not 0 < valid_bits <= bits:
                    raise ValueError('invalid extensible WAV valid-bit count in %s' % path)
                subformat = struct.unpack_from('<I', body, 24)[0]
                guid_tail = body[28:40]
                if guid_tail != b'\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71':
                    raise ValueError('unsupported extensible WAV subformat in %s' % path)
                tag = subformat
            fmt = (tag, channels, sr, byte_rate, align, bits, valid_bits)
        elif cid == b'data' and raw is None:
            raw = body
        pos = body_end + (size & 1)  # RIFF chunks are word-aligned

    if fmt is None or raw is None:
        raise ValueError('missing fmt/data chunk: %s' % path)
    tag, channels, sr, _byte_rate, align, bits, valid_bits = fmt
    if channels < 1 or sr < 1 or align < 1:
        raise ValueError('invalid WAV channel count, sample rate or block alignment: %s' % path)
    bytes_per_sample = (bits + 7) // 8
    if bits not in (8, 16, 24, 32, 64) or align < channels * bytes_per_sample:
        raise ValueError('unsupported WAV layout (%d channels, %d bits, align %d): %s'
                         % (channels, bits, align, path))
    if len(raw) % align:
        raise ValueError('partial WAV frame in data chunk: %s' % path)

    n_frames = len(raw) // align
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32), sr
    # Remove per-frame padding, if any, before interpreting sample data.
    if align == channels * bytes_per_sample:
        sample_bytes = raw
    else:
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(n_frames, align)
        sample_bytes = frame[:, :channels * bytes_per_sample].copy().tobytes()

    if tag == 3 and bits == 32:
        samples = np.frombuffer(sample_bytes, '<f4').astype(np.float32)
    elif tag == 3 and bits == 64:
        samples = np.frombuffer(sample_bytes, '<f8').astype(np.float32)
    elif tag == 1 and bits == 8:
        samples = (np.frombuffer(sample_bytes, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        samples = np.frombuffer(sample_bytes, '<i2').astype(np.float32) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(sample_bytes, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
             | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= (1 << 23), v - (1 << 24), v)
        samples = v.astype(np.float32) / float(1 << 23)
    elif tag == 1 and bits == 32:
        samples = np.frombuffer(sample_bytes, '<i4').astype(np.float32) / 2147483648.0
    else:
        raise ValueError('unsupported WAV format (%d bit, tag %d): %s' % (bits, tag, path))

    samples = np.nan_to_num(samples, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    samples = np.clip(samples, -1.0, 1.0)
    if channels > 1:
        channels_x = samples.reshape(n_frames, channels)
        mono = channels_x.mean(axis=1)
        channel_rms = np.sqrt(np.mean(channels_x.astype(np.float64) ** 2, axis=0))
        strongest = int(np.argmax(channel_rms))
        mean_rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
        # Opposite-polarity stereo recordings can cancel almost completely.
        if channel_rms[strongest] > 1e-8 and mean_rms < 0.25 * channel_rms[strongest]:
            mono = channels_x[:, strongest]
        samples = mono
    return np.asarray(samples, dtype=np.float32), int(sr)


def resample(x, sr_from, sr_to):
    """Band-limited windowed-sinc resampling without an optional SciPy dependency.

    Unlike FFT resizing, this does not assume that the input is periodic, so
    the first/last samples do not wrap into one another. Downsampling applies
    an anti-alias low-pass before decimation.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError('resample expects a mono 1-D signal')
    if (not np.isfinite(sr_from) or not np.isfinite(sr_to)
            or int(sr_from) != sr_from or int(sr_to) != sr_to
            or sr_from <= 0 or sr_to <= 0):
        raise ValueError('sample rates must be finite positive integers')
    x = np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    sr_from, sr_to = int(sr_from), int(sr_to)
    if sr_from == sr_to or not len(x):
        return x
    n_out = int(round(len(x) * float(sr_to) / sr_from))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)

    ratio = float(sr_to) / sr_from
    cutoff = min(1.0, ratio) * 0.94
    # Keep roughly 24 zero-crossing lobes in the output-rate domain. The
    # support must widen as the cutoff falls, otherwise strong downsampling
    # leaves an unnecessarily short anti-alias filter.
    radius = max(24, int(np.ceil(24.0 / cutoff)))
    offsets = np.arange(-radius + 1, radius + 1, dtype=np.int64)
    out = np.empty(n_out, dtype=np.float32)
    # Bound the temporary output-by-taps matrices even for unusually large
    # source/downsampling ratios.
    chunk_size = max(64, min(8192, 1_000_000 // len(offsets)))
    for first in range(0, n_out, chunk_size):
        last = min(n_out, first + chunk_size)
        positions = np.arange(first, last, dtype=np.float64) / ratio
        centres = np.floor(positions).astype(np.int64)
        indices = centres[:, None] + offsets[None, :]
        distance = positions[:, None] - indices
        inside = np.abs(distance) < radius
        window = np.where(inside, 0.5 + 0.5 * np.cos(np.pi * distance / radius), 0.0)
        weights = cutoff * np.sinc(cutoff * distance) * window
        valid = (indices >= 0) & (indices < len(x))
        weights *= valid
        values = x[np.clip(indices, 0, len(x) - 1)]
        denom = weights.sum(axis=1)
        out[first:last] = (np.sum(values * weights, axis=1)
                           / np.where(np.abs(denom) > 1e-12, denom, 1.0)).astype(np.float32)
    return out


def cut(x, a, b, dither=1e-4):
    """Return x[a:b], padding out-of-range samples with deterministic low-level noise."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError('cut expects a mono 1-D signal')
    a, b = int(a), int(b)
    if b < a:
        raise ValueError('cut end must not precede start')
    if dither < 0 or not np.isfinite(dither):
        raise ValueError('dither must be a finite non-negative value')
    out = (np.random.default_rng(a & 0xffff).standard_normal(b - a) * dither).astype(np.float32)
    lo, hi = max(a, 0), min(b, len(x))
    if hi > lo:
        out[lo - a:hi - a] = x[lo:hi]
    return out


def write_wav(path, x, sr):
    """Write mono PCM16 WAV, sanitizing invalid float samples before quantization."""
    if not np.isfinite(sr) or int(sr) != sr or sr <= 0:
        raise ValueError('sample rate must be a finite positive integer')
    sr = int(sr)
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError('write_wav expects a mono 1-D signal')
    x = np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    y = np.clip(np.round(np.clip(x, -1.0, 1.0) * 32767), -32768, 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(y.tobytes())


def band_edges(nfft):
    """FFT bin edges of the 24 log-spaced bands of Features.spec."""
    return np.unique(np.geomspace(4, nfft // 2, 25).astype(int))


def band_centres_hz(sr, win=0.040):
    nfft = 1 << int(np.ceil(np.log2(2 * int(round(win * sr)))))
    e = band_edges(nfft)
    return np.sqrt(np.maximum(e[:-1], 1) * e[1:]) * sr / nfft


class Features:
    """Chunked frame analysis: energy, periodicity, spectral shape and edge cues.

    Long recordings are common in voicebanks. Processing a whole file as one
    ``frames × samples`` matrix can require hundreds of megabytes of temporary
    memory, so extraction is batched while the compact feature arrays are kept.
    The frame grid and feature definitions remain shared across the batches.
    """

    FRAME_BATCH = 512

    def __init__(self, x, sr):
        x = np.asarray(x, dtype=np.float32)
        if x.ndim != 1:
            raise ValueError('Features expects a mono 1-D signal')
        if not np.isfinite(sr) or int(sr) != sr or sr < 8000:
            raise ValueError('audio analysis requires an integer sample rate of at least 8000 Hz')
        if not np.all(np.isfinite(x)):
            x = np.nan_to_num(x, copy=True, nan=0.0, posinf=1.0, neginf=-1.0)
        self.sr = int(sr)
        sr = self.sr
        hop = max(1, int(round(HOP * sr)))
        win = max(32, int(round(0.040 * sr)))
        n = max(1, (len(x) - win) // hop + 1) if len(x) >= win else 1
        xp = np.pad(x, (0, max(0, win - len(x))))
        window = np.hanning(win).astype(np.float64)
        nfft = 1 << int(np.ceil(np.log2(2 * win)))
        lo, hi = max(1, int(sr / 900)), min(win - 1, int(sr / 70))
        if hi <= lo:
            raise ValueError('sample rate/window is too short for pitch analysis')
        wac = np.correlate(window, window, 'full')[win - 1:]
        wac_norm = np.maximum(wac[lo:hi] / max(wac[lo], 1e-12), 1e-6)
        edges = band_edges(nfft)
        mag_bins = nfft // 2
        hf_start = min(mag_bins, int(3000 * nfft / sr))
        lf_start = max(0, int(80 * nfft / sr))
        lf_end = min(mag_bins, max(lf_start + 1, int(1000 * nfft / sr)))

        self.energy = np.empty(n, dtype=np.float32)
        self.period = np.empty(n, dtype=np.float32)
        self.lag = np.empty(n, dtype=float)
        self.spec = np.empty((n, len(edges) - 1), dtype=np.float64)
        self.hf_ratio = np.empty(n, dtype=np.float32)
        self.spectral_centroid = np.empty(n, dtype=np.float32)
        self.spectral_flatness = np.empty(n, dtype=np.float32)

        sw = max(32, int(round(0.012 * sr)))
        sh = sw // 2
        short_window = np.hanning(sw).astype(np.float64)
        snfft = 1 << int(np.ceil(np.log2(sw * 2)))
        sf = np.fft.rfftfreq(snfft, 1 / sr)
        short_hf_mask = sf >= 3000
        short_lf_mask = (sf >= 80) & (sf <= 1000)
        if not short_hf_mask.any():
            short_hf_mask[-1] = True
        if not short_lf_mask.any():
            short_lf_mask[max(0, len(sf) // 8):max(1, len(sf) // 4)] = True
        self.short_energy = np.empty(n, dtype=np.float32)
        self.short_hf_ratio = np.empty(n, dtype=np.float32)
        self.short_hf_db = np.empty(n, dtype=np.float32)
        centres = int(round(0.020 * sr)) + hop * np.arange(n)
        right_pad = max(0, int(centres[-1] + sw - len(x))) if n else 0
        xs = np.pad(x, (sh, sh + right_pad))
        short_offsets = np.arange(-sh, sw - sh, dtype=np.int64) + sh

        for first in range(0, n, self.FRAME_BATCH):
            last = min(n, first + self.FRAME_BATCH)
            frame_idx = hop * np.arange(first, last)[:, None] + np.arange(win)[None, :]
            frame_samples = xp[frame_idx]
            # Remove each frame's DC component before measuring energy and
            # periodicity. Microphone/interface offsets otherwise bias pitch
            # autocorrelation and the lowest spectral bands.
            frames = (frame_samples - frame_samples.mean(axis=1, keepdims=True)) * window[None, :]
            F = np.fft.rfft(frames, nfft, axis=1)
            ac = np.fft.irfft(np.abs(F) ** 2, nfft, axis=1)[:, :win]
            ac0 = ac[:, :1] + 1e-12
            acn = ac[:, lo:hi] / ac0
            acn /= wac_norm[None, :]
            best = np.max(acn, axis=1)
            self.period[first:last] = np.clip(best, 0.0, 1.0)
            peaks = np.zeros_like(acn, dtype=bool)
            if acn.shape[1] > 2:
                peaks[:, 1:-1] = ((acn[:, 1:-1] >= acn[:, :-2])
                                  & (acn[:, 1:-1] >= acn[:, 2:]))
            near = peaks & (acn >= 0.90 * best[:, None])
            first_peak = np.where(near.any(axis=1), np.argmax(near, axis=1), np.argmax(acn, axis=1))
            self.lag[first:last] = (first_peak + lo).astype(float)

            self.energy[first:last] = (20.0 * np.log10(
                np.sqrt(np.mean(frames * frames, axis=1) + 1e-12) + 1e-9)).astype(np.float32)
            mag = np.abs(F[:, :mag_bins])
            bands = np.stack([mag[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
            self.spec[first:last] = np.log(bands + 1e-6)
            # Lightweight descriptors complement the coarse 24-band envelope.
            # They are deliberately descriptive rather than treated as
            # standalone phoneme/formant detectors.
            freq = np.fft.rfftfreq(nfft, 1.0 / sr)[:mag_bins]
            mag_sum = np.maximum(mag.sum(axis=1), 1e-12)
            self.spectral_centroid[first:last] = (mag @ freq / mag_sum).astype(np.float32)
            gm = np.exp(np.mean(np.log(mag + 1e-9), axis=1))
            am = np.mean(mag, axis=1) + 1e-9
            self.spectral_flatness[first:last] = np.clip(gm / am, 0.0, 1.0).astype(np.float32)
            hf = mag[:, hf_start:].mean(axis=1)
            lf = mag[:, lf_start:lf_end].mean(axis=1)
            self.hf_ratio[first:last] = np.log((hf + 1e-9) / (lf + 1e-9)).astype(np.float32)

            short_idx = centres[first:last, None] + short_offsets[None, :]
            short_samples = xs[short_idx]
            short = (short_samples - short_samples.mean(axis=1, keepdims=True)) * short_window[None, :]
            short_mag = np.abs(np.fft.rfft(short, snfft, axis=1))
            shf = short_mag[:, short_hf_mask].mean(axis=1)
            slf = short_mag[:, short_lf_mask].mean(axis=1)
            self.short_energy[first:last] = (20.0 * np.log10(
                np.sqrt(np.mean(short * short, axis=1) + 1e-12) + 1e-9)).astype(np.float32)
            self.short_hf_ratio[first:last] = np.log((shf + 1e-9) / (slf + 1e-9)).astype(np.float32)
            self.short_hf_db[first:last] = (20.0 * np.log10(shf + 1e-9)).astype(np.float32)

        self.spectral_flux = np.zeros(n, dtype=np.float32)
        if n > 1:
            delta = np.maximum(self.spec[1:] - self.spec[:-1], 0.0)
            self.spectral_flux[1:] = (np.linalg.norm(delta, axis=1)
                                      / np.sqrt(np.mean(self.spec[1:] ** 2, axis=1) + 1e-6)).astype(np.float32)

        peak = float(np.percentile(self.energy, 99))
        floor = float(np.percentile(self.energy, 5))
        self.peak = peak
        self.noise_floor = floor
        if peak < -140.0:
            # A truly empty/digital-silence file has numerical FFT energy near
            # -180 dBFS. Do not apply this special case to merely quiet takes:
            # a quiet human recording is still valid audio, not silence.
            self.sil_thr = max(-140.0, peak + 6.0)
        else:
            self.sil_thr = min(max(floor + 8.0, peak - 45.0), peak - 35.0)
        k = 30
        ep = np.pad(self.energy, k, mode='edge')
        local = np.lib.stride_tricks.sliding_window_view(ep, 2 * k + 1).max(axis=1)
        energy_gate = np.maximum(peak - 40.0, local - 20.0)
        self.periodicity_confidence = np.clip((self.period - 0.22) / 0.68, 0.0, 1.0).astype(np.float32)
        self.energy_confidence = (1.0 / (1.0 + np.exp(-np.clip((self.energy - energy_gate) / 4.0, -30, 30)))).astype(np.float32)
        # The upper-band balance is a soft cue, not a hard rejection: voiced
        # fricatives, breathy voices and nasal timbres can legitimately carry
        # substantial high-frequency energy.
        self.harmonic_balance_confidence = np.clip((0.55 - self.hf_ratio) / 1.1, 0.0, 1.0).astype(np.float32)
        self.hnr_proxy_db = (10.0 * np.log10(
            np.maximum(self.period, 1e-5) / np.maximum(1.0 - self.period, 1e-5))).astype(np.float32)
        self.voicing_confidence = np.clip(
            0.64 * self.periodicity_confidence
            + 0.22 * self.energy_confidence
            + 0.14 * self.harmonic_balance_confidence, 0.0, 1.0).astype(np.float32)
        self.voiced = ((self.voicing_confidence >= 0.57)
                       & (self.energy > self.sil_thr)
                       & (self.period > 0.32))
        # DCT-II of the log-band spectrum: a compact timbral descriptor useful
        # for comparing voices without pretending it is a neural embedding.
        bands_n = self.spec.shape[1]
        coeff_n = min(13, bands_n)
        n_idx = np.arange(bands_n, dtype=np.float64)
        k_idx = np.arange(coeff_n, dtype=np.float64)[:, None]
        basis = np.cos(np.pi * (n_idx + 0.5) * k_idx / bands_n)
        basis[0] *= np.sqrt(1.0 / bands_n)
        if coeff_n > 1:
            basis[1:] *= np.sqrt(2.0 / bands_n)
        self.mfcc = (self.spec @ basis.T).astype(np.float32)
        self.n = n

    def t2f(self, t):
        return int(np.clip(round((t - 0.020) / HOP), 0, self.n - 1))

    def f2t(self, f):
        return f * HOP + 0.020

    def silent(self, f):
        return self.energy[f] < self.sil_thr

    def smooth_energy(self, f0, f1, k=3):
        e = self.energy[f0:f1 + 1]
        if len(e) < 2 * k + 1:
            return e
        return np.convolve(np.pad(e, k, mode='edge'), np.ones(2 * k + 1) / (2 * k + 1), 'valid')


def _nearest(cands, h):
    return min(cands, key=lambda c: abs(c - h)) if cands else None


def _fricative_edge(ft, f0, f1, fh, direction):
    """Find a voiced-fricative edge from short-window high-frequency energy.

    direction=+1 finds vowel -> fricative (high-frequency rise); -1 finds
    fricative -> vowel (high-frequency fall). A future energy valley suppresses
    the early edge of a stop closure, which otherwise looks like broadband noise.
    """
    hf, en = ft.short_hf_db, ft.short_energy
    best, best_score = None, 0.0
    lo, hi = max(f0 + 4, 4), min(f1 - 4, ft.n - 5)
    for f in range(lo, hi + 1):
        before_h = float(np.median(hf[f - 4:f]))
        after_h = float(np.median(hf[f + 1:f + 5]))
        change = direction * (after_h - before_h)
        if change < 3.0:                        # a real high-band onset/offset, not a ratio swing in silence
            continue
        before_e = float(np.median(en[f - 4:f]))
        after_e = float(np.median(en[f + 1:f + 5]))
        if direction > 0:
            # Do not mistake noise during an affricate's closure for the frication onset.
            future = en[f + 1:min(ft.n, f + 9)]
            future_low = float(np.min(future)) if len(future) else after_e
            if before_e - future_low > 8.0:
                continue
            # For a closure followed by a release, the real frication edge also has a
            # short-window energy recovery. With no closure, the spectral rise is enough.
            recovery = max(0.0, after_e - before_e)
        else:
            recovery = max(0.0, before_e - after_e)
        score = (change + 0.025 * recovery) * np.exp(-0.5 * ((f - fh) / 10.0) ** 2)
        if score > best_score:
            best, best_score = f, score
    if best is None:
        return None
    t = ft.f2t(best) - HOP / 2
    if direction > 0:
        # A release burst precedes the stable frication in an affricated sample.
        # When a real closure is visible, place the boundary after that transient;
        # without a closure, retain the sharper unshifted onset.
        prior = en[max(0, best - 22):max(1, best - 10)]
        recent = en[max(0, best - 10):best]
        if len(prior) >= 4 and len(recent) >= 2 and float(np.median(prior) - np.min(recent)) > 8.0:
            t += 0.010
    return min(t, ft.f2t(f1))


def deaffricate_medial_vcv(x, sr, ft, fricative_onset, clip_start, y):
    """Repair an affricated [V dz V] realization in a medial VOCALOID V-dz-V unit.

    The phoneme remains labeled dz; acoustic context says its medial realization is [z].
    Only called for vowel-dz-vowel clips. If short-window analysis finds a real low-energy
    closure followed by a sibilant release, extend the preceding voiced vowel through
    that closure using a pitch-cycle loop, then crossfade into the recorded frication.
    Natural, already-continuous [V z V] recordings are left untouched.
    Returns (audio, repaired).
    """
    f = ft.t2f(fricative_onset)
    if f < 24 or f >= ft.n - 6:
        return y, False
    baseline = float(np.median(ft.short_energy[max(0, f - 26):max(0, f - 12)]))
    if not np.isfinite(baseline):
        return y, False
    threshold = baseline - 8.0
    # Find the final run of low-energy frames immediately before the detected sibilant.
    a, b = max(0, f - 20), f
    low = ft.short_energy[a:b] < threshold
    runs = []
    i = 0
    while i < len(low):
        if not low[i]:
            i += 1
            continue
        j = i + 1
        while j < len(low) and low[j]:
            j += 1
        if j - i >= 3 and b - (a + j) <= 2:
            runs.append((a + i, a + j))
        i = j
    if not runs:
        return y, False
    c0, c1 = runs[-1]
    gap_db = baseline - float(np.median(ft.short_energy[c0:c1]))
    post_hf = float(np.median(ft.short_hf_db[f + 1:f + 5]))
    closure_hf = float(np.median(ft.short_hf_db[c0:c1]))
    post_energy = float(np.median(ft.short_energy[f + 1:f + 5]))
    if gap_db < 9.0 or post_energy - float(np.median(ft.short_energy[c0:c1])) < 6.0:
        return y, False
    if post_hf - closure_hf < 6.0:
        return y, False

    # The run's window centres straddle the real closure onset. Start just before
    # the first low frame; keep the original time length by extending the vowel.
    gap_start = max(0.0, ft.f2t(c0) - 0.006)
    gap_end = max(gap_start, fricative_onset)
    start = int(round((gap_start - clip_start) * sr))
    end = int(round((gap_end - clip_start) * sr))
    if start < 0 or end > len(y) or end - start < int(0.012 * sr):
        return y, False

    # Estimate the preceding vowel's period from voiced frames, then repeat one
    # cycle. The seam is phase-coherent because the cycle is taken directly from
    # the same recording immediately before the closure.
    voiced = np.flatnonzero(ft.voiced[max(0, c0 - 12):max(1, c0 - 2)])
    if not len(voiced):
        return y, False
    vf = max(0, c0 - 12) + int(voiced[-1])
    period = int(round(ft.lag[vf]))
    if period < 12 or period > int(0.020 * sr):
        return y, False
    gap_sample = int(round(gap_start * sr))
    if gap_sample - period < 0:
        return y, False
    cycle = x[gap_sample - period:gap_sample].copy()
    if len(cycle) != period or np.max(np.abs(cycle)) < 1e-5:
        return y, False

    fade = min(int(0.012 * sr), len(y) - end)
    fill_len = end - start + fade
    fill = np.resize(cycle, fill_len).astype(np.float32)
    # A slight taper keeps the extended vowel from sounding louder than the
    # following voiced fricative while preserving its continuous voicing.
    if end > start:
        fill[:end - start] *= np.linspace(1.0, 0.88, end - start, dtype=np.float32)
    out = y.copy()
    out[start:end] = fill[:end - start]
    if fade > 1:
        w = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[end:end + fade] = fill[end - start:] * (1.0 - w) + out[end:end + fade] * w
    return out, True


def find_boundary(ft, x_cls, y_cls, hint, lo, hi, y_ph=None, x_ph=None):
    """Place the transition between phoneme classes x_cls -> y_cls near `hint` inside [lo, hi] (seconds).

    Returns (time, detected?)."""
    f0, f1, fh = ft.t2f(lo), ft.t2f(hi), ft.t2f(hint)
    if f1 - f0 < 2:
        return hint, False
    rng = range(f0 + 1, f1 + 1)
    v = ft.voiced
    s = ft.energy < ft.sil_thr
    unv = ('uplos', 'ufric')
    cand = None
    if y_cls == 'sil':
        cand = _nearest([f for f in rng if not s[f - 1] and s[f] and s[f:f + 4].all()], fh)
    elif x_cls == 'sil':
        ons = [f for f in rng if s[f - 1] and not s[f] and not s[f:f + 3].any()]
        cand = _nearest(ons, fh)
        if cand is not None and y_cls == 'uplos':
            cand = max(f0, cand - 4)          # closure sits in silence: start 20 ms before the burst
    elif x_cls in ('vowel', 'vcons', 'vplos') and y_cls in unv:
        cand = _nearest([f for f in rng if v[f - 1] and not v[f] and not v[f:f + 3].any()], fh)
        # voicing can bleed on into a closure: the vowel really ends where its level falls away
        # (closures only: a breathy h/f/hy is meant to carry some of the vowel's energy)
        closure = y_cls == 'uplos' or y_ph in ('ts', 'tS')
        c0 = cand if cand is not None else fh
        pl = ft.energy[max(f0, c0 - 25):max(f0 + 1, c0 - 8)]
        if closure and len(pl) >= 4:
            plateau = float(np.median(pl))
            drop = [f for f in range(max(f0 + 1, c0 - 25), min(f1 + 1, c0 + 1)) if ft.energy[f] < plateau - 6]
            if drop and (cand is None or drop[0] < cand):
                cand = drop[0]
    elif x_cls in unv and y_cls in ('vowel', 'vcons', 'vplos'):
        cand = _nearest([f for f in rng if not v[f - 1] and v[f] and v[f:f + 3].all()], fh)
    elif x_cls == 'vowel' and y_cls == 'vcons' and y_ph in ('z', 'Z', 'v', 'D', 'h\\'):
        edge = _fricative_edge(ft, f0, f1, fh, 1)
        if edge is not None:
            return edge, True
        # A level slope is not a reliable substitute for the spectral onset of a
        # voiced fricative. Keep the OTO anchor rather than cutting into its closure.
        return hint, False
    elif x_cls == 'vcons' and y_cls == 'vowel' and x_ph in ('z', 'Z', 'v', 'D', 'h\\'):
        edge = _fricative_edge(ft, f0, f1, fh, -1)
        if edge is not None:
            return edge, True
        return hint, False
    elif x_cls == 'vowel' and y_cls == 'vowel':
        sp = ft.spec[f0:f1 + 1]
        if len(sp) > 8:
            dist = np.linalg.norm(sp[4:] - sp[:-4], axis=1)
            w = np.exp(-0.5 * ((np.arange(len(dist)) + 2 + f0 - fh) / 12.0) ** 2)
            cand = f0 + 2 + int(np.argmax(dist * w))
    if cand is None and x_cls != y_cls and 'sil' not in (x_cls, y_cls):
        # energy slope: falling into a consonant, rising into a vowel
        e = ft.smooth_energy(f0, f1)
        d = np.diff(e)
        if len(d):
            w = np.exp(-0.5 * ((np.arange(len(d)) + 1 + f0 - fh) / 8.0) ** 2)
            falling = x_cls == 'vowel' or (x_cls != 'vowel' and y_cls != 'vowel' and x_cls == 'vcons')
            score = (-d if falling else d) * w
            if score.max() > 0.5:
                cand = f0 + int(np.argmax(score)) + 1
    if cand is None:
        return hint, False
    return ft.f2t(cand) - HOP / 2, True



def boundary_confidence(ft, t, lo=None, hi=None, detected=True):
    """Estimate confidence in a proposed transition from independent acoustic cues.

    This does not move the boundary. It measures local agreement between
    spectral change, transient energy, voicing and high-frequency balance so
    downstream ranking can prefer well-supported cuts while preserving the
    OTO fallback when evidence is weak.
    """
    if not detected or ft.n < 3 or not np.isfinite(t):
        return 0.12
    f = ft.t2f(t)
    left = slice(max(0, f - 4), max(1, f - 1))
    right = slice(min(ft.n - 1, f + 1), min(ft.n, f + 4))
    if (left.stop - left.start) < 1 or (right.stop - right.start) < 1:
        return 0.25
    spec_delta = float(np.linalg.norm(ft.spec[right].mean(axis=0) - ft.spec[left].mean(axis=0)))
    flux = float(np.max(ft.spectral_flux[max(0, f - 2):min(ft.n, f + 3)], initial=0.0))
    hf_delta = abs(float(np.mean(ft.short_hf_ratio[right]) - np.mean(ft.short_hf_ratio[left])))
    energy_delta = abs(float(np.mean(ft.short_energy[right]) - np.mean(ft.short_energy[left])))
    voice_delta = abs(float(np.mean(ft.voicing_confidence[right]) - np.mean(ft.voicing_confidence[left])))
    cues = (np.clip(spec_delta / 2.5, 0.0, 1.0), np.clip(flux / 0.45, 0.0, 1.0),
            np.clip(hf_delta / 0.8, 0.0, 1.0), np.clip(energy_delta / 8.0, 0.0, 1.0),
            np.clip(voice_delta / 0.65, 0.0, 1.0))
    evidence = 0.30 * cues[0] + 0.18 * cues[1] + 0.18 * cues[2] + 0.16 * cues[3] + 0.18 * cues[4]
    confidence = 0.20 + 0.80 * float(evidence)
    if lo is not None and hi is not None and hi > lo:
        # A candidate pressed against the search-window edge is less convincing.
        edge_distance = min(abs(t - lo), abs(hi - t)) / max((hi - lo) * 0.15, 1e-4)
        confidence *= 0.75 + 0.25 * float(np.clip(edge_distance, 0.0, 1.0))
    return float(np.clip(confidence, 0.05, 1.0))


def stable_region(ft, t0, t1):
    """Most pitch-stable sub-span (>=40% of the segment) of a sustained segment, as (begin, end)."""
    f0, f1 = ft.t2f(t0), ft.t2f(t1)
    if f1 - f0 < 10:
        return t0 + 0.25 * (t1 - t0), t1 - 0.15 * (t1 - t0)
    lag = ft.lag[f0:f1]
    voiced = ft.voiced[f0:f1]
    w = max(8, int(0.4 * (f1 - f0)))
    if len(lag) < w or int(voiced.sum()) < 8:
        return t0 + 0.25 * (t1 - t0), t1 - 0.15 * (t1 - t0)
    best, bi = None, 0
    for i in range(0, len(lag) - w + 1, 2):
        mask = voiced[i:i + w]
        if int(mask.sum()) < max(6, int(0.65 * w)):
            continue
        seg = lag[i:i + w][mask]
        med = float(np.median(seg))
        score = np.std(seg / max(med, 1e-9)) - 0.002 * float(np.mean(ft.energy[f0 + i:f0 + i + w]))
        if best is None or score < best:
            best, bi = score, i
    if best is None:
        return t0 + 0.25 * (t1 - t0), t1 - 0.15 * (t1 - t0)
    return ft.f2t(f0 + bi), ft.f2t(f0 + bi + w)


def sustained_extent(ft, t0, max_len=2.5, max_dist=2.2, max_drop=4.0, backward=False):
    """How far a sustained sound starting at t0 really lasts in the recording (it usually runs past the
    oto region). Stops where the spectrum moves away from its start (next syllable, other vowel),
    the level drops or voicing ends. backward=True walks towards earlier times from t0 instead.
    Returns (begin, end) in seconds."""
    f0 = ft.t2f(t0)
    step = -1 if backward else 1
    if not backward:
        # a CV sample's vowel is still swelling right after the consonant: start at full level
        top = float(ft.energy[f0:min(ft.n, f0 + 20)].max(initial=-200))
        while f0 < min(ft.n - 1, ft.t2f(t0) + 20) and ft.energy[f0] < top - 3:
            f0 += 1
    lo, hi = (max(0, f0 - 11), f0 + 1) if backward else (f0, min(ft.n, f0 + 12))
    if hi - lo < 4:
        return t0, t0
    spec = ft.spec[:, 4:]                  # the lowest bands sit below the voice: mostly room noise
    ref = spec[lo:hi].mean(axis=0)
    eref = float(np.median(ft.energy[lo:hi]))
    # how much this recording wobbles frame to frame on its own: the limit adapts to it
    lim = max(max_dist, 2.5 * float(np.median([np.linalg.norm(spec[k] - ref) for k in range(lo, hi)])))
    last, bad, f = f0, 0, f0
    while 0 <= f < ft.n and abs(f - f0) < int(max_len / HOP):
        # compared with the last ~60 ms (slow natural drift is fine, a new syllable is not) and,
        # loosely, with the start (so it can never glide into another vowel)
        recent = spec[f - 12 * step:f - 2 * step:step] if abs(f - f0) > 12 else spec[lo:hi]
        near = np.linalg.norm(spec[f] - recent.mean(axis=0)) if len(recent) else 0.0
        steady = near < lim and np.linalg.norm(spec[f] - ref) < 2 * lim
        if ft.voiced[f] and ft.energy[f] > eref - max_drop and steady:
            last, bad = f, 0
            eref = max(eref, float(ft.energy[f]) - 2) if abs(f - f0) < 20 else eref
        else:
            bad += 1
            if bad >= 3:
                break
        f += step
    if backward:
        return ft.f2t(last) + 0.02, ft.f2t(f0)
    return ft.f2t(f0), ft.f2t(last) - 0.02


def burst_time(x, sr, b, e, min_rise=10.0):
    """Find a plosive/affricate release burst in [b, e], or return None.

    Uses short-time high-pass energy, a local pre-burst reference and a
    peak constraint. This is less likely than a raw frame-to-frame difference
    to label a random loud sample as the release.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1 or sr <= 0 or not np.isfinite(b + e) or e <= b:
        return None
    start = max(0, int(round(b * sr)))
    stop = min(len(x), int(round(e * sr)))
    if stop - start < int(0.012 * sr):
        return None
    seg = np.nan_to_num(x[start:stop], nan=0.0, posinf=0.0, neginf=0.0).astype(np.float64)
    # A first difference suppresses the vowel's low-frequency periodic energy.
    hp = np.diff(seg, prepend=seg[0])
    win = max(8, int(round(0.006 * sr)))
    hop_n = max(1, int(round(0.002 * sr)))
    if len(hp) < win + 3 * hop_n:
        return None
    sq = hp * hp
    cs = np.concatenate(([0.0], np.cumsum(sq)))
    starts = np.arange(0, len(hp) - win + 1, hop_n, dtype=np.int64)
    level = 10.0 * np.log10((cs[starts + win] - cs[starts]) / win + 1e-14)
    if len(level) < 4:
        return None
    # Rise across about 4 ms, with a local maximum at/after the rise.
    rise = level[2:] - level[:-2]
    baseline = np.array([np.median(level[max(0, i - 12):max(1, i - 2)])
                         if i >= 3 else np.median(level[:max(1, i)])
                         for i in range(len(level))])
    peak_margin = level - baseline
    candidates = np.flatnonzero((rise >= min_rise) & (peak_margin[2:] >= 6.0)) + 2
    if not len(candidates):
        return None
    # Prefer a strong, sharp peak but avoid selecting the first edge of a long noisy plateau.
    scores = rise[candidates - 2] + 0.25 * np.maximum(peak_margin[candidates], 0.0)
    k = int(candidates[int(np.argmax(scores))])
    return float(b + (starts[k] + win / 2.0) / sr)


def estimate_formants(x, sr, t0=0.0, t1=None, frame_sec=0.030, hop_sec=0.010,
                      lpc_order=18, max_formants=4):
    """Estimate median vowel formants (Hz) with windowed LPC over a span.

    The returned values are approximate acoustic descriptors, not phoneme
    labels. LPC formants are unreliable on fricatives, whisper, very short
    spans, strongly nasalized sounds, and recordings with substantial noise;
    invalid tracks are returned as NaN rather than guessed.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1 or sr <= 0 or frame_sec <= 0 or hop_sec <= 0 or lpc_order < 4:
        return np.full(max(1, int(max_formants)), np.nan, dtype=float)
    if t1 is None:
        t1 = len(x) / float(sr)
    a = max(0, int(round(t0 * sr)))
    b = min(len(x), int(round(t1 * sr)))
    y = np.nan_to_num(x[a:b], copy=False)
    frame_n = max(lpc_order * 4, int(round(frame_sec * sr)))
    hop_n = max(1, int(round(hop_sec * sr)))
    if len(y) < frame_n:
        return np.full(max(1, int(max_formants)), np.nan, dtype=float)
    order = min(int(lpc_order), frame_n // 3)
    window = np.hamming(frame_n)
    tracks = []
    for pos in range(0, len(y) - frame_n + 1, hop_n):
        frame = y[pos:pos + frame_n]
        frame = np.append(frame[0], frame[1:] - 0.97 * frame[:-1])
        frame = (frame - np.mean(frame)) * window
        if np.sqrt(np.mean(frame * frame)) < 1e-5:
            continue
        ac = np.correlate(frame, frame, mode='full')[frame_n - 1:frame_n + order]
        toeplitz = ac[np.abs(np.subtract.outer(np.arange(order), np.arange(order)))]
        try:
            coeff = -np.linalg.solve(toeplitz + np.eye(order) * (ac[0] * 1e-8 + 1e-12), ac[1:order + 1])
            roots = np.roots(np.r_[1.0, coeff])
        except np.linalg.LinAlgError:
            continue
        candidates = []
        for root in roots:
            if np.imag(root) <= 0 or abs(root) <= 0 or abs(root) >= 1:
                continue
            frequency = float(np.angle(root) * sr / (2.0 * np.pi))
            bandwidth = float(-sr / np.pi * np.log(abs(root)))
            if 90.0 < frequency < min(5500.0, sr * 0.48) and 0 < bandwidth < 500.0:
                candidates.append(frequency)
        candidates.sort()
        if len(candidates) >= max_formants:
            tracks.append(candidates[:max_formants])
    if not tracks:
        return np.full(max(1, int(max_formants)), np.nan, dtype=float)
    arr = np.full((len(tracks), max_formants), np.nan, dtype=float)
    for i, row in enumerate(tracks):
        arr[i, :min(max_formants, len(row))] = row[:max_formants]
    with np.errstate(all='ignore'):
        return np.nanmedian(arr, axis=0)


def f0_track_detailed(x, sr, t0, t1, hop=0.005, win=0.03, min_hz=55.0, max_hz=1000.0):
    """Track F0 with per-frame autocorrelation candidates and confidence.

    Every frame evaluates the full supported pitch range. Strong period
    candidates are compared after correcting for the analysis-window overlap;
    the shortest strong candidate avoids period-multiple/subharmonic traps.
    A tiny continuity preference resolves nearby peaks but cannot prevent a
    genuine register change. Returns ``(times, f0_hz, confidence)``.
    """
    x = np.asarray(x, dtype=np.float32)
    if (x.ndim != 1 or sr <= 0 or hop <= 0 or win <= 0 or t1 <= t0
            or min_hz <= 0 or max_hz <= min_hz):
        return np.array([]), np.array([]), np.array([])
    a0 = max(0, int(round(t0 * sr)))
    b0 = min(len(x), int(round(t1 * sr)))
    seg = x[a0:b0].astype(np.float64)
    # At least 45 ms gives low voice pitches (around 55-70 Hz) enough cycles
    # for stable candidate selection even when callers request a 30 ms window.
    win_n = max(32, int(round(max(win, 0.045) * sr)))
    if len(seg) < max(int(0.05 * sr), win_n):
        return np.array([]), np.array([]), np.array([])
    lag_min = max(2, int(np.floor(sr / max_hz)))
    lag_max = min(win_n - 3, int(np.ceil(sr / min_hz)))
    if lag_max <= lag_min + 2:
        return np.array([]), np.array([]), np.array([])
    window = np.hanning(win_n)
    nfft = 1 << int(np.ceil(np.log2(2 * win_n)))
    window_ac = np.correlate(window, window, 'full')[win_n - 1:]
    window_norm = np.maximum(window_ac[lag_min:lag_max + 1] / max(window_ac[0], 1e-12), 1e-6)
    times, pitches, confidences = [], [], []
    previous_lag = None
    for t in np.arange(t0, t1, hop):
        start = int(round(t * sr))
        chunk = x[start:start + win_n].astype(np.float64)
        if len(chunk) < win_n:
            break
        raw_rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-18))
        times.append(t)
        if raw_rms < 1e-5:
            pitches.append(np.nan)
            confidences.append(0.0)
            previous_lag = None
            continue
        frame = (chunk - np.mean(chunk)) * window
        spectrum = np.fft.rfft(frame, nfft)
        ac = np.fft.irfft(np.abs(spectrum) ** 2, nfft)[:win_n]
        normalized = ac[lag_min:lag_max + 1] / (ac[0] + 1e-12)
        normalized /= window_norm
        normalized = np.clip(normalized, -1.0, 1.0)
        peaks = np.zeros(len(normalized), dtype=bool)
        if len(normalized) > 2:
            peaks[1:-1] = ((normalized[1:-1] >= normalized[:-2])
                           & (normalized[1:-1] >= normalized[2:]))
        candidates = np.flatnonzero(peaks & (normalized >= 0.25))
        if not len(candidates):
            k = int(np.argmax(normalized))
            if normalized[k] >= 0.30:
                candidates = np.array([k], dtype=int)
        if not len(candidates):
            pitches.append(np.nan)
            confidences.append(0.0)
            previous_lag = None
            continue
        strengths = normalized[candidates]
        best_peak = float(np.max(strengths))
        near_best = candidates[strengths >= 0.95 * best_peak]
        # Select the first strong period (not a later integer multiple). For
        # close local candidates, continuity helps suppress frame-to-frame
        # jitter while the short-period rule keeps register changes possible.
        shortest = int(np.min(near_best))
        near_lags = near_best.astype(np.float64) + lag_min
        close_short = np.flatnonzero(np.abs(12.0 * np.log2(near_lags / (shortest + lag_min))) <= 2.5)
        if previous_lag is not None and len(close_short):
            local_corr = normalized[near_best[close_short]]
            strongest_local = float(np.max(local_corr))
            tied = close_short[local_corr >= 0.995 * strongest_local]
            distances = np.abs(12.0 * np.log2(near_lags[tied] / previous_lag))
            k = int(near_best[tied[int(np.argmin(distances))]])
        else:
            k = shortest
        peak = float(normalized[k])
        lag = float(lag_min + k)
        if 0 < k < len(normalized) - 1:
            left, centre, right = normalized[k - 1:k + 2]
            denom = left - 2.0 * centre + right
            if abs(denom) > 1e-12:
                lag += float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))
        if peak < 0.30 or not np.isfinite(lag) or lag <= 0:
            pitches.append(np.nan)
            confidences.append(0.0)
            previous_lag = None
            continue
        alternatives = normalized[near_best]
        margin = max(0.0, peak - float(np.max(alternatives[near_best != k]))) if np.any(near_best != k) else 0.25
        strength = float(np.clip((peak - 0.22) / 0.70, 0.0, 1.0))
        uniqueness = 0.65 + 0.35 * float(np.clip(margin / 0.25, 0.0, 1.0))
        pitches.append(sr / lag)
        confidences.append(strength * uniqueness)
        previous_lag = lag
    return (np.asarray(times, dtype=float), np.asarray(pitches, dtype=float),
            np.asarray(confidences, dtype=np.float32))


def f0_track(x, sr, t0, t1, hop=0.005, win=0.03):
    """Compatibility wrapper returning ``(times, f0_hz)`` from the detailed tracker."""
    times, pitches, _confidence = f0_track_detailed(x, sr, t0, t1, hop=hop, win=win)
    return times, pitches


def clean_span(x, sr, t0, t1, max_dev=30.0, max_step=15.0, max_dip=2.5):
    """Longest stretch of [t0, t1] without a pitch/level seam in a held vowel.

    Pitch frames with weak periodicity are ignored for the pitch statistics but
    excluded from the candidate clean run; if too little pitch is measurable,
    the original span is retained rather than trimmed based on unreliable data.
    """
    ts, f = f0_track(x, sr, t0, t1)
    if len(f) < 25:
        return t0, t1
    valid = np.isfinite(f) & (f > 0)
    if int(valid.sum()) < max(25, int(0.60 * len(f))):
        return t0, t1
    f = np.interp(ts, ts[valid], f[valid])
    c = 1200.0 * np.log2(f / np.median(f))
    dev = np.abs(c - np.convolve(c, np.ones(21) / 21, 'same'))
    dev[:10] = dev[-10:] = 0
    step = np.abs(np.diff(c, prepend=c[0]))
    env = np.array([rms_db(x[int(t * sr):int(t * sr) + int(0.02 * sr)]) for t in ts])
    dip = np.convolve(env, np.ones(21) / 21, 'same') - env
    dip[:10] = dip[-10:] = 0
    bad = (dev > max_dev) | (step > max_step) | (~valid) | (dip > max_dip)
    best, start = (t0, t0), None
    for k in range(len(ts) + 1):
        if k < len(ts) and not bad[k]:
            start = k if start is None else start
        elif start is not None:
            a, b = ts[start] + 0.03, ts[k - 1] - 0.03
            if b - a > best[1] - best[0]:
                best = (a, b)
            start = None
    return best

def flat_span(x, sr, t0, t1, tol=3.0, hop=0.01, win=0.02):
    """Longest stretch of [t0, t1] whose level stays within tol dB of the stretch's median: a loop cut
    from a swelling or fading vowel jumps in level at every repeat."""
    ts = np.arange(t0, t1 - win, hop)
    if len(ts) < 5:
        return t0, t1
    lv = np.array([rms_db(x[int(t * sr):int(t * sr) + int(win * sr)]) for t in ts])
    med = np.median(lv)
    best, start = (t0, t0), None
    ok = np.abs(lv - med) <= tol
    for k in range(len(ts) + 1):
        if k < len(ts) and ok[k]:
            start = k if start is None else start
        elif start is not None:
            a, b = ts[start], ts[k - 1] + win
            if b - a > best[1] - best[0]:
                best = (a, b)
            start = None
    return best


def devoice(y, sr, a, b, fade=0.008, n=512, hop=128, smooth_hz=700, max_db=None):
    """Replace ``y[a:b]`` with deterministic aperiodic noise shaped by its spectrum.

    Uses padded overlap-add so every requested sample (including the final
    partial hop) is covered. This avoids the silent tail that can otherwise
    occur when the segment length is not an exact multiple of the STFT hop.
    """
    y = np.asarray(y, dtype=np.float32)
    if y.ndim != 1:
        raise ValueError('devoice expects a mono 1-D signal')
    if sr <= 0 or n < 64 or hop < 1 or smooth_hz <= 0 or fade < 0:
        raise ValueError('invalid devoice parameters')
    a, b = max(0, int(a)), min(len(y), int(b))
    if b <= a:
        return y
    f = min(int(round(fade * sr)), (b - a) // 2)
    a0, b0 = max(0, a - f), min(len(y), b + f)
    seg = np.nan_to_num(y[a0:b0].astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if len(seg) < 64 or np.sqrt(np.mean(seg * seg)) < 1e-8:
        return y
    # Adapt the frame size for short fricatives while retaining useful spectral resolution.
    n = min(int(n), 1 << int(np.floor(np.log2(len(seg)))))
    n = max(64, n - (n % 2))
    if len(seg) < n:
        return y
    hop = min(int(hop), max(1, n // 4))
    pad = n // 2
    mode = 'reflect' if len(seg) > 1 else 'edge'
    padded = np.pad(seg, (pad, pad), mode=mode)
    win = np.hanning(n + 1)[:-1].astype(np.float64)  # periodic Hann for stable overlap-add
    k = max(1, min(n // 2, int(round(smooth_hz / (sr / n)))))
    if k % 2 == 0:
        k += 1
    rng = np.random.default_rng(int(a) & 0xffffffff)
    out = np.zeros(len(padded), dtype=np.float64)
    norm = np.zeros(len(padded), dtype=np.float64)
    starts = list(range(0, len(padded) - n + 1, hop))
    if starts[-1] != len(padded) - n:
        starts.append(len(padded) - n)
    for s0 in starts:
        frame = padded[s0:s0 + n] * win
        mag = np.abs(np.fft.rfft(frame))
        log_env = np.log(mag + 1e-9)
        env = np.exp(np.convolve(log_env, np.ones(k, dtype=np.float64) / k, mode='same'))
        phase = rng.uniform(-np.pi, np.pi, len(env))
        phase[0] = 0.0
        if n % 2 == 0:
            phase[-1] = 0.0
        shaped = np.fft.irfft(env * np.exp(1j * phase), n)
        out[s0:s0 + n] += shaped * win
        norm[s0:s0 + n] += win * win
    processed = out / np.maximum(norm, 1e-8)
    processed = processed[pad:pad + len(seg)]
    rms_in = np.sqrt(np.mean(seg * seg)) + 1e-12
    rms_out = np.sqrt(np.mean(processed * processed)) + 1e-12
    processed *= rms_in / rms_out
    if max_db is not None:
        level = 20.0 * np.log10(np.sqrt(np.mean(processed * processed)) + 1e-12)
        if level > max_db:
            processed *= 10.0 ** ((max_db - level) / 20.0)
    blend = np.ones(len(seg), dtype=np.float64)
    if f:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, f))
        blend[:f] = ramp
        blend[-f:] = ramp[::-1]
    result = y.copy()
    result[a0:b0] = (seg * (1.0 - blend) + processed * blend).astype(np.float32)
    return result


def _fft_filter(x, sr, gain_at_frequency):
    """Apply a zero-phase frequency response with reflected edge padding."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1:
        raise ValueError('audio filter expects a mono 1-D signal')
    if not np.isfinite(sr) or int(sr) != sr or sr <= 0:
        raise ValueError('sample rate must be a finite positive integer')
    sr = int(sr)
    if not len(x):
        return x.copy()
    pad = min(max(32, int(round(0.025 * sr))), max(0, len(x) - 1))
    padded = np.pad(x, (pad, pad), mode='reflect' if len(x) > 1 else 'edge')
    nfft = 1 << int(np.ceil(np.log2(max(len(padded), 2))))
    spectrum = np.fft.rfft(padded, nfft)
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sr)
    gain_db = np.asarray(gain_at_frequency(frequencies), dtype=np.float64)
    filtered = np.fft.irfft(spectrum * 10.0 ** (gain_db / 20.0), nfft)[:len(padded)]
    return filtered[pad:pad + len(x)].astype(np.float32)


def band_eq(x, sr, centres_hz, gains_db):
    """Zero-phase EQ: gains (dB) at band centres, interpolated over log frequency."""
    centres_hz = np.asarray(centres_hz, dtype=np.float64)
    gains_db = np.asarray(gains_db, dtype=np.float64)
    if centres_hz.ndim != 1 or gains_db.ndim != 1 or len(centres_hz) != len(gains_db) or len(centres_hz) < 2:
        raise ValueError('EQ centres and gains must be 1-D arrays of equal length >= 2')
    if (not np.all(np.isfinite(centres_hz)) or not np.all(np.isfinite(gains_db))
            or np.any(centres_hz <= 0) or np.any(np.diff(centres_hz) <= 0)):
        raise ValueError('EQ centres must be positive/increasing and gains finite')
    log_centres = np.log(centres_hz)
    return _fft_filter(x, sr, lambda f: np.interp(np.log(np.maximum(f, 1.0)), log_centres, gains_db))


def boost_fundamental(x, sr, f0, gain_db=6.0, width_oct=0.8):
    """Raise the first harmonic (a bell around f0), then preserve the original RMS."""
    if not np.isfinite(f0) or f0 <= 0 or not np.isfinite(gain_db) or width_oct <= 0:
        raise ValueError('f0 and width_oct must be positive; gain_db must be finite')
    x = np.asarray(x, dtype=np.float32)
    if not len(x):
        return x.copy()
    y = _fft_filter(x, sr, lambda f: gain_db * np.exp(
        -0.5 * (np.log2(np.maximum(f, 1.0) / f0) / (width_oct / 2.355)) ** 2))
    rms_x = np.sqrt(np.mean(np.square(x, dtype=np.float64)))
    rms_y = np.sqrt(np.mean(np.square(y, dtype=np.float64)))
    if rms_x > 0 and rms_y > 1e-12:
        y *= rms_x / rms_y
    return y.astype(np.float32)


def high_shelf(x, sr, gain_db, fc=2000.0):
    """Zero-phase high shelf with a smooth one-octave transition around ``fc``."""
    if not np.isfinite(gain_db) or not np.isfinite(fc) or fc <= 0:
        raise ValueError('gain_db must be finite and fc positive')
    return _fft_filter(x, sr, lambda f: gain_db * np.clip(
        np.log2(np.maximum(f, 1.0) / fc) + 0.5, 0.0, 1.0))


def dip_at(x, sr, t, reach=0.08, ctx=0.07, hop=0.005, win=0.02):
    """Level envelope around t: how far (dB) it sinks below the line joining the levels just before
    and after. Returns (depth_db, times, gain_db) where gain_db would lift the envelope onto that line."""
    n, w = int(hop * sr), int(win * sr)
    ts = np.arange(t - reach - ctx, t + reach + ctx, hop)
    env = np.array([rms_db(cut(x, int(u * sr) - w // 2, int(u * sr) + w // 2)) for u in ts])
    pre, post = ts < t - reach, ts > t + reach
    if not pre.any() or not post.any():
        return 0.0, ts, np.zeros_like(ts)
    a, b = np.median(env[pre]), np.median(env[post])
    line = np.interp(ts, [t - reach, t + reach], [a, b])
    gain = np.where(pre | post, 0.0, np.clip(line - env, 0, None))
    return float(gain.max()), ts, np.convolve(gain, np.ones(5) / 5, 'same')


def fill_dip(x, sr, t, max_db=12.0):
    """Lift a level dip at t (a re-attacked vowel in a VCV recording) onto the surrounding level."""
    depth, ts, gain = dip_at(x, sr, t)
    if depth < 1.5:
        return x, depth
    g = 10 ** (np.clip(np.interp(np.arange(len(x)) / sr, ts, gain, left=0, right=0), 0, max_db) / 20)
    return (x * g).astype(np.float32), depth


def rms_db(x):
    x = np.asarray(x, dtype=np.float64)
    if not x.size:
        return -120.0
    x = np.nan_to_num(x, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return float(20.0 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9))



def perceptual_level_db(x, sr):
    """Approximate A-weighted RMS level for comparing sustained vocal segments.

    It is intended for relative normalization within one voicebank, not as a
    standards-certified loudness meter.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1 or not len(x) or sr <= 0:
        return -120.0
    x = np.nan_to_num(x, copy=False)
    n = min(len(x), max(256, int(round(0.4 * sr))))
    if len(x) > n:
        starts = np.linspace(0, len(x) - n, min(5, max(1, len(x) // n)), dtype=int)
        frames = np.stack([x[a:a + n] for a in starts])
    else:
        frames = x[None, :]
    nfft = 1 << int(np.ceil(np.log2(max(256, frames.shape[1]))))
    freq = np.fft.rfftfreq(nfft, 1.0 / sr)
    f2 = freq * freq
    ra_num = (12194.0 ** 2) * (f2 ** 2)
    ra_den = ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
              * (f2 + 12194.0 ** 2))
    ra = ra_num / np.maximum(ra_den, 1e-30)
    a_db = 20.0 * np.log10(np.maximum(ra, 1e-12)) + 2.0
    weight = 10.0 ** (a_db / 20.0)
    vals = []
    window = np.hanning(frames.shape[1])
    for frame in frames:
        spec = np.fft.rfft(frame * window, nfft)
        vals.append(float(np.sqrt(np.mean(np.abs(spec * weight) ** 2)) / max(np.sqrt(np.mean(window ** 2)), 1e-12)))
    return float(20.0 * np.log10(np.mean(vals) + 1e-12))


def analyze_audio_quality(x, sr):
    """Return inexpensive recording-health metrics and a conservative 0-100 score.

    The score is a ranking hint, not a universal judgement of vocal quality.
    A quiet/breathy take can still be musically useful; callers should apply
    penalties gently and retain user control over the source recordings.
    """
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1 or sr <= 0:
        raise ValueError('quality analysis expects mono audio and a positive sample rate')
    if not len(x):
        return {'score': 0.0, 'duration': 0.0, 'peak_dbfs': -120.0, 'rms_dbfs': -120.0,
                'clipped_fraction': 0.0, 'dc_offset': 0.0, 'noise_floor_dbfs': -120.0,
                'snr_estimate_db': 0.0, 'spectral_flatness': 0.0, 'warnings': ['empty audio']}
    y = np.nan_to_num(x.astype(np.float64), copy=False)
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y * y) + 1e-18))
    dc = float(abs(np.mean(y)))
    clipped = float(np.mean(np.abs(y) >= 0.999))
    frame_n = max(128, int(round(0.020 * sr)))
    hop_n = max(1, frame_n // 2)
    if len(y) < frame_n:
        frames = y[None, :]
    else:
        total = max(1, (len(y) - frame_n) // hop_n + 1)
        count = min(512, total)
        starts = np.linspace(0, max(0, len(y) - frame_n), count, dtype=int)
        frames = np.stack([y[a:a + frame_n] for a in starts])
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-18)
    frame_db = 20.0 * np.log10(frame_rms + 1e-12)
    active = frame_db > np.percentile(frame_db, 20) + 3.0
    if active.any():
        noise_db = float(np.percentile(frame_db[active], 10))
        signal_db = float(np.percentile(frame_db[active], 90))
    else:
        noise_db = signal_db = float(np.median(frame_db))
    snr = max(0.0, signal_db - noise_db)
    nfft = 1 << int(np.ceil(np.log2(max(256, min(len(y), int(0.2 * sr))))))
    sample = y[:min(len(y), nfft)]
    if len(sample) < nfft:
        sample = np.pad(sample, (0, nfft - len(sample)))
    mag = np.abs(np.fft.rfft(sample * np.hanning(nfft))) + 1e-12
    flatness = float(np.exp(np.mean(np.log(mag))) / np.mean(mag))
    # Score is deliberately conservative: clipping and DC are clear defects;
    # SNR and flatness are only soft clues because breathy/fricative material
    # can naturally be noise-like.
    penalty = min(45.0, clipped * 5000.0)
    penalty += min(15.0, max(0.0, dc - 0.002) * 1500.0)
    penalty += min(22.0, max(0.0, 22.0 - snr) * 0.55)
    penalty += min(8.0, max(0.0, flatness - 0.65) * 12.0)
    score = float(np.clip(100.0 - penalty, 0.0, 100.0))
    warnings = []
    if clipped > 0.0005:
        warnings.append('possible clipping')
    if dc > 0.01:
        warnings.append('DC offset')
    if snr < 12.0:
        warnings.append('low estimated SNR')
    if flatness > 0.75:
        warnings.append('noise-like spectrum; may be natural for fricatives or breath sounds')
    return {'score': score, 'duration': len(y) / float(sr),
            'peak_dbfs': float(20.0 * np.log10(peak + 1e-12)),
            'rms_dbfs': float(20.0 * np.log10(rms + 1e-12)),
            'clipped_fraction': clipped, 'dc_offset': dc,
            'noise_floor_dbfs': noise_db, 'snr_estimate_db': snr,
            'spectral_flatness': flatness, 'warnings': warnings}


def mean_f0(ft, t0, t1):
    f0, f1 = ft.t2f(t0), ft.t2f(t1)
    lag = ft.lag[f0:f1 + 1][ft.voiced[f0:f1 + 1]]
    return float(ft.sr / np.median(lag)) if len(lag) else 0.0
