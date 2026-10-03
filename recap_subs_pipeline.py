import argparse
import json
import os
import re
import shutil
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


BUNDLE_MAGIC = b"POTATO70"
TOC_START = 0x20
TOC_ENTRY_SIZE = 304
TOC_ENTRY_NAME_SIZE = 256
TOC_ENTRY_HASH_SIZE = 16
TOC_ENTRY_TAIL_OFFSET = TOC_ENTRY_NAME_SIZE + TOC_ENTRY_HASH_SIZE
TOC_ENTRY_TAIL_FMT = "<8I"
RECAP_SUBS_PREFIX = r"movies\cutscenes\gamestart\subs\recap_wip_"

RECORD_LINE_RE = re.compile(r"^(\d+),\s*(\d+),\s*(.*)$")


@dataclass
class BundleEntry:
    name: str
    hash_bytes: bytes
    offset_low: int
    offset_high: int
    size: int
    zsize: int
    crc: int
    compression_flag: int
    unk7: int
    unk8: int
    toc_offset: int

    @property
    def offset(self):
        return (self.offset_high << 32) | self.offset_low


@dataclass
class SubtitleRecord:
    start_ms: int
    end_ms: int
    text: str


def _read_exact(handle, size):
    chunk = handle.read(size)
    if len(chunk) != size:
        raise ValueError(f"Unexpected end of file while reading {size} bytes.")
    return chunk


def _align_up(value, alignment):
    if alignment <= 1:
        return value
    remainder = value % alignment
    if remainder == 0:
        return value
    return value + (alignment - remainder)


def load_bundle_entries(bundle_path):
    entries = []
    with open(bundle_path, "rb") as handle:
        magic = _read_exact(handle, 8)
        if magic != BUNDLE_MAGIC:
            raise ValueError(f"Unsupported bundle magic in {bundle_path}: {magic!r}")

        bundle_size_low = struct.unpack("<I", _read_exact(handle, 4))[0]
        dummy = struct.unpack("<I", _read_exact(handle, 4))[0]
        data_offset = struct.unpack("<I", _read_exact(handle, 4))[0]
        _read_exact(handle, TOC_START - 20)

        if data_offset % TOC_ENTRY_SIZE != 0:
            raise ValueError(
                "TOC size is not divisible by expected entry size. "
                f"data_offset={data_offset}, entry_size={TOC_ENTRY_SIZE}"
            )

        entry_count = data_offset // TOC_ENTRY_SIZE

        for index in range(entry_count):
            toc_offset = TOC_START + index * TOC_ENTRY_SIZE
            blob = _read_exact(handle, TOC_ENTRY_SIZE)

            raw_name = blob[:TOC_ENTRY_NAME_SIZE]
            name = raw_name.split(b"\x00", 1)[0].decode("iso-8859-1", "ignore")

            hash_bytes = blob[TOC_ENTRY_NAME_SIZE : TOC_ENTRY_NAME_SIZE + TOC_ENTRY_HASH_SIZE]
            tail = struct.unpack(
                TOC_ENTRY_TAIL_FMT,
                blob[TOC_ENTRY_TAIL_OFFSET : TOC_ENTRY_TAIL_OFFSET + struct.calcsize(TOC_ENTRY_TAIL_FMT)],
            )
            offset_low, offset_high, size, zsize, crc, compression_flag, unk7, unk8 = tail

            entries.append(
                BundleEntry(
                    name=name,
                    hash_bytes=hash_bytes,
                    offset_low=offset_low,
                    offset_high=offset_high,
                    size=size,
                    zsize=zsize,
                    crc=crc,
                    compression_flag=compression_flag,
                    unk7=unk7,
                    unk8=unk8,
                    toc_offset=toc_offset,
                )
            )

    header = {
        "bundle_size_low": bundle_size_low,
        "dummy": dummy,
        "data_offset": data_offset,
        "entry_count": entry_count,
    }
    return header, entries


def read_entry_payload(bundle_path, entry):
    with open(bundle_path, "rb") as handle:
        handle.seek(entry.offset)
        payload = _read_exact(handle, entry.zsize)
    return payload


def decode_subs_payload(entry, payload):
    if entry.compression_flag == 0:
        raw = payload
    elif entry.compression_flag == 1:
        raw = zlib.decompress(payload)
    else:
        raise ValueError(
            f"Unsupported compression flag {entry.compression_flag} for entry {entry.name}"
        )

    if len(raw) != entry.size:
        raise ValueError(
            "Decoded size mismatch for entry "
            f"{entry.name}: expected {entry.size}, got {len(raw)}"
        )

    return raw


