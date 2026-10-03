import os
import json
import shutil
import struct
import sys
from dataclasses import dataclass
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk

# File format constants
MAGIC_BYTES = b"RTSW"
FIRST_UTF8_VERSION = 164
UTF16LE_VERSION = 162

# Known language key -> magic map from game files.
# Languages with key 0 are stored without obfuscation.
KNOWN_LANGUAGE_MAGIC = {
    0x83496237: 0x73946816,  # pl
    0x43975139: 0x79321793,  # en
    0x75886138: 0x42791159,  # de
    0x45931894: 0x12375973,  # it
    0x23863176: 0x75921975,  # fr
    0x24987354: 0x21793217,  # cz
    0x18796651: 0x42387566,  # es
    0x18632176: 0x16875467,  # zh
    0x63481486: 0x42386347,  # ru
    0x42378932: 0x67823218,  # hu
    0x54834893: 0x59825646,  # jp
}

LANGUAGE_CHOICES = [
    "ar",
    "br",
    "cn",
    "cz",
    "de",
    "en",
    "enpc",
    "es",
    "esmx",
    "fr",
    "hu",
    "it",
    "jp",
    "kr",
    "pl",
    "plpc",
    "ru",
    "tr",
    "ua",
    "zh",
]


@dataclass
class W3StringEntry:
    str_id: int
    offset: int
    length: int
    text: str


@dataclass
class W3KeyEntry:
    key_hash: int
    str_id: int


@dataclass
class W3StringsFile:
    version: int
    key: int
    strings: list
    keys: list


class W3StringsError(Exception):
    pass


def _state_root_dir():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        # Keep mutable app state under LOCALAPPDATA so the game install stays untouched.
        return os.path.join(local_app_data, "Witcher3DualSubtitlesRemastered")
    return os.path.join(os.path.expanduser("~"), ".witcher3_dual_subtitles_remastered")


STATE_ROOT_DIR = _state_root_dir()
UNDO_HISTORY_DIR = os.path.join(STATE_ROOT_DIR, "undo_history")
UNDO_HISTORY_INDEX = os.path.join(UNDO_HISTORY_DIR, "history.json")


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _new_operation_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _load_operation_history():
    if not os.path.exists(UNDO_HISTORY_INDEX):
        return []

    try:
        with open(UNDO_HISTORY_INDEX, "r", encoding="utf8") as handle:
            data = json.load(handle)
    except Exception:
        return []

    if isinstance(data, list):
        return data

    # Backward compatibility: accept both old list-only and wrapped JSON payloads.
    if isinstance(data, dict) and isinstance(data.get("operations"), list):
        return data["operations"]

    return []


def _save_operation_history(operations):
    _ensure_dir(UNDO_HISTORY_DIR)
    payload = {"operations": operations}
    with open(UNDO_HISTORY_INDEX, "w", encoding="utf8") as handle:
        json.dump(payload, handle, indent=2)


def _record_merge_operation(source_lang, target_lang, location, snapshots, found, merged, failed):
    if not snapshots:
        return None

    operation = {
        "id": _new_operation_id(),
        "action": "merge",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_lang": source_lang.lower(),
        "target_lang": target_lang.lower(),
        "location": os.path.abspath(location),
        "found": found,
        "merged": merged,
        "failed": failed,
        "files": snapshots,
    }

    operations = _load_operation_history()
    operations.append(operation)

    # Keep last 100 operations to limit disk usage over time.
    if len(operations) > 100:
        to_remove = operations[:-100]
        operations = operations[-100:]
        for old in to_remove:
            old_id = old.get("id")
            if not old_id:
                continue
            old_dir = os.path.join(UNDO_HISTORY_DIR, old_id)
            shutil.rmtree(old_dir, ignore_errors=True)

    _save_operation_history(operations)
    return operation["id"]


def _normalize_path(path):
    return os.path.normcase(os.path.abspath(path))


