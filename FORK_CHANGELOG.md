# Henkan Fan Edition (Henkan FE) — engineering changes

Henkan FE preserves the deterministic, recording-based UTAU-to-VOCALOID
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
- Added one-click Audio QA from Convert/Build tabs, preflight/validate access from the Build tab, clearer fork branding and an initial ttk layout refresh.
- Added regression tests for transition-specific OTO markers, VCV boundaries, OTO edge cases and DBTool preflight/post-build verification.


## VOCALOID4 VQM / numbered commercial-bank WAV import

- The Convert-tab VQM importer accepts one WAV, a `vqm/` folder, or a commercial voicebank root containing sibling `art/`, `sta/` and `vqm/` folders.
- It discovers `vqm_N.wav` files in numeric order and creates one internal, unsuffixed `Grw` stationary for each sample. `art/` and `sta/` are not imported; Henkan FE continues to build those from the UTAU source bank.
- A `vqm_samples.json` handoff is carried beside the built DB so original numeric `N` values survive DBTool's internal stationary IDs and are written to VQM records without renumbering.
- During Pack, each DBTool-generated `Grw` stationary contributes its first 100 `FRM2` analysis chunks and its `SND ` audio chunk to the output DDB; the native VQM block uses rebased pointers.
- No donor DDB is read and no growl is synthesized from an ordinary vowel. Samples must supply at least 100 DBTool EpR frames; short/unusable samples fail rather than silently shifting pitch indexes.
- The `.vvd` Growl capability line is enabled only after the VQM block and all referenced DDB chunks validate. Native VQM is VOCALOID4-only.
- Added regression tests for commercial `art/sta/vqm` folder discovery, multiple numbered samples, source-index preservation, VQM chunk construction, FRM2/SND pointer rebasing, DBV count handling and Growl flag gating.

## VOCALOID4 voice parameters and VSQX controls

- Added configurable V4 Singer Editor offsets for BRE/BRI/CLE/GEN (signed -127..127) to the pack CLI, Pack-tab GUI and Developer Editor DB_Dev.ini slot registration. Neutral defaults remain zero.
- Added a separate V4 control schema with validated ranges for DYN/BRE/BRI/CLE/GEN/POR/PIT/PBS/GWL/XSY. GWL and XSY require explicit opt-ins because they depend on VQM data and a secondary singer, respectively.
- Extended VSQX4 generation to serialize native `<cc>` control-change events and signed singer-level `<vPrm>` offsets. OPE and VEL are serialized per note (`nStyle/opening` and note velocity) rather than as track curves; the POR probe enables note portamento so the timing control is exercised.
- Added `tools/parameter_probe.py` to generate paired baseline/changed-parameter V4 projects for audibility checks, with optional Growl probe generation.
- Added validation and regression tests for parameter domains, VSQX XML placement, note-specific OPE/VEL, voice profile packaging and dev-slot output.
- Real VOCALOID4 Editor/DBTool audible validation remains required; synthetic tests validate serialization and range handling only.


## CustomTkinter GUI redesign

- Replaced the old tabbed ttk shell with a CustomTkinter dark workspace and persistent workflow sidebar.
- Added the Henkan FE Quick Start page, reorganized Convert / Build singer DB / Pack & Install / V3 → V2 navigation, and retained the existing workflow controls and specialist dialogs.
- Added a collapsible activity console with CLI output highlighting and a sticky process-status / progress footer.
- Updated the `.bat` launcher and requirements so CustomTkinter is installed on first launch with the other Python dependencies.
- Preserved the CLI-driven conversion/build/package commands and the existing settings file format.
- GUI rendering still needs a Windows smoke test with CustomTkinter installed; this environment did not have network access to install the GUI dependency or launch the Windows DBTool.


## Windows application packaging

- Added a PyInstaller GUI entry point and internal console worker so packaged Henkan FE can stream CLI output and stop background tasks without requiring Python on the target PC.
- Added a Windows GitHub Actions workflow that runs tests, builds `HenkanFE.exe` and `HenkanFEWorker.exe`, and uploads a ready-to-extract Windows ZIP.
- Frozen builds resolve the app install folder separately from PyInstaller's temporary extraction folder and look for DBTool beside the app or one directory above it.
- Kept `Henkan FE GUI.bat` as an optional source-checkout launcher; the packaged release uses the two frozen executables instead.
- The build does not bundle or download DBTool, Yamaha components, dictionaries or voicebank materials.


## PC requirements and compatibility diagnostics

- Added a dedicated **PC Requirements** page to the CustomTkinter sidebar and a shortcut from Quick Start.
- The live report displays Windows version / architecture, processor and logical cores, installed and available RAM, free space on the system temporary-file drive, display resolution, and whether the required `HenkanFEWorker.exe` helper is present.
- Added clear minimum and recommended targets for Windows 10/11 x64, CPU, RAM, workspace storage and display resolution. Dedicated GPU is not required.
- Separated Henkan FE hardware requirements from external prerequisites: DBTool, phonetic dictionary, VOCALOID editor, the conditional VC++ 2008 x86 runtime for DBTool error 14001, and UAC elevation for install/uninstall.
- Added `SYSTEM_REQUIREMENTS.md`, updated the README and Windows build guide, and included the requirements document in the GitHub Actions release ZIP.
- Added unit tests for minimum-spec PCs, unsupported OS/architecture, low RAM/disk, display constraints and a missing worker executable.
- The report is advisory and does not block startup; the legacy DBTool / actual VOCALOID editor still need separate compatibility verification.


## Windows PyInstaller data-file resolution

- Fixed the GUI and worker PyInstaller commands to resolve `v3v2/idtables.json` and `v3v2/v2_japanese_units.json` to absolute source paths before packaging. With custom `--specpath` directories under `build/`, PyInstaller was incorrectly looking for relative data inputs beneath `build/spec-gui/` or `build/spec-worker/`.
- Updated `BUILDING_WINDOWS.md` to use the same absolute-path approach for local PowerShell builds.


## Winlator-friendly Windows packaging layout

- Switched the GUI and worker PyInstaller commands from `--onefile` to `--onedir`, avoiding the one-file bootloader's temporary `_MEI...` extraction step.
- Kept `HenkanFE.exe` and `HenkanFEWorker.exe` at the root of the release ZIP so the GUI's existing same-folder worker lookup remains valid.
- Stored each executable's bundled files in separate `_henkan_fe_gui_runtime/` and `_henkan_fe_worker_runtime/` folders to prevent file collisions. Updated CI smoke-test paths, local build instructions and user-facing extraction guidance.
- A Windows CI run is still needed to validate the resulting frozen executables and test behavior under Winlator.
