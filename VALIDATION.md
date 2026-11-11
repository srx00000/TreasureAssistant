# Validation — v0.3.0

- 62 unit and regression tests passed (Python 3.12).
- Existing six-screen fixtures retained; added an upright warehouse-detail OCR fixture with text and geometry only.
- Actual RapidOCR runs on the six original screenshots and the new warehouse screenshot verified the upright pipeline. The warehouse returned four distinct attributes: all-faction soldier destruction 2.74%, cavalry attack 9.88%, all-faction soldier defense 2.91%, food production 9.99%. Scope-text confidence remained low, correctly requiring review.
- Row-pairing regressions cover a missing middle value, malformed inverted numeric text, and warehouse comparison-footer boundaries. Numeric-only content is not guessed into attributes; irrelevant unsigned icon digits are ignored.
- SQLite checks cover draft restart persistence, exact and uniquely compatible subset deduplication, conservative conflicting-value handling, no quantity increments on reread, preservation of manual edits, atomic draft promotion/rollback, mixed draft/item deletion, stat cascade, reset/clear behavior, deleted-item edit references, bounded raw text, and a v0.2-style database without the drafts table.
- Linux virtual-display UI integration exercised scan-to-draft, restart, four-stat review-to-inventory, manual mixed selection delete cancel/confirm, stale queued scan rejection, CLEAR cancel/wrong/correct confirmation, settings preserved by clear and removed by RESET, draft JSON export/import, and simplified UI. Main window remained 470x640.
- Capture integration deliberately covered the game with both the assistant and an owned dialog. Auto-docking and temporary-hide fallback returned pixel-exact images in continuous and single-shot capture, confirmed both gear records after repeated OCR, restored all windows, and wrote no screenshots.
- Runtime dependency pins are unchanged. The additive drafts table preserves previous inventory, settings and rules. Old misparsed records are not automatically rewritten.
- User-specific screenshots, QA database files and GUI captures are not distributed in the repository.
- Windows/Taobao client composition, foreground behavior, window restoration and DPI have not been verified on a Windows host here. Other apps covering the game are not hidden. Small or unusual glyphs can still be misread and require review.
- Game-side recycling success and item identity still require user confirmation; manual database deletion is a separate operation and never controls the game.

Run the repository tests with `python -m unittest discover -s tests -v`.