def _find_latest_merge_operation(source_lang, target_lang, location):
    source_lang = source_lang.lower()
    target_lang = target_lang.lower()
    normalized_location = _normalize_path(location)

    operations = _load_operation_history()
    for operation in reversed(operations):
        if operation.get("action") != "merge":
            continue
        if operation.get("undone_at"):
            continue
        if operation.get("source_lang") != source_lang:
            continue
        if operation.get("target_lang") != target_lang:
            continue

        op_location = operation.get("location", "")
        if _normalize_path(op_location) != normalized_location:
            continue

        return operation

    return None


def blind_undo_operation(operation_id):
    operations = _load_operation_history()
    operation = None
    for item in operations:
        if item.get("id") == operation_id:
            operation = item
            break

    if operation is None:
        print("Blind undo could not find requested operation.")
        return 0, 0

    if operation.get("undone_at"):
        print("This operation is already marked as undone.")
        return 0, 0

    restored = 0
    failed = 0

    print(f"Blind undo operation id: {operation_id}")
    for file_info in operation.get("files", []):
        target_path = file_info.get("target_path")
        snapshot_path = file_info.get("snapshot_path")

        if not target_path or not snapshot_path:
            failed += 1
            print("FAILURE TO BLIND UNDO: missing metadata entry")
            continue

        if not os.path.exists(snapshot_path):
            failed += 1
            print(f"FAILURE TO BLIND UNDO {target_path}")
            print(f"Missing snapshot: {snapshot_path}")
            continue

        try:
            shutil.copy2(snapshot_path, target_path)
            restored += 1
            print(f"Blind restored: {target_path}")
        except Exception as exc:
            failed += 1
            print(f"FAILURE TO BLIND UNDO {target_path}")
            print(str(exc))

    if restored > 0 and failed == 0:
        operation["undone_at"] = datetime.now().isoformat(timespec="seconds")
        _save_operation_history(operations)

    print(f"Blind undo finished. restored: {restored}, failed: {failed}")
    return restored, failed


def _rotl16(value):
    return ((value << 1) | (value >> 15)) & 0xFFFF


def _magic_from_key(key):
    if key == 0:
        return 0
    magic = KNOWN_LANGUAGE_MAGIC.get(key)
    if magic is None:
        raise W3StringsError(
            f"Unknown non-zero language key 0x{key:08X}; cannot decode safely."
        )
    return magic


def _unit_size(version):
    return 1 if version >= FIRST_UTF8_VERSION else 2


def _read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise W3StringsError(f"Unexpected end of file while reading {size} bytes.")
    return data


def _read_count(stream):
    # The w3strings format uses a custom variable-length integer ("bit6") encoding,
    # not standard varint/LEB128; decode it exactly to avoid offset corruption.
    raw = 0
    shift = 0
    index = 1

    while True:
        data = stream.read(1)
        if not data:
            raise W3StringsError("Invalid bit6 count: stream ended inside the count.")
        x = data[0]

        if x > 127:
            mask = 0x7F
            step = 7
        elif x > 63 and index == 1:
            mask = 0x3F
            step = 6
        else:
            mask = 0xFF
            step = 6

        raw |= (x & mask) << shift
        shift += step

        # Terminator rules are format-specific and depend on byte index.
        if x < 64 or (index >= 3 and x < 128):
            break

        index += 1
        if index > 6:
            raise W3StringsError("Invalid bit6 count: too many bytes.")

    if raw > 0xFFFFFFFF:
        raise W3StringsError("Invalid bit6 count: exceeds 32-bit unsigned integer.")

    return raw


def _try_read_count(data, offset):
    raw = 0
    shift = 0
    index = 1
    pos = offset

    while True:
        if pos >= len(data):
            return False, 0, offset

        x = data[pos]
        pos += 1

        if x > 127:
            mask = 0x7F
            step = 7
        elif x > 63 and index == 1:
            mask = 0x3F
            step = 6
        else:
            mask = 0xFF
            step = 6

        raw |= (x & mask) << shift
        shift += step

        if x < 64 or (index >= 3 and x < 128):
            break

        index += 1

    if raw > 0xFFFFFFFF:
        return False, 0, offset

    return True, raw, pos


