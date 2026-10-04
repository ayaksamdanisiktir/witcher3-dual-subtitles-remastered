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

- Apply Dual Subtitles: single button that applies dual subtitles for
    .w3strings files, movie subtitles in movies.bundle/bob.bundle, and installs the
    mods\modDualSubtitles script mod (numbers in tooltips, see below).
- Restore From Backup (Ctrl+Z): single undo button that restores the
    .w3strings backup files and bundle backups and removes the script mod.
- Long operations run in the background and show a loading bar; the UI stays responsive while processing.
- Clear Log: clears output panel.

Batch mode:

    python dual_subtitles_remastered.py <source_lang> <target_lang> <witcher3_path>

Example:

    python dual_subtitles_remastered.py tr en "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"

Movie subtitle pipeline (movies.bundle, bob.bundle)
---------------------------------------------------
Subtitles of pre-rendered movies are not part of .w3strings. This covers:

- the intro cinematic (movies\cutscenes\gamestart\recap_wip.usm)
- story recaps shown when continuing a save (movies\cutscenes\storybook\st_*.usm)
- flashbacks (movies\cutscenes\flashbacks\rs_*.usm)
- final boards / endings (movies\cutscenes\finalboards\fb_*.usm)
- DLC cutscenes with subtitle files (dlc\bob\...\cs704_sister_lives_teleport.usm)

The UI applies all of this automatically. recap_subs_pipeline.py can be used separately
to inspect the intro files and optionally create a patched bundle copy.

Where the movie text actually comes from:

- Every movie has <movie>_<lang>.subs files next to it (subs\ and sometimes altsubs\
    for the alternative voice track). These are plain "start, end, text" lines.
- Many movies (intro, flashbacks, final boards, cs704) ALSO contain an embedded
    subtitle stream (CRI Sofdec @SBT chunks) with up to 15 language channels:
    en, pl, de, it, fr, cz, es, zh, ru, cn, jp, kr, br, esmx, ar.
- When the text language has an embedded channel, the game renders THAT text and
    ignores the .subs file. Languages without a channel (tr, hu, ua) and movies without
    embedded text (storybook recaps) use the .subs file.
- The tool therefore patches both: every .subs pair for the language combination and
    the embedded channel of the target language inside every movie that has one
    (rewritten chunks are re-padded to 32 bytes, the CRID file size is updated, and the
    SBT header limits are raised only if the merged text exceeds the original maximums).
- Rewritten movies and subtitle files are appended to their bundle and the TOC entries
    are repointed; nothing inside the original data is overwritten. Each touched bundle
    gets a <bundle>.dualsub_backup on first use (movies.bundle and bob.bundle).

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
- html-break: appends source after a "<br>" tag (only works where the text is
    rendered as HTML; the storybook recap field shows it literally).
- carriage-return: appends source after a lone CR character. The game's .subs parser
    splits records on LF only, so the CR stays inside the record, and the movie
    subtitle field renders it as a line break (verified in the storybook recaps).

Intro visibility note:

- In the main app flow the second language is written on the next line, source-first
    (Turkish, then English on the line below).
    * .subs files: a lone CR (carriage-return style, see above).
    * embedded movie text (SBT): a real newline character.
    * .w3strings: a real newline for plain text, <br> for rows that already contain
      HTML, and " / " on the same line for short labels (<= 25 chars, no sentence
      punctuation). Scripts append values right after such labels
      ("Required Level" + " " + level); with a line break the value would land on
      the second line, which single-line fields clip.
- In recap_subs_pipeline.py CLI the default is still same-line (" | ").
    Use --separator-style carriage-return --source-first to match the app's .subs output.
- The CLI also rewrites the embedded USM channel by default and writes
    <output-dir>\usm\recap_wip.patched.usm plus sbt_<target>_<source>.txt for inspection.
    Use --no-usm to skip that step.

Script mod for numbers in tooltips (mods\modDualSubtitles)
-----------------------------------------------------------
Skill/item/perk descriptions keep their numbers as placeholders in .w3strings:
"$I$" (integer), "$F$" (decimal) and "$S$" (text), e.g.
"Increases crossbow critical hit chance by $I$%." The values are filled at runtime by
GetLocStringByKeyExtWithParams / GetLocStringByIdWithParams in
content\content0\scripts\game\localizedContent.ws, one placeholder per parameter
(StrReplace replaces the first occurrence only).

A merged row contains each placeholder twice (once per language), so without help
the game fills the first language and the second one keeps showing raw "$I$".
The tool therefore writes a small script mod:

    mods\modDualSubtitles\content\scripts\game\localizedContent.ws

It is generated from the game's own localizedContent.ws at apply time: the two
functions get a second replacement pass that runs only when exactly one copy of each
placeholder is still left, plus a helper DualSub_CountOccurrences. Everything else in
the file is unchanged. The game recompiles scripts on the next launch (slightly longer
first start). Restore From Backup (or applying with source == target) deletes the mod.

If you use other mods that change localizedContent.ws, merge them with Script Merger.

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
