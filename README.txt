Remastered Witcher 3 Dual Subtitles
===================================

This version does NOT use old w3strings.exe.
It includes its own .w3strings reader/writer with support for:
- classic containers (UTF-16 payload)
- remastered containers (version 164+, UTF-8 payload)

Files
-----
- dual_subtitles_remastered.py
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

- Apply Merge: merges added translation language into modified language.
- Restore From Backup (Ctrl+Z): restores modified language from `*_backup.w3strings` files.
- Blind Undo Last Action (Ctrl+Shift+Z): restores files to the exact state before the last matching merge action
    (based on source language + target language + selected folder).
- Clear Log: clears output panel.

Batch mode:

    python dual_subtitles_remastered.py <source_lang> <target_lang> <witcher3_path>

Example:

    python dual_subtitles_remastered.py tr en "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"

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