def _build_count(value, group_count, terminator_bits):
    if group_count == 1:
        if value < 64:
            return bytes([value])
        return None

    total_bits = 6 + 7 * (group_count - 2) + terminator_bits
    if value >> total_bits != 0:
        return None

    chunks = [0] * group_count
    v = value

    chunks[0] = v & 0x3F
    v >>= 6

    for i in range(1, group_count - 1):
        chunks[i] = v & 0x7F
        v >>= 7

    chunks[-1] = v & ((1 << terminator_bits) - 1)
    v >>= terminator_bits

    if v != 0:
        return None

    chunks[0] |= 0x40
    for i in range(1, group_count - 1):
        chunks[i] |= 0x80

    if chunks[-1] & 0x40:
        return None

    return bytes(chunks)


def _write_count(value):
    if value < 0 or value > 0xFFFFFFFF:
        raise W3StringsError(f"Count out of range for bit6 encoding: {value}")

    # Generate candidates and verify by decoding back to guarantee round-trip safety.
    for group_count in range(1, 7):
        for terminator_bits in (6, 7, 8):
            candidate = _build_count(value, group_count, terminator_bits)
            if candidate is None:
                continue

            ok, decoded, end = _try_read_count(candidate, 0)
            if ok and decoded == value and end == len(candidate):
                return candidate

    raise W3StringsError(f"Failed to encode bit6 count: {value}")


def _decode_stored_text(stored, length, magic, unit):
    if len(stored) != length * unit:
        raise W3StringsError(
            f"Stored text length mismatch: expected {length * unit}, got {len(stored)}"
        )

    buf = bytearray(stored)
    key = (magic >> 8) & 0xFFFF

    for i in range(length):
        char_key = ((length + 1) * key) & 0xFFFFFFFF
        if unit == 2:
            j = i * 2
            buf[j] ^= char_key & 0xFF
            buf[j + 1] ^= (char_key >> 8) & 0xFF
        else:
            buf[i] ^= char_key & 0xFF
        key = _rotl16(key)

    if unit == 2:
        return bytes(buf).decode("utf-16-le")
    return bytes(buf).decode("utf-8")


def _encode_stored_text(text, magic, unit):
    if unit == 2:
        raw = text.encode("utf-16-le")
    else:
        raw = text.encode("utf-8")

    length = len(raw) // unit
    buf = bytearray(raw)
    key = (magic >> 8) & 0xFFFF

    for i in range(length):
        char_key = ((length + 1) * key) & 0xFFFFFFFF
        if unit == 2:
            j = i * 2
            buf[j] ^= char_key & 0xFF
            buf[j + 1] ^= (char_key >> 8) & 0xFF
        else:
            buf[i] ^= char_key & 0xFF
        key = _rotl16(key)

    return bytes(buf), length


def read_w3strings(path):
    file_size = os.path.getsize(path)
    if file_size < 15:
        raise W3StringsError(f"File too small to be a w3strings file: {path}")

    with open(path, "rb") as f:
        if _read_exact(f, 4) != MAGIC_BYTES:
            raise W3StringsError(f"Invalid magic in file: {path}")

        version = struct.unpack("<I", _read_exact(f, 4))[0]
        key1 = struct.unpack("<H", _read_exact(f, 2))[0]

        unit = _unit_size(version)

        f.seek(file_size - 2)
        key2 = struct.unpack("<H", _read_exact(f, 2))[0]
        key = (key1 << 16) | key2
        magic = _magic_from_key(key)

        f.seek(10)

        block1_count = _read_count(f)
        strings = []
        for _ in range(block1_count):
            raw = _read_exact(f, 12)
            str_id_hashed, offset, length = struct.unpack("<III", raw)
            strings.append(
                W3StringEntry(
                    str_id=str_id_hashed ^ magic,
                    offset=offset,
                    length=length,
                    text="",
                )
            )

        block2_count = _read_count(f)
        keys = []
        for _ in range(block2_count):
            raw = _read_exact(f, 8)
            key_hash, str_id_hashed = struct.unpack("<II", raw)
            keys.append(W3KeyEntry(key_hash=key_hash, str_id=str_id_hashed ^ magic))

        buffer_units = _read_count(f)
        buffer_start = f.tell()
        key2_offset = file_size - 2
        buffer_end = buffer_start + buffer_units * unit
        if buffer_end > key2_offset:
            raise W3StringsError(
                f"String buffer overruns file: end={buffer_end}, key2={key2_offset}"
            )

        # Read by physical buffer order to avoid seek collisions when offsets are sparse.
        for entry in sorted(strings, key=lambda item: item.offset):
            if entry.offset + entry.length > buffer_units:
                raise W3StringsError(
                    "String entry points outside string buffer: "
                    f"id={entry.str_id}, offset={entry.offset}, length={entry.length}, buffer={buffer_units}"
                )

            text_pos = buffer_start + entry.offset * unit
            f.seek(text_pos)
            stored = _read_exact(f, entry.length * unit)
            entry.text = _decode_stored_text(stored, entry.length, magic, unit)

    return W3StringsFile(version=version, key=key, strings=strings, keys=keys)


