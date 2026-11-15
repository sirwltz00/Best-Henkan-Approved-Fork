# Henkan audio analysis

This note documents the signal path used by `u2v/audio.py` and the parts of
`u2v/convert.py` that consume it. Henkan is a deterministic, sample-based
converter. It does not use a neural model or attempt to replace the voicebank's
recording style.

## 1. Input audio

`read_wav()` decodes little-endian RIFF/WAVE PCM (8, 16, 24 and 32 bit) and
IEEE-float (32 and 64 bit), including `WAVE_FORMAT_EXTENSIBLE` PCM/float files.
It checks chunk boundaries, frame alignment and format metadata rather than
silently truncating malformed sample data. It returns finite mono `float32`
samples and the original sample rate. Multi-channel audio is averaged; when
that average would almost cancel because channels are strongly out of phase,
the strongest channel is retained instead of returning near-silence.

`resample()` uses a windowed-sinc low-pass interpolator. The filter support
widens when downsampling more aggressively, and temporary arrays are chunked
so long input recordings do not require one giant output-by-taps matrix. The
converter still performs analysis and writes converted clips at 44.1 kHz.

## 2. Frame analysis

`Features` keeps a 5 ms frame grid and computes two time scales:

- 40 ms windows for energy, autocorrelation periodicity, pitch period and
  24-band log-spectral shape;
- 12 ms windows on the same grid for sharper high-frequency / energy edges
  around short consonants and fricatives.

Frames are mean-centered before analysis to prevent DC offsets from biasing
energy, periodicity and the lower spectral bands. Positive spectral flux is
also retained as a transient cue. Feature extraction is batched (512 frames at
a time by default), which bounds peak temporary memory for long recordings.
The small per-frame feature arrays remain available to the segmenter.

Silence detection is relative to the file's level and noise floor. Digital
silence receives a dedicated threshold, but quiet recordings are not classified
as silence merely because their peak is below -70 dBFS. Voicing additionally
requires periodicity, sufficient local level and a non-vowel-like high-frequency
balance.

## 3. Boundary placement

`oto.ini` remains an anchor and fallback, not a source of unquestioned truth.
The converter searches bounded time ranges around OTO timing and uses the most
appropriate cue for the transition:

- voicing onset/offset for vowel and unvoiced-consonant transitions;
- high-band onset/offset for voiced fricatives such as `z`, `Z`, `v` and `D`;
- spectral change for vowel-to-vowel transitions;
- energy changes where a more specific cue is unavailable.

If a cue is ambiguous, the detector reports that it did not confidently detect
the boundary and retains the OTO-derived position. It does not invent a precise
boundary from weak evidence.

Japanese VOCALOID labels remain `dz` / `dZ`. In a vowel-surrounded medial VCV
context the acoustic analysis target is interpreted as [z] / [Z], while
phrase-initial and post-nasal contexts retain affricate analysis. The exported
phoneme label is not renamed.

## 4. Pitch and sustained regions

`f0_track()` anchors a pitch search from autocorrelation and follows a bounded
period neighbourhood frame by frame to reduce octave jumps. Frames that do not
show enough periodicity are returned as `NaN`; they are not assigned a made-up
pitch. `clean_span()` ignores unreliable pitch frames for its statistics and
will keep the original span when there is not enough trustworthy pitch data.
`stable_region()` likewise scores only windows with enough voiced frames.

`sustained_extent()` follows a voiced, spectrally similar vowel or nasal beyond
the OTO range, stopping when voicing, level or spectral shape changes. This
feeds stationary selection and loop preparation. Vowel-led VCV recordings can
also derive a phrase-start transition when there is sufficient preceding clean
silence and a clean voiced attack.

## 5. Audio repairs

- Medial `V-dz-V` / `V-dZ-V` repair is limited to the vowel-surrounded context.
  It only fills a detected closure; an already-continuous fricative is left
  unchanged.
- `devoice()` shapes aperiodic noise from the recorded spectrum for unvoiced
  material. Its padded overlap-add covers the final partial hop as well as the
  middle of the clip, avoiding a zero-filled tail.
- EQ, high-shelf and fundamental-balance operations use reflected edge padding
  to reduce FFT wraparound artefacts at clip boundaries.

These are local repairs, not a general-purpose denoiser. They cannot recover
information that was never recorded.

## 6. Memory and validation

The converter caches decoded audio and its features with an LRU budget of
256 MiB. Large banks no longer cause the cache to grow without bound; a file
that is evicted may be decoded and analysed again later, trading some time for
predictable memory use.

Run the test suite on either Windows or a development host with:

```bash
python -m pytest -q
```

The V3/V4 installer helpers that do not need the Windows registry remain
importable on other platforms. Actual registry discovery and installation are
Windows-only; registry-dependent integration cases are skipped where the
Windows registry is unavailable.

The suite includes synthetic WAV-format, anti-aliasing, pitch, boundary,
medial-fricative, phrase-start, cache-eviction and overlap-add regressions.
Synthetic tests prove specific signal properties; they do not replace
listening to real voicebanks and checking a render in the target VOCALOID
editor.

## Known limitations

- Pitch tracking is autocorrelation-based. Whispered, very breathy, noisy,
  heavily layered or strongly inharmonic material can lack a reliable F0.
- A voiced-fricative edge can be hard to distinguish from a stop release when
  a recording has no clear spectral or energy contrast. In that case the OTO
  anchor is intentionally kept.
- Spliced or repaired units remain approximations. Real recordings are
  preferable whenever a suitable unit exists.
