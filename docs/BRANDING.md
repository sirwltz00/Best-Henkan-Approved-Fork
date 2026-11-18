# Henkan FE brand assets

The complete, editable brand kit lives in [`assets/brand/`](../assets/brand/). Keep the original SVG and raster
files together so the app, Windows build, README, and future materials use the same source artwork.

## Identity

Henkan FE keeps the original Henkan waveform-to-keyboard symbol so the source project's identity is preserved. The
light and dark wordmarks are adapted to read **Henkan FE** and **FAN EDITION · VOICEBANK CONVERTER**.

## Files and intended use

| File | Use |
|---|---|
| `henkan-icon.svg` / `henkan-icon-1024.png` | Full app icon artwork |
| `henkan-icon.ico` | Windows executable and native window icon |
| `henkan-icon-512.png` through `henkan-icon-16.png` | Raster icon sizes for app surfaces and integrations |
| `henkan-mark.svg` | Standalone symbol when the full app-icon background is not wanted |
| `henkan-wordmark.svg` / `henkan-wordmark.png` | Horizontal logo for light backgrounds, including this README |
| `henkan-wordmark-dark.svg` / `henkan-wordmark-dark.png` | Horizontal logo for dark backgrounds |

The PNG wordmarks are previews of their corresponding editable SVG artwork, adapted for the Henkan FE name. The icon symbol itself remains inherited from the original Henkan artwork. The SVG wordmarks use system font
fallbacks and do not embed a proprietary font.

## Colour palette

| Name | Hex | Suggested role |
|---|---|---|
| Deep navy | `#10151F` / `#202A3A` | Main dark surfaces |
| Henkan FE pink | `#D6336C` | Primary accent |
| Light pink | `#F58AAE` | Accent highlights |
| Ivory | `#F7F1F4` | Light foregrounds |
| Slate | `#647084` | Secondary text and details |

## App integration

The CustomTkinter window loads `henkan-icon-128.png` from `assets/brand/` for its header and native window icon.
On Windows it also applies `henkan-icon.ico` to the native window. The PyInstaller GUI build embeds that ICO as the
`HenkanFE.exe` file icon and bundles the whole brand directory at `assets/brand/`; the GUI bundle self-test checks
that the required resources are present.

The build commands in [BUILDING_WINDOWS.md](../BUILDING_WINDOWS.md) and
[`.github/workflows/build.yml`](../.github/workflows/build.yml) should stay in sync when changing resource paths.
