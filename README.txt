Remastered Witcher 3 Dual Subtitles
===================================

This version does NOT use old w3strings.exe.
It includes its own .w3strings reader/writer with support for:
- classic containers (UTF-16 payload)
- remastered containers (version 164+, UTF-8 payload)

Files
-----
- dual_subtitles_remastered.py
- recap_subs_pipeline.py
- build_exe.bat

Requirements
------------
- Python 3.10+ (Tkinter included in standard Windows Python installs)
- Internet connection (first build only, to install PyInstaller)

Build EXE (Windows, one-click)
-------------------------------
Double-click:

    build_exe.bat

This generates:

    release\Witcher3DualSubtitlesRemastered.exe

and copies this README into the same release folder.

How to share
------------
- Send the EXE from the release folder.
- User can double-click the EXE and the UI opens directly.
- No Python install is needed on the target PC.

Usage
-----
UI mode:

    python dual_subtitles_remastered.py

UI actions:

- Apply Dual Subtitles: single button that applies dual subtitles for both
    .w3strings files and intro/recap subtitles in movies.bundle.
- Restore From Backup (Ctrl+Z): single undo button that restores both
    .w3strings backup files and recap bundle backup.
- Long operations run in the background and show a loading bar; the UI stays responsive while processing.
- Clear Log: clears output panel.

Batch mode:

    python dual_subtitles_remastered.py <source_lang> <target_lang> <witcher3_path>

Example:

    python dual_subtitles_remastered.py tr en "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"

Intro/Recap subtitle pipeline (movies.bundle)
---------------------------------------------
The recap subtitles shown during intro/loading are not part of .w3strings.
The UI already applies this automatically, but this script can be used separately
to inspect files, generate merged recap text, and optionally create a patched bundle copy.

Where the intro text actually comes from:

- movies\cutscenes\gamestart\recap_wip.usm (the intro cinematic) contains its own
    embedded subtitle stream (CRI Sofdec @SBT chunks) with 15 language channels:
    en, pl, de, it, fr, cz, es, zh, ru, cn, jp, kr, br, esmx, ar.
- When the game text language has an embedded channel, the game renders THAT text and
    ignores movies\cutscenes\gamestart\subs\recap_wip_<lang>.subs.
- Languages without a channel (tr, hu, ua) fall back to the .subs file.
- The tool therefore patches both: the .subs file (for fallback languages) and the
    embedded channel of the target language inside recap_wip.usm (rewritten chunks are
    re-padded to 32 bytes, the CRID file size is updated, and the SBT header limits are
    raised only if the merged text exceeds the original maximums).
- The rewritten movie is appended to the bundle and the TOC entry is repointed; nothing
    inside the original data is overwritten, so the backup/restore flow stays the same.

content\metadata.store:

- The game does not look up files through the bundle TOC at runtime; it uses
    content\metadata.store, which holds its own offset/size per file.
- When bundle sizes no longer match the store, the game rebuilds metadata.store on launch
    (observed: new file written a few seconds after start). The first start after
    applying or restoring may take slightly longer.
- If the intro ever shows stale text after a patch/restore, start the game once with the
    launch option -rebuild-store (witcher3.exe supports it) or verify files via Steam.

Generate extraction + merged files only:

    python recap_subs_pipeline.py --bundle "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3\content\content0\bundles\movies.bundle" --output-dir ".\tools\recap_build" --target-lang en --source-lang tr

Generate extraction + merged files + patched bundle copy:

    python recap_subs_pipeline.py --bundle "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3\content\content0\bundles\movies.bundle" --output-dir ".\tools\recap_build" --target-lang en --source-lang tr --patched-bundle ".\tools\recap_build\movies.patched.bundle"

Separator options:

- same-line (default): appends source with " | " delimiter.
- escaped-newline: appends source as literal "\\n" after target.
- actual-newline: appends source with real newline character.

Intro visibility note:

- In the main app flow the second language is written on the next line, source-first
    (Turkish, then English on the line below). The in-game .w3strings texts use the
    same line break: a real newline for plain subtitles, <br> for rows that already
    contain HTML.
- In recap_subs_pipeline.py CLI the default is still same-line (" | ").
    Use --separator-style actual-newline --source-first to match the app.
- The CLI also rewrites the embedded USM channel by default and writes
    <output-dir>\usm\recap_wip.patched.usm plus sbt_<target>_<source>.txt for inspection.
    Use --no-usm to skip that step.

Important:

- In UI mode, the game movies.bundle is patched in place after creating a local backup file.
- In CLI mode for recap_subs_pipeline.py, the original bundle is not modified unless
    you explicitly choose to write a patched output bundle.
- --patched-bundle writes a new copied bundle file.
- Keep your own backup and test on a copied game file/mod setup first.

How it works
------------
- target_lang file is the one that gets modified.
- source_lang text is appended to target_lang text.
- Before each merge, the script restores target_lang from backup:

    <target_lang>_backup.w3strings

- If source_lang == target_lang, the script restores defaults (no merge).

Notes
-----
- The script scans recursively and processes folders where both language files exist.
- Language codes are read from filenames (for example en.w3strings, tr.w3strings).
- Keep a backup of your game files/mod setup as a safety practice.