def decode_subs_text(raw):
    # Files include UTF-16 text (BOM present in observed recap subtitle files).
    try:
        return raw.decode("utf-16")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le")


def parse_subtitle_records(text):
    header_value = None
    records = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if header_value is None and stripped.isdigit():
            header_value = int(stripped)
            continue

        match = RECORD_LINE_RE.match(stripped)
        if not match:
            continue

        start_ms = int(match.group(1))
        end_ms = int(match.group(2))
        line_text = match.group(3)
        records.append(SubtitleRecord(start_ms=start_ms, end_ms=end_ms, text=line_text))

    if header_value is None:
        header_value = 1000

    if not records:
        raise ValueError("No subtitle records were parsed from decoded subtitle text.")

    return header_value, records


def render_subtitle_text(header_value, records):
    lines = [str(header_value), ""]
    for record in records:
        lines.append(f"{record.start_ms}, {record.end_ms}, {record.text}")
        lines.append("")
    return "\n".join(lines) + "\n"


def merge_records(target_records, source_records, separator_style, source_first=False):
    if len(target_records) != len(source_records):
        raise ValueError(
            "Record count mismatch between target and source subtitle files: "
            f"{len(target_records)} vs {len(source_records)}"
        )

    if separator_style == "escaped-newline":
        separator = r"\n"
    elif separator_style == "same-line":
        separator = " | "
    elif separator_style == "actual-newline":
        separator = "\n"
    else:
        raise ValueError(f"Unknown separator style: {separator_style}")

    merged = []
    for index, (target_rec, source_rec) in enumerate(zip(target_records, source_records), start=1):
        if target_rec.start_ms != source_rec.start_ms or target_rec.end_ms != source_rec.end_ms:
            raise ValueError(
                "Timing mismatch at record index "
                f"{index}: target=({target_rec.start_ms}, {target_rec.end_ms}) "
                f"source=({source_rec.start_ms}, {source_rec.end_ms})"
            )

        source_text = source_rec.text.strip()
        target_text = target_rec.text.strip()

        if source_text and source_text not in target_text:
            if source_first:
                merged_text = f"{source_rec.text}{separator}{target_rec.text}"
            else:
                merged_text = f"{target_rec.text}{separator}{source_rec.text}"
        else:
            merged_text = target_rec.text

        merged.append(
            SubtitleRecord(
                start_ms=target_rec.start_ms,
                end_ms=target_rec.end_ms,
                text=merged_text,
            )
        )

    return merged


def find_recap_entry(entries, language_code):
    language_code = language_code.lower()
    expected = f"{RECAP_SUBS_PREFIX}{language_code}.subs"
    for entry in entries:
        if entry.name.lower() == expected.lower():
            return entry
    return None


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def write_utf8_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def patch_bundle_copy(bundle_path, output_bundle_path, entry_to_patch, merged_raw, align=16):
    source_abs = os.path.abspath(bundle_path)
    target_abs = os.path.abspath(output_bundle_path)
    if source_abs == target_abs:
        raise ValueError(
            "Refusing to patch in place. Use a different --patched-bundle path to avoid "
            "accidentally modifying the original game file."
        )

    ensure_dir(os.path.dirname(target_abs) or ".")
    shutil.copy2(source_abs, target_abs)

    return patch_bundle_entry_in_place(
        target_abs, entry_to_patch, merged_raw, compress=True, align=align
    )


def patch_bundle_entry_in_place(bundle_path, entry_to_patch, raw, compress=True, align=16):
    """Append new content for an existing TOC entry and repoint the entry to it.

    The old payload stays in the file (unreferenced); the TOC tail and the 64-bit
    bundle size in the header are updated. The game rebuilds content\\metadata.store
    on next launch when bundle sizes no longer match the store.
    """
    target_abs = os.path.abspath(bundle_path)
    if compress:
        payload = zlib.compress(raw, level=9)
        compression_flag = 1
    else:
        payload = raw
        compression_flag = 0
    crc = zlib.crc32(raw) & 0xFFFFFFFF

    with open(target_abs, "r+b") as handle:
        handle.seek(0, os.SEEK_END)
        old_end = handle.tell()
        new_offset = _align_up(old_end, align)

        if new_offset > old_end:
            handle.write(b"\x00" * (new_offset - old_end))

        handle.seek(new_offset)
        handle.write(payload)

        low = new_offset & 0xFFFFFFFF
        high = (new_offset >> 32) & 0xFFFFFFFF
        new_tail = struct.pack(
            TOC_ENTRY_TAIL_FMT,
            low,
            high,
            len(raw),
            len(payload),
            crc,
            compression_flag,
            entry_to_patch.unk7,
            entry_to_patch.unk8,
        )

        handle.seek(entry_to_patch.toc_offset + TOC_ENTRY_TAIL_OFFSET)
        handle.write(new_tail)

        handle.seek(0, os.SEEK_END)
        new_size = handle.tell()
        # Header stores the bundle size as two 32-bit halves (low at 0x08, high at 0x0C).
        handle.seek(8)
        handle.write(struct.pack("<II", new_size & 0xFFFFFFFF, (new_size >> 32) & 0xFFFFFFFF))

    return {
        "patched_entry": entry_to_patch.name,
        "new_offset": new_offset,
        "new_size": len(raw),
        "new_zsize": len(payload),
        "new_crc": crc,
        "compression_flag": compression_flag,
        "output_bundle": target_abs,
    }