def write_w3strings(path, data):
    version = data.version
    key = data.key
    key1 = (key >> 16) & 0xFFFF
    key2 = key & 0xFFFF
    unit = _unit_size(version)
    magic = _magic_from_key(key)

    if len({entry.str_id for entry in data.strings}) != len(data.strings):
        raise W3StringsError("Duplicate string IDs found; cannot write file safely.")

    buffer_parts = []
    cursor_units = 0
    # Preserve original offset order to keep writer output stable and deterministic.
    offset_ordered_entries = sorted(data.strings, key=lambda item: item.offset)

    for entry in offset_ordered_entries:
        stored, length = _encode_stored_text(entry.text, magic, unit)
        entry.offset = cursor_units
        entry.length = length
        buffer_parts.append(stored)
        # Each string in the shared buffer is null-terminated in unit width (UTF-8/UTF-16LE).
        cursor_units += length + 1

    with open(path, "wb") as f:
        f.write(MAGIC_BYTES)
        f.write(struct.pack("<I", version))
        f.write(struct.pack("<H", key1))

        f.write(_write_count(len(data.strings)))
        for entry in data.strings:
            f.write(struct.pack("<I", entry.str_id ^ magic))
            f.write(struct.pack("<I", entry.offset))
            f.write(struct.pack("<I", entry.length))

        f.write(_write_count(len(data.keys)))
        for key_entry in data.keys:
            f.write(struct.pack("<I", key_entry.key_hash))
            f.write(struct.pack("<I", key_entry.str_id ^ magic))

        f.write(_write_count(cursor_units))

        for stored in buffer_parts:
            f.write(stored)
            f.write(b"\x00" * unit)

        f.write(struct.pack("<H", key2))


def merge_entries(source_file, target_file):
    source_map = {entry.str_id: entry.text for entry in source_file.strings}
    changed = 0

    for entry in target_file.strings:
        source_text = source_map.get(entry.str_id)
        if source_text is None:
            continue

        source_text = source_text.strip()
        target_text = entry.text.strip()

        # Skip placeholder or control-token rows to avoid breaking UI/menu labels.
        if not source_text or source_text.startswith("#"):
            continue
        if target_text.startswith("#"):
            continue
        if source_text in target_text:
            continue

        # Preserve readability: long lines use HTML line-break, short lines stay inline.
        delimiter = "<br>~ " if len(entry.text) > 20 else " ~ "
        entry.text = f"{entry.text}{delimiter}{source_text}"
        changed += 1

    return changed


def _resolve_case_insensitive_file(directory, file_name):
    direct_path = os.path.join(directory, file_name)
    if os.path.exists(direct_path):
        return direct_path

    lower_target = file_name.lower()
    try:
        for name in os.listdir(directory):
            if name.lower() == lower_target:
                return os.path.join(directory, name)
    except FileNotFoundError:
        return direct_path

    return direct_path


