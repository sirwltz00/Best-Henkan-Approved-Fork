# Converter, OTO and DB build improvements

This document describes the non-audio-analysis changes in Best-Henkan-Approved-Fork. The project remains a deterministic UTAU-to-VOCALOID converter: it interprets source voicebank recordings and produces the sidecars and database input expected by the VOCALOID3 DBTool.

## OTO parsing

The OTO reader now treats the final five comma-separated fields as timing data, so commas inside an alias are preserved. It reports malformed and non-finite timing rows and missing WAV references with the source `oto.ini` path and line number.

`prefix.map` is append/folder scoped. A map in a folder applies to that folder's `oto.ini` files and descendants; sibling appends do not inherit its affixes. The parser also resolves nested relative WAV paths with slash normalization and case/Unicode-normalized component lookup, which makes copied banks more portable across case-sensitive filesystems.

Shared-suffix inference is deliberately conservative. Tiny OTO files do not provide enough examples to infer an alias suffix safely, and all-lowercase tails are retained because they may be phonetic content (`- ka`, `a ka`, `i ka`) rather than a recording-group label. Explicit `prefix.map` data remains the preferred way to describe arbitrary prefixes/suffixes.

## Phoneme boundary placement

OTO timing values describe different things and must not be used as if they were interchangeable. OpenUtau's voicebank-development guide describes offset as the phoneme start, overlap as the blend region with the previous note, preutterance as the musical note start, and consonant/fixed-region as the region that is not stretched or looped ([OpenUtau voicebank development](https://github.com/openutau/openutau.com/blob/main/wiki/Voicebank-development.md)).

Henkan uses these roles as priors:

- **C→V:** the fixed-region/consonant marker is the primary prior for the transition into a sustained vowel.
- **V→C:** the overlap marker is preferred for the outgoing transition when it is inside the usable sample region.
- **Preutterance:** retained as separate note-onset context and a fallback when the more specific marker is missing or invalid.
- **VCV/triphones:** use the overlap prior for the earlier V→C edge and the consonant/fixed-region prior for the later C→V edge.

The acoustic detector searches around the relevant OTO marker(s). A candidate can refine the marker when independent acoustic cues agree; low-confidence guesses revert to the marker instead of forcing a speculative cut. Boundaries are checked again for minimum segment length and monotonic order. The conversion manifest records boundary confidence so questionable edges can be found during QA.

This is a prior-based approach, not a claim that every OTO marker is perfectly authored. Unusual banks, custom phoneme inventories and nonstandard recording styles still need manual audition.

## DBTool preflight and output verification

Before opening the Windows DBTool, Henkan validates the complete converted folder:

- each `.trans` has phoneme text and valid articulation directives;
- every referenced phoneme exists in the selected dictionary;
- each transcription has its matching `.wav` and `.seg`;
- WAVs are mono, 44.1 kHz, uncompressed 16-bit PCM;
- `.seg` phonemes match `.trans`, segments are positive and contiguous, and timing does not run past the WAV;
- each directive has a matching `.asN` with the right phoneme sequence, cut range and ordered marks;
- manifest references resolve and orphan/missing sidecars are reported;
- DBTool text parser constraints (including `.trans` size and line limits) are checked before launch.

A build also refuses to overwrite an existing DB or partial `.dat`/`.tree` output. After DBTool exits, Henkan checks that both `.dat` and `.tree` exist and are nontrivial, then reads the PHDC phoneme dictionary back from the generated `.dat`. Missing `singer.inf` is a warning for manual inspection rather than an automatic success assumption.

## GUI improvements

- **OTO Inspector** shows folder/group counts, unmapped aliases, malformed timing rows and missing-WAV references before conversion.
- **Audio QA** runs the existing acoustic-quality report from the Convert and Build tabs.
- **Preflight / Validate** is directly available from the Build tab.
- The GUI uses clearer fork branding, more consistent control padding, a more legible notebook and an accent action style.

The GUI still uses the original Tkinter workflow and the existing tabs. This is a usability pass, not a replacement GUI framework.

## Validation and known limits

Run the automated suite from the project directory:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
python henkan.py --help
```

On Windows with the VOCALOID developer kit, also run `python henkan.py validate <converted-folder>`, build the singer DB, pack it, and audition CV, VCV, CVVC, English and append/pitch combinations in the target editor. The repository's automated tests cannot launch the Windows-only DBTool or substitute for real-bank listening tests.