def patch_bundle_in_place(bundle_path, entry_to_patch, merged_raw, align=16):
    return patch_bundle_entry_in_place(
        bundle_path, entry_to_patch, merged_raw, compress=True, align=align
    )


# ---------------------------------------------------------------------------
# Intro movie (recap_wip.usm) embedded subtitles
# ---------------------------------------------------------------------------
#
# The intro cinematic is a CRI Sofdec USM stream. Besides video (@SFV) and audio
# (@SFA) it carries embedded subtitle chunks (@SBT) in multiple language channels.
# For languages that have an SBT channel the game renders those embedded subtitles
# and ignores movies\cutscenes\gamestart\subs\recap_wip_<lang>.subs; the .subs
# files are only used for languages without a channel (for example tr, hu, ua).
# To show dual subtitles for a target language that has a channel, the SBT chunks
# of that channel must be rewritten inside the USM.

RECAP_USM_NAME = r"movies\cutscenes\gamestart\recap_wip.usm"
USM_CHUNK_ALIGN = 32
USM_CHUNK_HEADER_SIZE = 0x18
SBT_PAYLOAD_HEADER_FMT = "<IIIII"  # channel, time unit, start, duration, text length
SBT_PAYLOAD_HEADER_SIZE = struct.calcsize(SBT_PAYLOAD_HEADER_FMT)
SBT_CHANNEL_MATCH_THRESHOLD = 0.6

# Channel numbers observed in recap_wip.usm (Next-Gen). Used as a sanity check;
# the actual channel is detected by comparing texts against recap_wip_<lang>.subs.
SBT_LANGUAGE_CHANNELS = {
    "en": 0,
    "pl": 1,
    "de": 2,
    "it": 3,
    "fr": 4,
    "cz": 5,
    "es": 6,
    "zh": 7,
    "ru": 8,
    "cn": 9,
    "jp": 10,
    "kr": 11,
    "br": 12,
    "esmx": 13,
    "ar": 14,
}


@dataclass
class UsmChunk:
    offset: int
    chunk_id: bytes
    total_size: int  # including the 8-byte id/size prefix


@dataclass
class SbtLine:
    chunk_index: int
    channel: int
    time_unit: int
    start: int
    duration: int
    text: str
    text_len: int  # raw length field (UTF-8 bytes + terminator bytes)


# Observed convention in recap_wip.usm: the length field counts the UTF-8 text
# plus a two-byte NUL terminator.
SBT_TEXT_TERMINATOR = b"\x00\x00"


def find_entry_by_name(entries, name):
    wanted = name.lower()
    for entry in entries:
        if entry.name.lower() == wanted:
            return entry
    return None


def iter_usm_chunks(raw):
    pos = 0
    total = len(raw)
    index = 0
    while pos + 8 <= total:
        chunk_id = raw[pos : pos + 4]
        size = struct.unpack(">I", raw[pos + 4 : pos + 8])[0]
        chunk_total = 8 + size
        if pos + chunk_total > total:
            raise ValueError(f"Truncated USM chunk {chunk_id!r} at offset {pos}")
        yield index, UsmChunk(offset=pos, chunk_id=chunk_id, total_size=chunk_total)
        pos += chunk_total
        index += 1
    if pos != total:
        raise ValueError(f"USM stream has {total - pos} trailing bytes after last chunk")


def _usm_chunk_header(raw, chunk):
    base = chunk.offset
    header_size = raw[base + 9]
    footer_size = struct.unpack(">H", raw[base + 10 : base + 12])[0]
    chunk_type = struct.unpack(">I", raw[base + 12 : base + 16])[0]
    payload_start = base + 8 + header_size
    payload_end = base + chunk.total_size - footer_size
    return header_size, footer_size, chunk_type, payload_start, payload_end


