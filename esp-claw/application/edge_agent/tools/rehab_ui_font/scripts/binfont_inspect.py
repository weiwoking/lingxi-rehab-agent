#!/usr/bin/env python3
"""Inspect LVGL binfont files using the lv_binfont_loader.c layout."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


HEAD_FORMAT = "<IHHHhHhHhhHHBBBBBBBBBBhH"
HEAD_NAMES = (
    "version",
    "tables_count",
    "font_size",
    "ascent",
    "descent",
    "typo_ascent",
    "typo_descent",
    "typo_line_gap",
    "min_y",
    "max_y",
    "default_advance_width",
    "kerning_scale",
    "index_to_loc_format",
    "glyph_id_format",
    "advance_width_format",
    "bits_per_pixel",
    "xy_bits",
    "wh_bits",
    "advance_width_bits",
    "compression_id",
    "subpixels_mode",
    "padding",
    "underline_position",
    "underline_thickness",
)


class BinfontError(ValueError):
    pass


def section(data: bytes, offset: int, label: bytes) -> tuple[int, int]:
    if offset + 8 > len(data):
        raise BinfontError(f"truncated {label.decode()} section")
    length, actual = struct.unpack_from("<I4s", data, offset)
    if actual != label or length < 8 or offset + length > len(data):
        raise BinfontError(f"invalid {label.decode()} section")
    return length, offset + 8


def read_bits(
    data: bytes, bit_offset: int, count: int, signed: bool = False
) -> int:
    value = 0
    for bit_index in range(count):
        absolute = bit_offset + bit_index
        value = (value << 1) | (
            (data[absolute // 8] >> (7 - absolute % 8)) & 1
        )
    if signed and count and value & (1 << (count - 1)):
        value -= 1 << count
    return value


def parse(path: Path, codepoints: list[int]) -> dict:
    data = path.read_bytes()
    head_length, head_data = section(data, 0, b"head")
    head_values = struct.unpack_from(HEAD_FORMAT, data, head_data)
    head = dict(zip(HEAD_NAMES, head_values))
    if head_data + struct.calcsize(HEAD_FORMAT) != head_length:
        raise BinfontError("unexpected head size")

    cmap_start = head_length
    cmap_length, cmap_data = section(data, cmap_start, b"cmap")
    cmap_count = struct.unpack_from("<I", data, cmap_data)[0]
    cmap_data += 4
    cmaps = []
    for index in range(cmap_count):
        values = struct.unpack_from("<IIHHHBB", data, cmap_data + index * 16)
        entry = dict(
            zip(
                (
                    "data_offset",
                    "range_start",
                    "range_length",
                    "glyph_id_start",
                    "data_entries_count",
                    "format_type",
                    "padding",
                ),
                values,
            )
        )
        payload = cmap_start + entry["data_offset"]
        count = entry["data_entries_count"]
        if entry["format_type"] == 0:
            entry["glyph_id_offsets"] = list(data[payload : payload + count])
        elif entry["format_type"] in (1, 3):
            entry["unicode_offsets"] = list(
                struct.unpack_from(f"<{count}H", data, payload)
            )
            if entry["format_type"] == 1:
                payload += count * 2
                entry["glyph_id_offsets"] = list(
                    struct.unpack_from(f"<{count}H", data, payload)
                )
        elif entry["format_type"] != 2:
            raise BinfontError(
                f"unknown cmap format {entry['format_type']}"
            )
        cmaps.append(entry)

    def glyph_id(codepoint: int) -> int | None:
        for cmap in cmaps:
            relative = codepoint - cmap["range_start"]
            if relative < 0 or relative >= cmap["range_length"]:
                continue
            cmap_type = cmap["format_type"]
            if cmap_type == 2:
                return cmap["glyph_id_start"] + relative
            if cmap_type == 0:
                offset = cmap["glyph_id_offsets"][relative]
                if offset == 0 and relative != 0:
                    continue
                return cmap["glyph_id_start"] + offset
            try:
                list_index = cmap["unicode_offsets"].index(relative)
            except ValueError:
                continue
            if cmap_type == 3:
                return cmap["glyph_id_start"] + list_index
            return (
                cmap["glyph_id_start"]
                + cmap["glyph_id_offsets"][list_index]
            )
        return None

    loca_start = cmap_start + cmap_length
    loca_length, loca_data = section(data, loca_start, b"loca")
    loca_count = struct.unpack_from("<I", data, loca_data)[0]
    loca_data += 4
    if head["index_to_loc_format"] == 0:
        offsets = list(
            struct.unpack_from(f"<{loca_count}H", data, loca_data)
        )
    elif head["index_to_loc_format"] == 1:
        offsets = list(
            struct.unpack_from(f"<{loca_count}I", data, loca_data)
        )
    else:
        raise BinfontError("invalid loca format")

    glyph_start = loca_start + loca_length
    glyph_length, _ = section(data, glyph_start, b"glyf")
    descriptor_bits = (
        head["advance_width_bits"]
        + 2 * head["xy_bits"]
        + 2 * head["wh_bits"]
    )

    def descriptor(gid: int) -> dict:
        if gid < 0 or gid >= loca_count:
            raise BinfontError(f"glyph id {gid} outside loca table")
        current = offsets[gid]
        following = (
            offsets[gid + 1] if gid + 1 < loca_count else glyph_length
        )
        if current > following or glyph_start + following > len(data):
            raise BinfontError(f"invalid offsets for glyph {gid}")
        if gid == 0 and current == following:
            return {
                "glyph_id": 0,
                "adv_w": 0,
                "ofs_x": 0,
                "ofs_y": 0,
                "box_w": 0,
                "box_h": 0,
                "physical_bitmap_offset": glyph_start + current,
                "bitmap_size": 0,
                "bitmap_data_nonempty": False,
            }
        bit = (glyph_start + current) * 8
        if head["advance_width_bits"]:
            advance = read_bits(data, bit, head["advance_width_bits"])
            bit += head["advance_width_bits"]
        else:
            advance = head["default_advance_width"]
        if head["advance_width_format"] == 0:
            advance *= 16
        ofs_x = read_bits(data, bit, head["xy_bits"], signed=True)
        bit += head["xy_bits"]
        ofs_y = read_bits(data, bit, head["xy_bits"], signed=True)
        bit += head["xy_bits"]
        box_w = read_bits(data, bit, head["wh_bits"])
        bit += head["wh_bits"]
        box_h = read_bits(data, bit, head["wh_bits"])
        bitmap_size = following - current - descriptor_bits // 8
        bitmap_offset = glyph_start + current + descriptor_bits // 8
        bitmap = data[bitmap_offset : glyph_start + following]
        return {
            "glyph_id": gid,
            "adv_w": advance,
            "ofs_x": ofs_x,
            "ofs_y": ofs_y,
            "box_w": box_w,
            "box_h": box_h,
            "physical_bitmap_offset": bitmap_offset,
            "bitmap_size": bitmap_size,
            "bitmap_data_nonempty": bool(bitmap) and any(bitmap),
            "bitmap_sha256": hashlib.sha256(bitmap).hexdigest(),
        }

    inspected = {}
    for codepoint in codepoints:
        gid = glyph_id(codepoint)
        inspected[f"U+{codepoint:04X}"] = (
            None if gid is None else descriptor(gid)
        )

    bitmap_total_size = 0
    nonempty_descriptor_count = 0
    for gid in range(loca_count):
        item = descriptor(gid)
        if item["box_w"] and item["box_h"]:
            nonempty_descriptor_count += 1
            bitmap_total_size += item["bitmap_size"]

    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "file_size": len(data),
        "header": head,
        "cmap_count": cmap_count,
        "cmaps": [
            {
                key: value
                for key, value in cmap.items()
                if key not in ("unicode_offsets", "glyph_id_offsets")
            }
            for cmap in cmaps
        ],
        "mapped_codepoint_count": sum(
            cmap["data_entries_count"] for cmap in cmaps
        ),
        "glyph_descriptor_count": loca_count,
        "nonempty_glyph_descriptor_count": nonempty_descriptor_count,
        "bitmap_total_size": bitmap_total_size,
        "glyphs": inspected,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("font", type=Path)
    parser.add_argument(
        "codepoints", nargs="*", help="Hex codepoints such as 41 or U+4E00"
    )
    args = parser.parse_args()
    codepoints = [
        int(
            value.upper().removeprefix("U+").removeprefix("0X"),
            16,
        )
        for value in args.codepoints
    ]
    print(
        json.dumps(
            parse(args.font, codepoints),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