def process_files(source_lang, target_lang, location):
    source_lang = source_lang.lower()
    target_lang = target_lang.lower()
    should_merge = source_lang != target_lang
    found = 0
    merged = 0
    failed = 0
    snapshots = []

    operation_id = _new_operation_id()
    operation_dir = os.path.join(UNDO_HISTORY_DIR, operation_id)
    operation_storage_ready = False

    if should_merge:
        try:
            _ensure_dir(operation_dir)
            operation_storage_ready = True
        except Exception as exc:
            print("WARNING - blind undo snapshots are disabled for this run.")
            print(str(exc))

    print(f"Starting {source_lang} {target_lang} {location}")

    for root, _, _ in os.walk(location):
        source_path = _resolve_case_insensitive_file(root, f"{source_lang}.w3strings")
        target_path = _resolve_case_insensitive_file(root, f"{target_lang}.w3strings")

        if not (os.path.exists(source_path) and os.path.exists(target_path)):
            continue

        found += 1
        print(f"Processing: {target_path}")

        if should_merge and operation_storage_ready:
            snapshot_name = f"{len(snapshots) + 1:05d}.w3strings"
            snapshot_path = os.path.join(operation_dir, snapshot_name)
            try:
                shutil.copy2(target_path, snapshot_path)
                snapshots.append(
                    {
                        "target_path": target_path,
                        "snapshot_path": snapshot_path,
                    }
                )
            except Exception as exc:
                print(f"WARNING - failed to create blind undo snapshot for: {target_path}")
                print(str(exc))

        base_name, _ = os.path.splitext(target_path)
        backup_path = f"{base_name}_backup.w3strings"

        if not os.path.exists(backup_path):
            shutil.copy2(target_path, backup_path)
            print(f"Backup created: {backup_path}")

        # Always start from clean backup before applying merge so repeated runs are idempotent.
        shutil.copy2(backup_path, target_path)

        if not should_merge:
            # Selecting the same language acts as a quick restore command.
            print("Restored from backup (source and target languages are the same).")
            continue

        try:
            source_data = read_w3strings(source_path)
            target_data = read_w3strings(target_path)

            changed = merge_entries(source_data, target_data)
            write_w3strings(target_path, target_data)

            merged += 1
            print(f"Merged {changed} strings in: {target_path}")
        except Exception as exc:
            failed += 1
            print(f"FAILURE TO PROCESS {target_path}")
            print(str(exc))

    if found == 0:
        print("No matching files found for the specified source and target languages.")
    else:
        print(f"Finished. Matched folders: {found}, merged: {merged}, failed: {failed}")

    if should_merge and snapshots:
        history_id = _record_merge_operation(
            source_lang=source_lang,
            target_lang=target_lang,
            location=location,
            snapshots=snapshots,
            found=found,
            merged=merged,
            failed=failed,
        )
        if history_id:
            print(f"Blind undo record id: {history_id}")
    elif should_merge and operation_storage_ready:
        # Nothing was captured; drop the empty operation folder to avoid stale entries.
        shutil.rmtree(operation_dir, ignore_errors=True)


def restore_from_backups(target_lang, location):
    target_lang = target_lang.lower()
    backup_suffix = "_backup.w3strings"
    expected_backup = f"{target_lang}{backup_suffix}"

    restored = 0
    failed = 0

    print(f"Undo started for language: {target_lang}")
    print(f"Searching backups in: {location}")

    for root, _, files in os.walk(location):
        for file_name in files:
            if file_name.lower() != expected_backup:
                continue

            backup_path = os.path.join(root, file_name)
            target_name = file_name[: -len(backup_suffix)] + ".w3strings"
            target_path = os.path.join(root, target_name)

            try:
                shutil.copy2(backup_path, target_path)
                restored += 1
                print(f"Restored: {target_path}")
            except Exception as exc:
                failed += 1
                print(f"FAILURE TO RESTORE {target_path}")
                print(str(exc))

    if restored == 0 and failed == 0:
        print(
            "No backup files found for selected language. "
            "Run merge at least once before undo."
        )
    else:
        print(f"Undo finished. restored: {restored}, failed: {failed}")

    return restored, failed