def parse_sbt_data_chunk(raw, chunk_index, chunk):
    header_size, footer_size, chunk_type, payload_start, payload_end = _usm_chunk_header(raw, chunk)
    if chunk_type != 0:
        return None
    payload = raw[payload_start:payload_end]
    if len(payload) < SBT_PAYLOAD_HEADER_SIZE:
        raise ValueError(f"SBT chunk at {chunk.offset} has a short payload")
    channel, time_unit, start, duration, text_len = struct.unpack(
        SBT_PAYLOAD_HEADER_FMT, payload[:SBT_PAYLOAD_HEADER_SIZE]
    )
    text_bytes = payload[SBT_PAYLOAD_HEADER_SIZE : SBT_PAYLOAD_HEADER_SIZE + text_len]
    return SbtLine(
        chunk_index=chunk_index,
        channel=channel,
        time_unit=time_unit,
        start=start,
        duration=duration,
        text=text_bytes.rstrip(b"\x00").decode("utf-8", "replace"),
        text_len=text_len,
    )


def build_sbt_data_chunk(raw, chunk, line, new_text):
    """Return a replacement @SBT chunk carrying new_text (same header, resized)."""
    header_size, footer_size, chunk_type, payload_start, payload_end = _usm_chunk_header(raw, chunk)
    text_bytes = new_text.encode("utf-8") + SBT_TEXT_TERMINATOR
    payload = struct.pack(
        SBT_PAYLOAD_HEADER_FMT,
        line.channel,
        line.time_unit,
        line.start,
        line.duration,
        len(text_bytes),
    ) + text_bytes

    body_size = 8 + header_size + len(payload)
    total_size = _align_up(body_size, USM_CHUNK_ALIGN)
    new_footer = total_size - body_size

    header = bytearray(raw[chunk.offset + 8 : chunk.offset + 8 + header_size])
    header[2:4] = struct.pack(">H", new_footer)

    out = bytearray()
    out += chunk.chunk_id
    out += struct.pack(">I", total_size - 8)
    out += header
    out += payload
    out += b"\x00" * new_footer
    return bytes(out), len(text_bytes), total_size


def _utf_row_fields(buf, base):
    """Minimal CRI @UTF reader.

    Returns (table_name, rows) where rows is a list of dicts mapping column name to
    (absolute_offset_or_None, struct_fmt_or_None, value). Only numeric per-row
    columns get an absolute offset so callers can patch them in place.
    """
    if buf[base : base + 4] != b"@UTF":
        raise ValueError("Expected @UTF table")
    table_size = struct.unpack(">I", buf[base + 4 : base + 8])[0]
    body_base = base + 8
    body = buf[body_base : body_base + table_size]
    rows_off, strings_off, data_off, name_off, num_cols, row_len, num_rows = struct.unpack(
        ">IIIIHHI", body[:24]
    )
    strings = body[strings_off:data_off]

    def read_string(offset):
        end = strings.find(b"\x00", offset)
        return strings[offset:end].decode("utf-8", "replace")

    numeric = {
        0: ">B",
        1: ">b",
        2: ">H",
        3: ">h",
        4: ">I",
        5: ">i",
        6: ">Q",
        7: ">q",
        8: ">f",
    }

    columns = []
    pos = 24
    for _ in range(num_cols):
        flags = body[pos]
        pos += 1
        col_name = read_string(struct.unpack(">I", body[pos : pos + 4])[0])
        pos += 4
        col_type = flags & 0x0F
        storage = flags & 0xF0
        const = None
        if storage == 0x30:
            if col_type in numeric:
                fmt = numeric[col_type]
                const = struct.unpack(fmt, body[pos : pos + struct.calcsize(fmt)])[0]
                pos += struct.calcsize(fmt)
            elif col_type == 0xA:
                const = read_string(struct.unpack(">I", body[pos : pos + 4])[0])
                pos += 4
            elif col_type == 0xB:
                const = struct.unpack(">II", body[pos : pos + 8])
                pos += 8
        columns.append((col_name, col_type, storage, const))

    rows = []
    for row_index in range(num_rows):
        rp = rows_off + row_index * row_len
        row = {}
        for col_name, col_type, storage, const in columns:
            if storage == 0x30:
                row[col_name] = (None, None, const)
            elif storage == 0x50:
                if col_type in numeric:
                    fmt = numeric[col_type]
                    value = struct.unpack(fmt, body[rp : rp + struct.calcsize(fmt)])[0]
                    row[col_name] = (body_base + rp, fmt, value)
                    rp += struct.calcsize(fmt)
                elif col_type == 0xA:
                    value = read_string(struct.unpack(">I", body[rp : rp + 4])[0])
                    row[col_name] = (None, None, value)
                    rp += 4
                elif col_type == 0xB:
                    value = struct.unpack(">II", body[rp : rp + 8])
                    row[col_name] = (None, None, value)
                    rp += 8
            else:
                row[col_name] = (None, None, None)
        rows.append(row)
    return read_string(name_off), rows


