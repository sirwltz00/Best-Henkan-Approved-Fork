"""Signal-path regression tests for WAV I/O, resampling and analysis quality."""
import os
import struct
import sys
import tempfile
import unittest
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert  # noqa: E402

SR = 44100


def sine(freq, seconds=1.0, sr=SR, amplitude=0.4):
    t = np.arange(round(seconds * sr), dtype=np.float64) / sr
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def make_wave(fmt, data):
    def chunk(tag, body):
        return tag + struct.pack('<I', len(body)) + body + (b'\0' if len(body) & 1 else b'')
    body = b'WAVE' + chunk(b'fmt ', fmt) + chunk(b'data', data)
    return b'RIFF' + struct.pack('<I', len(body)) + body


class WavIO(unittest.TestCase):
    def test_extensible_pcm_valid_bits_and_chunk_padding(self):
        sr = 48000
        # 24 valid bits in a 32-bit, left-aligned PCM container.
        pcm = np.array([0, 1 << 30, -(1 << 30), (1 << 31) - 256], dtype='<i4')
        guid_pcm = b'\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71'
        fmt = struct.pack('<HHIIHHHHI', 0xFFFE, 1, sr, sr * 4, 4, 32, 22, 24, 0) + guid_pcm
        with tempfile.NamedTemporaryFile(suffix='.wav') as f:
            f.write(make_wave(fmt, pcm.tobytes()))
            f.flush()
            got, got_sr = audio.read_wav(f.name)
        self.assertEqual(got_sr, sr)
        np.testing.assert_allclose(got, [0.0, 0.5, -0.5, (2**31 - 256) / 2**31], atol=2e-7)

    def test_antiphase_stereo_does_not_downmix_to_silence(self):
        x = sine(440, seconds=0.2)
        interleaved = np.column_stack((x * 32767, -x * 32767)).astype('<i2')
        fmt = struct.pack('<HHIIHH', 1, 2, SR, SR * 4, 4, 16)
        with tempfile.NamedTemporaryFile(suffix='.wav') as f:
            f.write(make_wave(fmt, interleaved.tobytes()))
            f.flush()
            got, sr = audio.read_wav(f.name)
        self.assertEqual(sr, SR)
        self.assertGreater(audio.rms_db(got), -12.0)
        self.assertGreater(np.max(np.abs(got)), 0.3)

    def test_truncated_riff_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix='.wav') as f:
            f.write(b'RIFF\x80\x00\x00\x00WAVE')
            f.flush()
            with self.assertRaisesRegex(ValueError, 'truncated'):
                audio.read_wav(f.name)


class Resampling(unittest.TestCase):
    def test_downsampling_suppresses_frequencies_above_new_nyquist(self):
        x = sine(3000, seconds=0.6, sr=48000, amplitude=0.3)
        x += sine(12000, seconds=0.6, sr=48000, amplitude=0.3)
        y = audio.resample(x, 48000, 16000)
        self.assertEqual(len(y), 9600)
        # Ignore filter transients at both edges.
        core = y[800:-800].astype(np.float64)
        n = 1 << int(np.ceil(np.log2(len(core))))
        spectrum = np.abs(np.fft.rfft(core * np.hanning(len(core)), n))
        freqs = np.fft.rfftfreq(n, 1 / 16000)
        passband = spectrum[np.abs(freqs - 3000) < 100].max()
        alias = spectrum[np.abs(freqs - 4000) < 100].max()
        self.assertGreater(passband, 100.0)
        self.assertLess(alias / passband, 0.02)

    def test_constant_signal_remains_constant_at_edges(self):
        y = audio.resample(np.full(1000, 0.25, np.float32), 48000, 44100)
        np.testing.assert_allclose(y, 0.25, atol=2e-5)

    def test_invalid_sample_rates_are_rejected(self):
        with self.assertRaises(ValueError):
            audio.resample(np.ones(10, np.float32), 0, 44100)

    def test_non_integer_sample_rates_are_rejected(self):
        with self.assertRaises(ValueError):
            audio.resample(np.ones(10, np.float32), 44100.5, 48000)
        with tempfile.NamedTemporaryFile(suffix='.wav') as f:
            with self.assertRaises(ValueError):
                audio.write_wav(f.name, np.ones(10, np.float32), 44100.5)

    def test_resampler_sanitizes_non_finite_input(self):
        x = np.array([0.0, np.nan, np.inf, -np.inf, 0.25], dtype=np.float32)
        y = audio.resample(x, 44100, 44100)
        self.assertTrue(np.isfinite(y).all())
        np.testing.assert_allclose(y, [0.0, 0.0, 1.0, -1.0, 0.25])