def _configure_styles(window):
    style = ttk.Style(window)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    # Tk expects multi-word font families wrapped in braces in option strings.
    window.option_add("*Font", "{Segoe UI} 10")

    palette = {
        "app_bg": "#edf2f7",
        "panel_bg": "#ffffff",
        "title": "#0f172a",
        "muted": "#64748b",
        "accent": "#0ea5e9",
        "accent_hover": "#0284c7",
        "neutral": "#f8fafc",
        "neutral_hover": "#f1f5f9",
        "border": "#dbe3ee",
        "log_bg": "#f8fafc",
        "log_fg": "#111827",
    }

    window.configure(bg=palette["app_bg"])

    style.configure("Root.TFrame", background=palette["app_bg"])
    style.configure("Card.TFrame", background=palette["panel_bg"])
    style.configure(
        "Title.TLabel",
        background=palette["panel_bg"],
        foreground=palette["title"],
        font=("Segoe UI Semibold", 17),
    )
    style.configure(
        "SubTitle.TLabel",
        background=palette["panel_bg"],
        foreground=palette["muted"],
        font=("Segoe UI", 10),
    )
    style.configure(
        "Field.TLabel",
        background=palette["panel_bg"],
        foreground=palette["title"],
        font=("Segoe UI Semibold", 10),
    )
    style.configure(
        "Status.TLabel",
        background=palette["panel_bg"],
        foreground=palette["muted"],
        font=("Segoe UI", 9),
    )

    style.configure("TCombobox", padding=6)
    style.configure("TEntry", padding=6)

    style.configure(
        "Accent.TButton",
        padding=(14, 8),
        background=palette["accent"],
        foreground="#ffffff",
        bordercolor=palette["accent"],
    )
    style.map(
        "Accent.TButton",
        background=[("active", palette["accent_hover"]), ("pressed", palette["accent_hover"])],
        foreground=[("disabled", "#dbeafe"), ("!disabled", "#ffffff")],
    )

    style.configure(
        "Neutral.TButton",
        padding=(14, 8),
        background=palette["neutral"],
        foreground=palette["title"],
        bordercolor=palette["border"],
    )
    style.map(
        "Neutral.TButton",
        background=[("active", palette["neutral_hover"]), ("pressed", palette["neutral_hover"])],
    )

    return palette


class _TextRedirector:
    def __init__(self, text_widget, window):
        self.text_widget = text_widget
        self.window = window

    def write(self, text):
        if not text:
            return

        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, text)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")
        self.window.update_idletasks()

    def flush(self):
        pass


def redirect_output(text_widget, window):
    stream = _TextRedirector(text_widget, window)
    sys.stdout = stream
    sys.stderr = stream


def open_ui_dialog():
    window = tk.Tk()
    window.title("Witcher 3 Dual Subtitles - Remastered")
    window.minsize(840, 600)

    palette = _configure_styles(window)

    root_frame = ttk.Frame(window, style="Root.TFrame", padding=16)
    root_frame.pack(fill="both", expand=True)

    card = ttk.Frame(root_frame, style="Card.TFrame", padding=18)
    card.pack(fill="both", expand=True)

    ttk.Label(card, text="Witcher 3 Dual Subtitles", style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        card,
        text="Remastered compatible subtitle merge tool",
        style="SubTitle.TLabel",
    ).pack(anchor="w", pady=(2, 12))

    form = ttk.Frame(card, style="Card.TFrame")
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    ttk.Label(form, text="Modified language (target, will be edited)", style="Field.TLabel").grid(
        row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8)
    )
    target_language_combo = ttk.Combobox(form, values=LANGUAGE_CHOICES, state="readonly")
    target_language_combo.set("en")
    target_language_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))

    ttk.Label(form, text="Added translation language (source)", style="Field.TLabel").grid(
        row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 8)
    )
    source_language_combo = ttk.Combobox(form, values=LANGUAGE_CHOICES, state="readonly")
    source_language_combo.set("tr")
    source_language_combo.grid(row=1, column=1, sticky="ew", pady=(0, 8))

    ttk.Label(form, text="Game folder", style="Field.TLabel").grid(
        row=2, column=0, sticky="w", padx=(0, 10)
    )

    folder_row = ttk.Frame(form, style="Card.TFrame")
    folder_row.grid(row=2, column=1, sticky="ew")
    folder_row.columnconfigure(0, weight=1)

    folder_var = tk.StringVar(
        value=r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"
    )
    folder_entry = ttk.Entry(folder_row, textvariable=folder_var)
    folder_entry.grid(row=0, column=0, sticky="ew")

    def select_folder():
        folder_path = filedialog.askdirectory(initialdir=folder_var.get())
        if folder_path:
            folder_var.set(folder_path)

    browse_button = ttk.Button(
        folder_row,
        text="Browse...",
        style="Neutral.TButton",
        command=select_folder,
    )
    browse_button.grid(row=0, column=1, padx=(8, 0))

    button_row = ttk.Frame(card, style="Card.TFrame")
    button_row.pack(fill="x", pady=(14, 10))

    status_var = tk.StringVar(value="Ready.")

    def clear_log():
        output_text.configure(state="normal")
        output_text.delete("1.0", tk.END)
        output_text.configure(state="disabled")

    def set_busy(is_busy):
        state = "disabled" if is_busy else "normal"
        combo_state = "disabled" if is_busy else "readonly"

        run_button.configure(state=state)
        undo_button.configure(state=state)
        blind_undo_button.configure(state=state)
        clear_button.configure(state=state)
        browse_button.configure(state=state)
        source_language_combo.configure(state=combo_state)
        target_language_combo.configure(state=combo_state)
        folder_entry.configure(state=state)

        window.configure(cursor="watch" if is_busy else "")
        window.update_idletasks()

    def run_process(action="merge"):
        target_lang = target_language_combo.get().strip().lower()
        source_lang = source_language_combo.get().strip().lower()
        selected_folder = folder_var.get().strip()
        selected_operation = None

        if not selected_folder:
            messagebox.showwarning("Missing folder", "Please select your Witcher 3 folder.")
            return

        if not os.path.isdir(selected_folder):
            messagebox.showerror(
                "Invalid folder",
                "Selected folder does not exist. Please choose a valid Witcher 3 folder.",
            )
            return

        if action == "blind_undo":
            selected_operation = _find_latest_merge_operation(
                source_lang=source_lang,
                target_lang=target_lang,
                location=selected_folder,
            )

            if selected_operation is None:
                status_var.set("No matching blind undo record found.")
                messagebox.showinfo(
                    "No blind undo record",
                    "No matching merge action was found for the selected source/target languages and folder."
                    "\n\nRun a merge first, then try Blind Undo.",
                )
                return

            summary = (
                f"Operation: {selected_operation.get('source_lang')} -> "
                f"{selected_operation.get('target_lang')}\n"
                f"Date: {selected_operation.get('created_at')}\n"
                f"Files: {len(selected_operation.get('files', []))}\n\n"
                "Continue with blind undo?"
            )
            if not messagebox.askyesno("Confirm Blind Undo", summary):
                status_var.set("Blind undo canceled.")
                return

        if action == "merge":
            summary = (
                f"Target language (will be modified): {target_lang}\n"
                f"Source language (will be appended): {source_lang}\n"
                f"Folder: {selected_folder}\n\n"
                "Continue with merge?"
            )
            if not messagebox.askyesno("Confirm Merge", summary):
                status_var.set("Merge canceled.")
                return

        if action == "undo_backup":
            action_name = "Restore"
        elif action == "blind_undo":
            action_name = "Blind Undo"
        else:
            action_name = "Merge"

        status_var.set(f"{action_name} in progress...")
        set_busy(True)

        try:
            print("\n" + "=" * 70)
            if action == "undo_backup":
                print(f"Undo requested for language: {target_lang}")
                print(f"Selected folder: {selected_folder}")
                restored, failed = restore_from_backups(target_lang, selected_folder)
                if restored > 0 and failed == 0:
                    status_var.set("Undo completed.")
                elif restored > 0:
                    status_var.set("Undo completed with some errors. Check log.")
                else:
                    status_var.set("No backups found to undo.")
            elif action == "blind_undo":
                operation_id = selected_operation["id"]
                print(f"Blind undo requested for operation id: {operation_id}")
                restored, failed = blind_undo_operation(operation_id)
                if restored > 0 and failed == 0:
                    status_var.set("Blind undo completed.")
                elif restored > 0:
                    status_var.set("Blind undo completed with some errors. Check log.")
                else:
                    status_var.set("Blind undo failed. Check log.")
            else:
                print(f"Merge started: {source_lang} -> {target_lang}")
                print(f"Selected folder: {selected_folder}")
                process_files(source_lang, target_lang, selected_folder)
                status_var.set("Merge completed successfully.")
            print("--- DONE !       ---")
            print("--- You can exit ---")
            print("To restore defaults, use the same source and base languages.")
        except Exception as exc:
            status_var.set("Completed with errors. Check the log.")
            print(f"Unexpected error: {exc}")
            messagebox.showerror("Processing failed", str(exc))
        finally:
            set_busy(False)

    run_button = ttk.Button(
        button_row,
        text="Apply Merge",
        style="Accent.TButton",
        command=lambda: run_process(action="merge"),
    )
    run_button.pack(side="left")

    undo_button = ttk.Button(
        button_row,
        text="Restore From Backup (Ctrl+Z)",
        style="Neutral.TButton",
        command=lambda: run_process(action="undo_backup"),
    )
    undo_button.pack(side="left", padx=(8, 0))

    blind_undo_button = ttk.Button(
        button_row,
        text="Blind Undo Last Action (Ctrl+Shift+Z)",
        style="Neutral.TButton",
        command=lambda: run_process(action="blind_undo"),
    )
    blind_undo_button.pack(side="left", padx=(8, 0))

    clear_button = ttk.Button(
        button_row,
        text="Clear Log",
        style="Neutral.TButton",
        command=clear_log,
    )
    clear_button.pack(side="left", padx=(8, 0))

    ttk.Label(card, textvariable=status_var, style="Status.TLabel").pack(anchor="w", pady=(0, 8))

    output_text = scrolledtext.ScrolledText(
        card,
        wrap=tk.WORD,
        height=16,
        bg=palette["log_bg"],
        fg=palette["log_fg"],
        insertbackground=palette["log_fg"],
        relief="solid",
        borderwidth=1,
        font=("Consolas", 10),
        padx=10,
        pady=10,
    )
    output_text.pack(fill="both", expand=True)
    output_text.configure(state="disabled")

    redirect_output(output_text, window)
    print("UI ready.")
    print(
        "Tip: Ctrl+Z backup restore, Ctrl+Shift+Z blind undo history."
    )

    def on_backup_undo_shortcut(_event=None):
        run_process(action="undo_backup")
        return "break"

    def on_blind_undo_shortcut(_event=None):
        run_process(action="blind_undo")
        return "break"

    window.bind_all("<Control-z>", on_backup_undo_shortcut)
    window.bind_all("<Control-Z>", on_backup_undo_shortcut)
    window.bind_all("<Control-Shift-z>", on_blind_undo_shortcut)
    window.bind_all("<Control-Shift-Z>", on_blind_undo_shortcut)

    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    window_width = 900
    window_height = 670
    x_pos = (screen_width - window_width) // 2
    y_pos = (screen_height - window_height) // 2
    window.geometry(f"{window_width}x{window_height}+{x_pos}+{y_pos}")

    window.mainloop()


def main():
    if len(sys.argv) < 4:
        print("Running UI mode.")
        print(
            "Usage: python dual_subtitles_remastered.py <source_lang> <target_lang> <location>"
        )
        open_ui_dialog()
        return

    print("Running batch mode.")
    print("To restore defaults, use the same source and base languages.")

    source_lang = sys.argv[1]
    target_lang = sys.argv[2]
    location = sys.argv[3]

    process_files(source_lang, target_lang, location)


if __name__ == "__main__":
    main()