def _patch_u32_field(buf, field, new_value, grow_only=True):
    offset, fmt, old_value = field
    if offset is None:
        return False
    if grow_only and new_value <= old_value:
        return False
    buf[offset : offset + struct.calcsize(fmt)] = struct.pack(fmt, new_value)
    return True


def _normalize_subtitle_text(text):
    text = text.replace("\u2026", "...")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def detect_sbt_channel(sbt_lines, target_records, expected_channel=None, sample_size=5):
    """Pick the SBT channel whose texts match the target .subs records best."""
    import difflib

    by_start = {record.start_ms: record for record in target_records}
    scores = {}
    channels = sorted({line.channel for line in sbt_lines})
    for channel in channels:
        lines = [line for line in sbt_lines if line.channel == channel][:sample_size]
        pairs = []
        for line in lines:
            record = by_start.get(line.start)
            if record is not None:
                pairs.append((_normalize_subtitle_text(line.text), _normalize_subtitle_text(record.text)))
        if not pairs:
            scores[channel] = 0.0
            continue
        ratios = [difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs]
        scores[channel] = sum(ratios) / len(ratios)

    best_channel = max(scores, key=scores.get) if scores else None
    best_score = scores.get(best_channel, 0.0) if best_channel is not None else 0.0
    return best_channel, best_score, scores