class FeatureAnalysis(unittest.TestCase):
    def test_feature_batching_is_deterministic_across_batch_sizes(self):
        x = sine(220, seconds=3.4) + 0.03 * sine(3300, seconds=3.4)
        original = audio.Features.FRAME_BATCH
        try:
            audio.Features.FRAME_BATCH = 37
            small = audio.Features(x, SR)
            audio.Features.FRAME_BATCH = 512
            large = audio.Features(x, SR)
        finally:
            audio.Features.FRAME_BATCH = original
        for name in ('energy', 'period', 'lag', 'spec', 'hf_ratio', 'spectral_flux',
                     'short_energy', 'short_hf_ratio', 'short_hf_db', 'voiced'):
            np.testing.assert_allclose(getattr(small, name), getattr(large, name), atol=1e-6,
                                       err_msg=name)

    def test_silence_is_not_classified_as_voiced(self):
        ft = audio.Features(np.zeros(SR // 2, np.float32), SR)
        self.assertFalse(ft.voiced.any())
        self.assertTrue(np.isfinite(ft.energy).all())

    def test_quiet_but_real_audio_is_not_mistaken_for_silence(self):
        ft = audio.Features(sine(220, seconds=0.5, amplitude=0.0001), SR)
        middle = ft.t2f(0.25)
        self.assertFalse(ft.silent(middle))
        self.assertTrue(ft.voiced[middle])

    def test_dc_offset_does_not_bias_pitch_or_level_features(self):
        x = sine(220, seconds=0.5, amplitude=0.2)
        clean = audio.Features(x, SR)
        biased = audio.Features(x + 0.25, SR)
        np.testing.assert_allclose(biased.energy, clean.energy, atol=1e-4)
        np.testing.assert_allclose(biased.lag, clean.lag, atol=0.0)
        np.testing.assert_allclose(biased.hf_ratio, clean.hf_ratio, atol=0.1)

    def test_stable_region_uses_voiced_frames_only(self):
        x = sine(220, seconds=0.8)
        ft = audio.Features(x, SR)
        # Explicitly mark the first half unreliable. The later voiced section
        # remains eligible, while invalid frames cannot win by arbitrary lag.
        ft.voiced[:60] = False
        a, b = audio.stable_region(ft, 0.0, 0.75)
        self.assertGreaterEqual(a, 0.18)
        self.assertGreater(b, a)

    def test_f0_tracker_tracks_several_pitch_ranges(self):
        for frequency in (90.0, 220.0, 440.0, 800.0):
            x = sine(frequency, seconds=0.8, amplitude=0.2)
            times, pitches = audio.f0_track(x, SR, 0.10, 0.70)
            valid = pitches[np.isfinite(pitches)]
            self.assertGreater(len(valid), 40)
            self.assertAlmostEqual(float(np.median(valid)), frequency, delta=frequency * 0.025)

    def test_f0_tracker_marks_unvoiced_frames_as_missing(self):
        x = sine(220, seconds=0.3, amplitude=0.2)
        x = np.concatenate((x, np.zeros(int(0.2 * SR), np.float32),
                            sine(220, seconds=0.3, amplitude=0.2)))
        times, pitches = audio.f0_track(x, SR, 0.05, 0.75)
        gap = pitches[(times >= 0.34) & (times <= 0.44)]
        self.assertGreater(len(gap), 5)
        self.assertTrue(np.isnan(gap).all())


class AudioTransforms(unittest.TestCase):
    def test_devoice_covers_the_entire_requested_tail_without_zero_padding(self):
        x = sine(180, seconds=0.35, amplitude=0.3)
        y = audio.devoice(x, SR, int(0.04 * SR), int(0.30 * SR), n=512, hop=128)
        self.assertEqual(y.shape, x.shape)
        self.assertTrue(np.isfinite(y).all())
        self.assertGreater(audio.rms_db(y[int(0.285 * SR):int(0.305 * SR)]), -35.0)
        self.assertGreater(np.mean(np.abs(y[int(0.285 * SR):int(0.305 * SR)])), 0.01)
        self.assertGreater(np.mean(np.abs(y[int(0.20 * SR):int(0.29 * SR)] - x[int(0.20 * SR):int(0.29 * SR)])), 0.02)

    def test_filters_do_not_change_signal_when_gain_is_zero(self):
        x = sine(500, seconds=0.2)
        y = audio.band_eq(x, SR, [100, 1000, 10000], [0, 0, 0])
        z = audio.high_shelf(x, SR, 0.0)
        np.testing.assert_allclose(y, x, atol=1e-6)
        np.testing.assert_allclose(z, x, atol=1e-6)

    def test_fundamental_boost_preserves_rms(self):
        x = sine(220, seconds=0.25) + 0.15 * sine(440, seconds=0.25)
        y = audio.boost_fundamental(x, SR, 220)
        self.assertAlmostEqual(audio.rms_db(y), audio.rms_db(x), delta=0.05)


class ConverterReliability(unittest.TestCase):
    def test_vowel_led_vcv_can_derive_a_phrase_start(self):
        silence = np.zeros(int(0.25 * SR), np.float32)
        vowel = sine(220, seconds=0.45, amplitude=0.25)
        fricative = np.random.default_rng(8).normal(0, 0.06, int(0.12 * SR)).astype(np.float32)
        tail = sine(220, seconds=0.35, amplitude=0.25)
        x = np.concatenate((silence, vowel, fricative, tail))
        ft = audio.Features(x, SR)
        entry = SimpleNamespace(wav='synthetic.wav', offset=0.0)
        clip = convert.Clip(entry, ['a', 'z', 'a'], 0.34, 1.05, [0.70, 0.82], [True, True])
        converter = convert.Converter.__new__(convert.Converter)
        converter.load = lambda _path: (x, ft)
        converter.dict = None
        derived = converter.derive_silence(clip)
        self.assertTrue(any(c.derived == 'lead-sil' and c.phns[:2] == ['Sil', 'a'] for c in derived))

    def test_audio_cache_is_lru_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for i in range(3):
                path = os.path.join(root, '%d.wav' % i)
                audio.write_wav(path, sine(220, seconds=0.08 + i * 0.01), SR)
                paths.append(path)
            converter = convert.Converter.__new__(convert.Converter)
            converter._audio = OrderedDict()
            converter._audio_cache_bytes = 0
            converter._audio_cache_limit = 1
            first = converter.load(paths[0])
            second = converter.load(paths[1])
            self.assertEqual(len(converter._audio), 1)
            self.assertIn(paths[1], converter._audio)
            # Eviction drops only the cache's reference, not the returned data.
            self.assertGreater(len(first[0]), 0)
            self.assertGreater(len(second[0]), 0)


class AdvancedAnalysis(unittest.TestCase):
    def test_detailed_pitch_tracker_follows_a_large_register_change(self):
        low = sine(140.0, seconds=0.4, amplitude=0.2)
        high = sine(520.0, seconds=0.4, amplitude=0.2)
        x = np.concatenate((low, high))
        times, pitches, confidence = audio.f0_track_detailed(x, SR, 0.08, 0.75)
        early = pitches[(times >= 0.10) & (times <= 0.32)]
        late = pitches[(times >= 0.50) & (times <= 0.72)]
        self.assertAlmostEqual(float(np.nanmedian(early)), 140.0, delta=4.0)
        self.assertAlmostEqual(float(np.nanmedian(late)), 520.0, delta=12.0)
        self.assertGreater(float(np.nanmedian(confidence)), 0.5)

    def test_voicing_confidence_separates_tone_from_broadband_noise(self):
        tone = audio.Features(sine(220.0, seconds=0.5), SR)
        rng = np.random.default_rng(91)
        noise = audio.Features((0.2 * rng.standard_normal(SR // 2)).astype(np.float32), SR)
        self.assertGreater(float(np.median(tone.voicing_confidence)), 0.8)
        self.assertLess(float(np.median(noise.voicing_confidence)), 0.5)
        self.assertEqual(tone.mfcc.shape[1], 13)
        self.assertTrue(np.isfinite(tone.spectral_centroid).all())
        self.assertTrue(np.isfinite(tone.spectral_flatness).all())

    def test_boundary_confidence_rewards_an_acoustic_transition(self):
        rng = np.random.default_rng(7)
        hiss = (0.12 * rng.standard_normal(round(0.20 * SR))).astype(np.float32)
        x = np.concatenate((sine(220.0, seconds=0.20), hiss))
        ft = audio.Features(x, SR)
        detected = audio.boundary_confidence(ft, 0.20, 0.15, 0.25, detected=True)
        fallback = audio.boundary_confidence(ft, 0.20, detected=False)
        self.assertGreater(detected, fallback)
        self.assertGreaterEqual(detected, 0.0)
        self.assertLessEqual(detected, 1.0)

    def test_quality_analysis_flags_clipping_and_reports_finite_metrics(self):
        clean = sine(220.0, seconds=0.5)
        clipped = np.clip(clean * 8.0, -1.0, 1.0)
        q_clean = audio.analyze_audio_quality(clean, SR)
        q_clipped = audio.analyze_audio_quality(clipped, SR)
        self.assertGreater(q_clean['score'], q_clipped['score'])
        self.assertGreater(q_clipped['clipped_fraction'], 0.05)
        for key in ('peak_dbfs', 'rms_dbfs', 'noise_floor_dbfs', 'snr_estimate_db',
                    'spectral_flatness', 'score'):
            self.assertTrue(np.isfinite(q_clean[key]), key)
        self.assertIn('possible clipping', q_clipped['warnings'])

    def test_perceptual_level_is_consistent_and_frequency_weighted(self):
        low = sine(100.0, seconds=0.3, amplitude=0.2)
        mid = sine(1000.0, seconds=0.3, amplitude=0.2)
        low_db = audio.perceptual_level_db(low, SR)
        mid_db = audio.perceptual_level_db(mid, SR)
        self.assertGreater(mid_db, low_db)
        self.assertAlmostEqual(audio.perceptual_level_db(mid, SR), mid_db, places=5)

    def test_lpc_formant_estimator_finds_resonant_vowel_regions(self):
        sr = 16000
        n = sr // 2
        rng = np.random.default_rng(0)
        source = rng.standard_normal(n)
        signal = np.zeros(n, dtype=np.float64)
        for frequency, bandwidth, gain in ((500.0, 80.0, 1.0),
                                           (1500.0, 120.0, 0.8),
                                           (2500.0, 150.0, 0.5)):
            radius = np.exp(-np.pi * bandwidth / sr)
            angle = 2.0 * np.pi * frequency / sr
            resonated = np.zeros(n, dtype=np.float64)
            for i in range(2, n):
                resonated[i] = (source[i] + 2.0 * radius * np.cos(angle) * resonated[i - 1]
                                - radius * radius * resonated[i - 2])
            signal += gain * resonated
        formants = audio.estimate_formants(signal.astype(np.float32), sr, 0.10, 0.40)
        np.testing.assert_allclose(formants[:3], [500.0, 1500.0, 2500.0], rtol=0.08, atol=50.0)

    def test_unit_ranking_penalizes_clipped_sources(self):
        from types import SimpleNamespace
        from u2v.convert import Clip, Converter

        clean = sine(220.0, seconds=0.5, amplitude=0.2)
        clipped = np.clip(clean * 8.0, -1.0, 1.0)

        def candidate(path):
            entry = SimpleNamespace(wav=path, dup=0)
            return Clip(entry, ['a', 'i'], 0.0, 0.5, [0.25], [True],
                        boundary_confidence=[0.9])

        def scorer(signal):
            converter = Converter.__new__(Converter)
            converter._quality_cache = {}
            converter.load = lambda _path: (signal, audio.Features(signal, SR))
            converter.typical = None
            return converter.unit_score(candidate('take.wav'), 0)

        self.assertGreater(scorer(clean), scorer(clipped))


if __name__ == '__main__':
    unittest.main()
