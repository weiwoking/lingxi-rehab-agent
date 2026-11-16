#!/usr/bin/env python3
"""Create a host-only clean SYSTEM projection without invoking firmware build."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


APP = Path(__file__).resolve().parents[2]
REPOSITORY = APP.parents[1]
BUILD = APP / "build"
SYSTEM_SOURCE = APP / "fatfs_image" / "system"
SKILL_TOOLS = REPOSITORY / "components" / "common" / "skill_builder" / "tools"
LUA_TOOLS = (
    REPOSITORY / "components" / "common" / "lua_module_builder" / "tools"
)
sys.path.insert(0, str(SKILL_TOOLS))
sys.path.insert(0, str(LUA_TOOLS))

from estimate_system_fatfs import estimate  # noqa: E402
import generate_builtin_modules_skill as module_index  # noqa: E402
import sync_component_skills as skill_sync  # noqa: E402
import sync_lua_module_docs as lua_docs  # noqa: E402
import sync_lua_module_resources as lua_resources  # noqa: E402
from lua_sync_common import collect_build_component_sources  # noqa: E402


MINIMUM_MARGIN = 0x20000
BUILTIN_TEST_PREFIX = "builtin_lua_modules/scripts/builtin/test/"


def selected_board_overlay() -> Path | None:
    cmake = APP / "components" / "gen_bmgr_codes" / "CMakeLists.txt"
    if not cmake.is_file():
        return None
    match = re.search(
        r'^message\(STATUS "Board Path: (?P<path>.+)"\)$',
        cmake.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        return None
    overlay = Path(match.group("path")) / "fatfs_image"
    return overlay.resolve() if overlay.is_dir() else None


def project(output_dir: Path, build_dir: Path = BUILD) -> dict[str, object]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"projection output already exists: {output_dir}")

    shutil.copytree(SYSTEM_SOURCE, output_dir)
    board_overlay = selected_board_overlay()
    if board_overlay:
        shutil.copytree(board_overlay, output_dir, dirs_exist_ok=True)

    skill_sources = skill_sync.collect_skill_sources(build_dir)
    development_map = skill_sync.collect_component_skills(skill_sources)
    device_map = skill_sync.collect_component_skills(
        skill_sources,
        excluded_skill_ids=skill_sync.DEVICE_RUNTIME_EXCLUDED_SKILLS,
    )
    # Be independent of any prior incremental build output in the generated
    # skill source directory.
    device_map = {
        name: source
        for name, source in device_map.items()
        if not name.replace("\\", "/").startswith(BUILTIN_TEST_PREFIX)
    }
    skill_manifest_path = output_dir.parent / "projection_skill_manifest.json"
    skill_sync.sync_markdown_files(
        output_dir / "skills",
        skill_manifest_path,
        {"component_files": []},
        device_map,
    )

    all_component_sources = collect_build_component_sources(build_dir)
    lua_sources = collect_build_component_sources(
        build_dir,
        name_prefix=("lua_module_", "lua_driver_"),
    )
    entries = module_index.collect_lua_module_skill_entries(
        lua_sources,
        include_tests=False,
    )
    builtin_skill = output_dir / "skills" / "builtin_lua_modules"
    builtin_skill.mkdir(parents=True, exist_ok=True)
    (builtin_skill / "SKILL.md").write_text(
        module_index.render_generated_skill(entries, include_tests=False),
        encoding="utf-8",
    )
    docs_dir = builtin_skill / "scripts" / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for entry in lua_docs.collect_lua_module_doc_entries(lua_sources):
        source_name = str(entry["source_name"])
        content = Path(entry["doc_path"]).read_text(encoding="utf-8")
        (docs_dir / str(entry["file"])).write_text(
            lua_docs.render_lua_module_doc(
                source_name,
                content,
                lua_docs.PROFILE_DEVICE_RUNTIME,
            ),
            encoding="utf-8",
        )

    libs_manifest = output_dir.parent / "projection_lua_libs_manifest.json"
    libs_plan = lua_resources.collect_builtin_lua_module_libs(
        all_component_sources,
        output_dir / "scripts" / "builtin",
        libs_manifest,
    )
    libs_plan.apply()

    result = estimate(output_dir)
    development_skill_ids = sorted(
        {Path(name).parts[0] for name in development_map}
    )
    device_skill_ids = sorted({Path(name).parts[0] for name in device_map})
    result.update(
        {
            "profile": skill_sync.PROFILE_DEVICE_RUNTIME,
            "selected_board_overlay": (
                str(board_overlay) if board_overlay else None
            ),
            "development_skill_count": len(development_skill_ids),
            "device_skill_count": len(device_skill_ids),
            "excluded_skills": sorted(
                set(development_skill_ids) - set(device_skill_ids)
            ),
            "lua_source_test_count": len(
                module_index.collect_lua_module_test_script_entries(
                    all_component_sources
                )
            ),
            "packaged_lua_test_count": len(
                list(
                    (
                        builtin_skill / "scripts" / "builtin" / "test"
                    ).rglob("*.lua")
                )
            ),
            "lua_doc_count": len(
                list((builtin_skill / "scripts" / "docs").glob("*.md"))
            ),
            "lua_runtime_lib_file_count": libs_plan.count,
            "minimum_margin_bytes": MINIMUM_MARGIN,
            "margin_pass": (
                int(result["estimated_remaining_bytes"])
                >= MINIMUM_MARGIN
            ),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--build-dir", type=Path, default=BUILD)
    args = parser.parse_args()
    result = project(args.output_directory, args.build_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["margin_pass"]:
        raise SystemExit("projected SYSTEM margin is below 128 KiB")


if __name__ == "__main__":
    main()