def build_merged_recap_usm(bundle_path, target_lang, source_lang, separator_style, source_first=False):
    """Rewrite the target language SBT channel of recap_wip.usm with merged lines.

    Returns None when the target language has no embedded SBT channel (the game
    then falls back to recap_wip_<lang>.subs, which is handled separately).
    """
    bundle_path = os.path.abspath(bundle_path)
    target_lang = target_lang.lower()
    source_lang = source_lang.lower()

    if separator_style == "escaped-newline":
        separator = r"\n"
    elif separator_style == "same-line":
        separator = " | "
    elif separator_style == "actual-newline":
        separator = "\n"
    else:
        raise ValueError(f"Unknown separator style: {separator_style}")

    header, entries = load_bundle_entries(bundle_path)
    usm_entry = find_entry_by_name(entries, RECAP_USM_NAME)
    if usm_entry is None:
        raise ValueError(f"Could not find intro movie entry: {RECAP_USM_NAME}")
    if usm_entry.compression_flag != 0:
        raise ValueError("Unexpected compressed USM entry; refusing to rewrite.")

    target_entry = find_recap_entry(entries, target_lang)
    source_entry = find_recap_entry(entries, source_lang)
    if target_entry is None:
        raise ValueError(f"Could not find target recap entry for language: {target_lang}")
    if source_entry is None:
        raise ValueError(f"Could not find source recap entry for language: {source_lang}")

    def load_records(entry):
        payload = read_entry_payload(bundle_path, entry)
        text = decode_subs_text(decode_subs_payload(entry, payload))
        return parse_subtitle_records(text)[1]

    target_records = load_records(target_entry)
    source_records = load_records(source_entry)

    raw = read_entry_payload(bundle_path, usm_entry)
    if raw[:4] != b"CRID":
        raise ValueError("Intro movie payload does not start with a CRID chunk.")

    chunks = list(iter_usm_chunks(raw))
    sbt_lines = []
    sbt_header_chunk = None
    for index, chunk in chunks:
        if chunk.chunk_id != b"@SBT":
            continue
        header_size, footer_size, chunk_type, payload_start, payload_end = _usm_chunk_header(raw, chunk)
        if chunk_type == 0:
            sbt_lines.append(parse_sbt_data_chunk(raw, index, chunk))
        elif chunk_type == 1 and raw[payload_start : payload_start + 4] == b"@UTF":
            sbt_header_chunk = (chunk, payload_start)

    if not sbt_lines:
        return None

    expected_channel = SBT_LANGUAGE_CHANNELS.get(target_lang)
    channel, score, scores = detect_sbt_channel(sbt_lines, target_records, expected_channel)
    warnings = []
    if channel is None or score < SBT_CHANNEL_MATCH_THRESHOLD:
        if expected_channel is None:
            # No channel for this language: the game uses the .subs file.
            return None
        raise ValueError(
            f"Could not match an SBT channel for language '{target_lang}' "
            f"(best channel {channel}, score {score:.2f})."
        )
    if expected_channel is not None and expected_channel != channel:
        warnings.append(
            f"SBT channel detected by text ({channel}) differs from expected ({expected_channel}); "
            "using detected channel."
        )

    source_by_start = {record.start_ms: record for record in source_records}
    target_lines = [line for line in sbt_lines if line.channel == channel]
    replacements = {}
    merged_preview = []
    max_content = 0
    max_chunk = 0
    unmatched = 0
    for position, line in enumerate(target_lines):
        source_record = source_by_start.get(line.start)
        if source_record is None and position < len(source_records):
            source_record = source_records[position]
            unmatched += 1
        target_text = line.text.strip()
        source_text = source_record.text.strip() if source_record is not None else ""
        if source_text and source_text not in target_text:
            if source_first:
                merged_text = f"{source_text}{separator}{target_text}"
            else:
                merged_text = f"{target_text}{separator}{source_text}"
        else:
            merged_text = target_text
        merged_preview.append((line.start, line.start + line.duration, merged_text))
        chunk = chunks[line.chunk_index][1]
        new_chunk, content_size, total_size = build_sbt_data_chunk(raw, chunk, line, merged_text)
        replacements[line.chunk_index] = new_chunk
        max_content = max(max_content, content_size)
        max_chunk = max(max_chunk, total_size)
    if unmatched:
        warnings.append(f"{unmatched} SBT line(s) matched by order instead of start time.")

    # Account for untouched channels when deciding header maxima.
    for line in sbt_lines:
        if line.channel == channel:
            continue
        chunk = chunks[line.chunk_index][1]
        max_content = max(max_content, line.text_len)
        max_chunk = max(max_chunk, chunk.total_size)

    out = bytearray()
    new_offsets = {}
    for index, chunk in chunks:
        new_offsets[index] = len(out)
        replacement = replacements.get(index)
        if replacement is not None:
            out += replacement
        else:
            out += raw[chunk.offset : chunk.offset + chunk.total_size]

    header_patches = []
    # CRID directory: row 0 describes the whole .usm and stores its file size.
    crid_chunk = chunks[0][1]
    crid_header_size = raw[crid_chunk.offset + 9]
    crid_utf_base = new_offsets[0] + 8 + crid_header_size
    table_name, rows = _utf_row_fields(out, crid_utf_base)
    if rows and "filesize" in rows[0]:
        if _patch_u32_field(out, rows[0]["filesize"], len(out), grow_only=False):
            header_patches.append(f"CRID filesize -> {len(out)}")

    # SUBTITLE_HDRINFO: raise content_xsize / ixsize if merged lines exceed them.
    if sbt_header_chunk is not None:
        hdr_chunk, payload_start = sbt_header_chunk
        rel = payload_start - hdr_chunk.offset
        hdr_index = next(i for i, c in chunks if c.offset == hdr_chunk.offset)
        table_name, rows = _utf_row_fields(out, new_offsets[hdr_index] + rel)
        if rows:
            if _patch_u32_field(out, rows[0].get("content_xsize", (None, None, 0)), max_content):
                header_patches.append(f"SBT content_xsize -> {max_content}")
            if _patch_u32_field(out, rows[0].get("ixsize", (None, None, 0)), max_chunk):
                header_patches.append(f"SBT ixsize -> {max_chunk}")

    return {
        "bundle": bundle_path,
        "usm_entry": usm_entry,
        "target_language": target_lang,
        "source_language": source_lang,
        "channel": channel,
        "channel_score": score,
        "channel_scores": scores,
        "channel_count": len({line.channel for line in sbt_lines}),
        "lines_total": len(target_lines),
        "lines_modified": len(replacements),
        "merged_preview": merged_preview,
        "max_content_size": max_content,
        "max_chunk_size": max_chunk,
        "header_patches": header_patches,
        "warnings": warnings,
        "original_size": len(raw),
        "new_raw": bytes(out),
    }


def build_merged_recap_payload(bundle_path, target_lang, source_lang, separator_style, source_first=False):
    bundle_path = os.path.abspath(bundle_path)
    target_lang = target_lang.lower()
    source_lang = source_lang.lower()

    header, entries = load_bundle_entries(bundle_path)
    target_entry = find_recap_entry(entries, target_lang)
    source_entry = find_recap_entry(entries, source_lang)

    if target_entry is None:
        raise ValueError(f"Could not find target recap entry for language: {target_lang}")
    if source_entry is None:
        raise ValueError(f"Could not find source recap entry for language: {source_lang}")

    language_payloads = {}
    for lang, entry in ((target_lang, target_entry), (source_lang, source_entry)):
        payload = read_entry_payload(bundle_path, entry)
        decoded_raw = decode_subs_payload(entry, payload)
        decoded_text = decode_subs_text(decoded_raw)
        language_payloads[lang] = {
            "entry": entry,
            "payload": payload,
            "decoded_raw": decoded_raw,
            "decoded_text": decoded_text,
        }

    target_header, target_records = parse_subtitle_records(language_payloads[target_lang]["decoded_text"])
    source_header, source_records = parse_subtitle_records(language_payloads[source_lang]["decoded_text"])

    merged_records = merge_records(
        target_records=target_records,
        source_records=source_records,
        separator_style=separator_style,
        source_first=source_first,
    )

    merged_text = render_subtitle_text(target_header, merged_records)
    merged_raw = merged_text.encode("utf-16")
    merged_compressed = zlib.compress(merged_raw, level=9)

    return {
        "bundle": bundle_path,
        "header": header,
        "target_language": target_lang,
        "source_language": source_lang,
        "target_entry": target_entry,
        "source_entry": source_entry,
        "target_header": target_header,
        "source_header": source_header,
        "target_records": target_records,
        "source_records": source_records,
        "merged_records": merged_records,
        "language_payloads": language_payloads,
        "merged_text": merged_text,
        "merged_raw": merged_raw,
        "merged_zlib": merged_compressed,
    }


def command_build(args):
    bundle_path = os.path.abspath(args.bundle)
    output_dir = os.path.abspath(args.output_dir)
    target_lang = args.target_lang.lower()
    source_lang = args.source_lang.lower()

    ensure_dir(output_dir)

    print(f"Reading bundle metadata: {bundle_path}")
    header, entries = load_bundle_entries(bundle_path)

    target_entry = find_recap_entry(entries, target_lang)
    source_entry = find_recap_entry(entries, source_lang)

    if target_entry is None:
        raise ValueError(f"Could not find target recap entry for language: {target_lang}")
    if source_entry is None:
        raise ValueError(f"Could not find source recap entry for language: {source_lang}")

    print(f"Target recap entry: {target_entry.name}")
    print(f"Source recap entry: {source_entry.name}")

    dump_dir = os.path.join(output_dir, "dump")
    ensure_dir(dump_dir)

    language_payloads = {}
    for lang, entry in ((target_lang, target_entry), (source_lang, source_entry)):
        payload = read_entry_payload(bundle_path, entry)
        decoded_raw = decode_subs_payload(entry, payload)
        decoded_text = decode_subs_text(decoded_raw)

        language_payloads[lang] = {
            "entry": entry,
            "payload": payload,
            "decoded_raw": decoded_raw,
            "decoded_text": decoded_text,
        }

        with open(os.path.join(dump_dir, f"{lang}.payload.bin"), "wb") as handle:
            handle.write(payload)
        with open(os.path.join(dump_dir, f"{lang}.decoded.bin"), "wb") as handle:
            handle.write(decoded_raw)
        write_utf8_text(os.path.join(dump_dir, f"{lang}.subs.txt"), decoded_text)

    target_header, target_records = parse_subtitle_records(language_payloads[target_lang]["decoded_text"])
    source_header, source_records = parse_subtitle_records(language_payloads[source_lang]["decoded_text"])

    if target_header != source_header:
        print(
            "WARNING: subtitle header values differ: "
            f"target={target_header}, source={source_header}. Using target header."
        )

    merged_records = merge_records(
        target_records=target_records,
        source_records=source_records,
        separator_style=args.separator_style,
        source_first=args.source_first,
    )

    merged_text = render_subtitle_text(target_header, merged_records)
    merged_raw = merged_text.encode("utf-16")
    merged_compressed = zlib.compress(merged_raw, level=9)

    merged_dir = os.path.join(output_dir, "merged")
    ensure_dir(merged_dir)
    write_utf8_text(os.path.join(merged_dir, f"{target_lang}_{source_lang}.subs.txt"), merged_text)
    with open(os.path.join(merged_dir, f"{target_lang}_{source_lang}.subs.bin"), "wb") as handle:
        handle.write(merged_raw)
    with open(os.path.join(merged_dir, f"{target_lang}_{source_lang}.subs.zlib"), "wb") as handle:
        handle.write(merged_compressed)

    manifest = {
        "bundle": bundle_path,
        "header": header,
        "target_language": target_lang,
        "source_language": source_lang,
        "target_entry": target_entry.name,
        "source_entry": source_entry.name,
        "target_records": len(target_records),
        "source_records": len(source_records),
        "merged_records": len(merged_records),
        "separator_style": args.separator_style,
        "source_first": args.source_first,
        "merged_raw_size": len(merged_raw),
        "merged_zlib_size": len(merged_compressed),
        "patched_bundle": None,
    }

    if args.patched_bundle:
        patched_info = patch_bundle_copy(
            bundle_path=bundle_path,
            output_bundle_path=args.patched_bundle,
            entry_to_patch=target_entry,
            merged_raw=merged_raw,
            align=16,
        )
        manifest["patched_bundle"] = patched_info
        print(f"Patched bundle written: {patched_info['output_bundle']}")

    manifest["usm"] = None
    if not args.no_usm:
        print("Processing intro movie embedded subtitles (recap_wip.usm @SBT)...")
        usm_result = build_merged_recap_usm(
            bundle_path=bundle_path,
            target_lang=target_lang,
            source_lang=source_lang,
            separator_style=args.separator_style,
            source_first=args.source_first,
        )
        if usm_result is None:
            print(
                f"No embedded SBT channel for '{target_lang}'; the game uses "
                f"recap_wip_{target_lang}.subs for the intro, USM step skipped."
            )
        else:
            for warning in usm_result["warnings"]:
                print(f"WARNING: {warning}")
            print(
                f"SBT channel {usm_result['channel']} matched '{target_lang}' "
                f"(score {usm_result['channel_score']:.2f}); "
                f"{usm_result['lines_modified']}/{usm_result['lines_total']} lines rewritten; "
                f"max text {usm_result['max_content_size']} bytes, max chunk {usm_result['max_chunk_size']} bytes."
            )
            for patch_note in usm_result["header_patches"]:
                print(f"  header: {patch_note}")

            usm_dir = os.path.join(output_dir, "usm")
            ensure_dir(usm_dir)
            preview_lines = [f"{start}, {end}, {text}" for start, end, text in usm_result["merged_preview"]]
            write_utf8_text(
                os.path.join(usm_dir, f"sbt_{target_lang}_{source_lang}.txt"),
                "\n".join(preview_lines) + "\n",
            )
            with open(os.path.join(usm_dir, "recap_wip.patched.usm"), "wb") as handle:
                handle.write(usm_result["new_raw"])

            manifest["usm"] = {
                "entry": usm_result["usm_entry"].name,
                "channel": usm_result["channel"],
                "channel_score": usm_result["channel_score"],
                "channel_count": usm_result["channel_count"],
                "lines_modified": usm_result["lines_modified"],
                "max_content_size": usm_result["max_content_size"],
                "max_chunk_size": usm_result["max_chunk_size"],
                "header_patches": usm_result["header_patches"],
                "original_size": usm_result["original_size"],
                "new_size": len(usm_result["new_raw"]),
                "patched_bundle": None,
            }

            if args.patched_bundle:
                usm_patch = patch_bundle_entry_in_place(
                    bundle_path=args.patched_bundle,
                    entry_to_patch=usm_result["usm_entry"],
                    raw=usm_result["new_raw"],
                    compress=False,
                    align=4096,
                )
                manifest["usm"]["patched_bundle"] = usm_patch
                print(f"Patched USM written into bundle copy at offset {usm_patch['new_offset']}")

    write_utf8_text(os.path.join(output_dir, "manifest.json"), json.dumps(manifest, indent=2))

    print("Done.")
    print(f"Extracted dump directory: {dump_dir}")
    print(f"Merged output directory: {merged_dir}")


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Extract and merge Witcher 3 recap subtitle files from movies.bundle. "
            "Supports Next-Gen 304-byte TOC entries with 64-bit offsets."
        )
    )

    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to movies.bundle (for example: The Witcher 3/content/content0/bundles/movies.bundle)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output directory for extracted and merged subtitle artifacts.",
    )
    parser.add_argument(
        "--target-lang",
        default="en",
        help="Recap language entry to patch (default: en).",
    )
    parser.add_argument(
        "--source-lang",
        default="tr",
        help="Recap language entry to append into target lines (default: tr).",
    )
    parser.add_argument(
        "--separator-style",
        choices=["escaped-newline", "same-line", "actual-newline"],
        default="same-line",
        help=(
            "How target and source lines are joined: escaped-newline => literal \\n, "
            "same-line => ' | ', actual-newline => real newline character."
        ),
    )
    parser.add_argument(
        "--patched-bundle",
        default="",
        help=(
            "Optional output path for a patched bundle copy. "
            "If omitted, only extraction/merge files are generated."
        ),
    )
    parser.add_argument(
        "--source-first",
        action="store_true",
        help=(
            "Place source language text before target language text in merged recap lines."
        ),
    )
    parser.add_argument(
        "--no-usm",
        action="store_true",
        help=(
            "Skip rewriting the embedded subtitle channel inside recap_wip.usm. "
            "Without the USM step the intro keeps showing the original single-language "
            "text for languages that have an embedded channel (en, de, fr, ...)."
        ),
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        command_build(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
