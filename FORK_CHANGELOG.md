# Best-Henkan-Approved-Fork — engineering changes

This fork preserves Henkan's deterministic, recording-based UTAU-to-VOCALOID
conversion architecture. It does not add neural synthesis or replace source
voicebank recordings with generated speech.

## Audio analysis

- Reworked F0 tracking to search a broad period range per frame using window-corrected
  autocorrelation, with strong-period candidate selection and a
  continuity preference that does not stay trapped on stale subharmonics.
- Added `f0_track_detailed()` with per-frame confidence; retained `f0_track()`
  as a backward-compatible two-value wrapper.
- Added soft voicing confidence and a periodicity-derived HNR proxy.
- Added spectral centroid, spectral flatness, and a 13-coefficient DCT/MFCC-style
  timbral descriptor to `Features`.
- Added acoustic boundary-confidence scoring using spectral change, flux,
  voicing, energy and high-frequency cues.
- Added an optional LPC `estimate_formants()` helper for vowel analysis.

## Conversion and selection

- Candidate unit scoring now considers boundary confidence and cached recording
  health measurements, with gentle penalties so breathy/fricative recordings
  are not automatically rejected.
- Typical-timbre comparison now also uses MFCC-style descriptors, in addition
  to the existing spectral-band envelope.
- Sustained vowel normalization uses an A-weighted relative loudness estimate.
- Added peak headroom protection to normalization gain curves.
- Stationary stitching now checks normalized spectral-envelope similarity as
  well as waveform correlation before accepting a crossfade.
- Spectral correction for stationary timbre matching uses a smoother gain curve.

## Validation

- Added regression tests for large F0 register changes, voicing confidence,
  boundary confidence, clipping detection and perceptual level weighting.
- Synthetic tests are useful for deterministic signal properties but do not
  establish subjective quality. Real voicebank renders still need listening
  tests across languages, voice types, recording conditions and target editors.


## OTO interpretation and phoneme segmentation

- OTO rows are split from the right so aliases may contain commas without corrupting their timing fields.
- `prefix.map` rules are inherited only by OTO files in that folder and its descendants; unrelated appends no longer strip one another's aliases.
- Relative WAV references support slash normalization, nested subfolders, Unicode NFC and case-insensitive path resolution on case-sensitive filesystems.
- Malformed/non-finite timing rows and missing WAV references are reported with file and line context instead of being silently discarded.
- Shared-suffix inference requires stronger evidence, avoids one/two-entry over-stripping and no longer mistakes lowercase alias tails such as `ka` for group tags.
- C→V boundaries use the OTO fixed-region/consonant marker; V→C boundaries use the overlap marker where available; preutterance remains a separate note-onset prior and fallback.
- VCV/triphone cuts use the overlap prior for the outgoing V→C edge and the fixed-region marker for the incoming C→V edge. Acoustic boundary confidence may refine these priors, but weak evidence falls back to the OTO marker.
- Conversion reports include per-boundary confidence for downstream QA.

## DB build safety and GUI

- Added a full DBTool preflight for WAV format, transcriptions, segmentation geometry, directive occurrences, dictionary coverage, `.asN` consistency, missing/orphan sidecars and manifest references.
- DBTool build refuses existing partial-output collisions before running and verifies the generated `.dat`, `.tree` and embedded phoneme dictionary before reporting success.
- Added OTO Inspector in the GUI with group counts, unmapped aliases, malformed rows and missing-WAV diagnostics.
- Added one-click Audio QA from Convert/Build tabs, preflight/validate access from the Build tab, clearer fork branding and refreshed ttk styles.
- Added regression tests for transition-specific OTO markers, VCV boundaries, OTO edge cases and DBTool preflight/post-build verification.
