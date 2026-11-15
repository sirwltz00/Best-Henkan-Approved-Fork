# Real voicebank evaluation template

Use this checklist after a synthetic test run. Automated checks cannot verify
whether converted transitions sound natural in a VOCALOID editor.

## Voicebank and environment

- Voicebank / version:
- Language and phoneme system (CV / VCV / CVVC / Arpasing / other):
- Voice colour / pitch:
- Recording sample rate and bit depth:
- Recording environment / microphone notes:
- Henkan fork commit:
- VOCALOID editor and DBTool version:

## Analysis checks

| Test item | Expected / manually verified | Observed | Pass? |
|---|---|---|---|
| Vowel onset and offset | | | |
| Plosive closure and burst | | | |
| Unvoiced fricative onset | | | |
| Voiced fricative transition | | | |
| Vowel-to-vowel transition | | | |
| Nasal transition | | | |
| Pitch glide / register change | | | |
| Breath-heavy or soft phonation | | | |
| Low-level / noisy recording | | | |

## Listening checks

| Rendered phrase / unit | Source recording | Converted render | Issue heard | Severity |
|---|---|---|---|---|
| | | | | |
| | | | | |
| | | | | |

Listen specifically for doubled bursts, missing consonant attacks, clicks at
joins, abrupt timbre changes, loop flutter, loudness pumping, clipped peaks,
and unnatural vowel endings. Also note acceptable human characteristics such
as vibrato, breathiness and pitch instability; these are not automatically
defects and should not be flattened simply to make a waveform look cleaner.

## Boundary error measurements

For each manually labeled boundary, record the reference time and Henkan's
selected time. Report median absolute error and 95th-percentile absolute error
in milliseconds, separately by transition class. Keep OTO-fallback boundaries
separate from acoustically detected boundaries.

## Regression decision

- New issues introduced:
- Existing issues fixed:
- Voicebank-specific trade-offs:
- Safe to merge:
