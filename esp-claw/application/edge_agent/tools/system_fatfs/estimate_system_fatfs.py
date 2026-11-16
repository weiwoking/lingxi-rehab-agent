#!/usr/bin/env python3
"""Estimate ESP-IDF FATFS cluster use without generating an image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


RUNTIME_METADATA = (
    "ui/fonts/rehab_ui_glyphs.txt",
    "ui/fonts/rehab_ui_codepoints.txt",
    "ui/fonts/rehab_ui_codepoint_ranges.txt",
    "ui/fonts/rehab_ui_glyph_manifest.json",
)


def _clusters_for_bytes(size: int, cluster_size: int) -> int:
    return (size + cluster_size - 1) // cluster_size


def _tree_clusters(
    root: Path,
    current: Path,
    cluster_size: int,
    excluded: set[str],
) -> int:
    relative = current.relative_to(root).as_posix()
    if relative in excluded:
        return 0
    if current.is_file():
        return _clusters_for_bytes(current.stat().st_size, cluster_size)

    children = [
        child
        for child in sorted(current.iterdir(), key=lambda item: item.name)
        if child.relative_to(root).as_posix() not in excluded
    ]
    # Match ESP-IDF fatfsgen.calculate_min_space(), including its conservative
    # long-filename directory-entry estimate based on the current directory.
    lfn_entries = (len(current.name) + 12) // 13
    directory_bytes = 2 * 32 + len(children) * (lfn_entries + 1) * 32
    return _clusters_for_bytes(directory_bytes, cluster_size) + sum(
        _tree_clusters(root, child, cluster_size, excluded)
        for child in children
    )


def available_data_clusters(
    partition_size: int,
    sector_size: int,
    fat_count: int = 2,
    root_entries: int = 512,
    reserved_sectors: int = 1,
) -> int:
    total_sectors = partition_size // sector_size
    # ESP-IDF selects FAT12 here. Its FAT-size helper receives total sectors;
    # each FAT12 entry occupies 1.5 bytes.
    fat_bytes = (total_sectors * 3 + 1) // 2 + 12 // 4
    sectors_per_fat = (fat_bytes + sector_size - 1) // sector_size
    root_sectors = root_entries * 32 // sector_size
    return (
        total_sectors
        - reserved_sectors
        - fat_count * sectors_per_fat
        - root_sectors
    )


def estimate(
    root: Path,
    partition_size: int = 0x100000,
    cluster_size: int = 4096,
    excludes: tuple[str, ...] = (),
) -> dict[str, object]:
    root = root.resolve()
    excluded = {item.replace("\\", "/").lstrip("/") for item in excludes}
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    ]
    directories = [
        path
        for path in root.rglob("*")
        if path.is_dir() and path.relative_to(root).as_posix() not in excluded
    ]
    logical_bytes = sum(path.stat().st_size for path in files)
    file_cluster_bytes = sum(
        _clusters_for_bytes(path.stat().st_size, cluster_size) * cluster_size
        for path in files
    )
    required_clusters = _tree_clusters(
        root, root, cluster_size, excluded
    )
    capacity_clusters = available_data_clusters(
        partition_size, cluster_size
    )
    remaining_clusters = capacity_clusters - required_clusters
    largest = sorted(
        (
            {
                "path": path.relative_to(root).as_posix(),
                "logical_bytes": path.stat().st_size,
                "cluster_bytes": (
                    _clusters_for_bytes(path.stat().st_size, cluster_size)
                    * cluster_size
                ),
            }
            for path in files
        ),
        key=lambda item: (-int(item["cluster_bytes"]), str(item["path"])),
    )[:20]
    return {
        "root": str(root),
        "partition_bytes": partition_size,
        "cluster_bytes": cluster_size,
        "file_count": len(files),
        "directory_count": len(directories) + 1,
        "logical_bytes": logical_bytes,
        "file_cluster_bytes": file_cluster_bytes,
        "estimated_required_clusters": required_clusters,
        "estimated_used_bytes": required_clusters * cluster_size,
        "available_data_clusters": capacity_clusters,
        "estimated_remaining_clusters": remaining_clusters,
        "estimated_remaining_bytes": remaining_clusters * cluster_size,
        "excluded": sorted(excluded),
        "largest_files": largest,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("--partition-size", type=lambda value: int(value, 0), default=0x100000)
    parser.add_argument("--cluster-size", type=lambda value: int(value, 0), default=4096)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--minimum-margin", type=lambda value: int(value, 0), default=0x4000)
    args = parser.parse_args()

    result = estimate(
        args.input_directory,
        partition_size=args.partition_size,
        cluster_size=args.cluster_size,
        excludes=tuple(args.exclude),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if int(result["estimated_remaining_bytes"]) < args.minimum_margin:
        raise SystemExit(
            "SYSTEM FATFS estimated margin is below "
            f"{args.minimum_margin} bytes"
        )


if __name__ == "__main__":
    main()
