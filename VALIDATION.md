# Validation — v0.2.0

- 34 unit and regression tests passed.
- Existing six-image fixtures continue to pass: 7 item previews and 30 numerical attributes.
- Missing rarity/name/power retains correctly paired attributes; partial labels are warned, and numeric-only rows are not guessed.
- Self-interface markers block inventory ingestion. Obscured or cropped panels get specific diagnostics.
- Linux virtual-display integration deliberately covered the game with both the assistant and a separate assistant-owned dialog.
- Both auto-docking and temporary-hide fallback returned pixel-exact game images in continuous and single-shot capture. Both gear records were automatically confirmed after matching OCR passes.
- All hidden windows were restored; no screenshot files were written.
- Traditional and simplified layouts rendered at 470x640; navigation, editor and saving with secondary metadata blank were exercised.
- Data schema and runtime dependency pins remain compatible with v0.1.0.
- Windows/Taobao client composition, foreground behavior and DPI have not been tested on a Windows host here. Other applications covering the game are not hidden by this utility.
- Game-side recycling success/identity still requires user confirmation before inventory deletion.
