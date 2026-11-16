#
# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from lua_sync_common import ComponentSource, FileSyncPlan, LuaSyncConsole, LuaSyncError
from lua_sync_common import collect_build_component_sources, write_depfile, write_stamp


console = LuaSyncConsole()

# Libraries stay on the shared Lua scripts path so require() can find them at
# runtime. Development profiles may copy tests into the generated skill, while
# the SYSTEM device-runtime profile clears those generated copies.
GENERATED_SKILL_DIR = Path(__file__).resolve().parents[1] / 'skills' / 'builtin_lua_modules'
TESTS_DEST_DIR = GENERATED_SKILL_DIR / 'scripts' / 'builtin' / 'test'
PROFILE_DEVELOPMENT = 'development'
PROFILE_DEVICE_RUNTIME = 'device-runtime'
PROFILES = (PROFILE_DEVELOPMENT, PROFILE_DEVICE_RUNTIME)


def add_lib_script(plan: FileSyncPlan, category_dir: Path, script_path: Path, owner: str) -> None:
    relative_path = script_path.relative_to(category_dir)
    output_name = str(Path('lib') / relative_path)
    plan.add(output_name, script_path, owner)

    doc_path = script_path.with_suffix('.md')
    if not doc_path.is_file():
        raise LuaSyncError(f"Lua library '{script_path}' must have same-name markdown doc '{doc_path.name}'")
    doc_output_name = str(Path('lib') / relative_path.with_suffix('.md'))
    plan.add(doc_output_name, doc_path, owner)


def add_test_script(plan: FileSyncPlan, category_dir: Path, script_path: Path, owner: str) -> None:
    relative_path = script_path.relative_to(category_dir)
    plan.add(str(relative_path), script_path, owner)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Sync builtin Lua module libraries and tests.')
    parser.add_argument('--build-dir', required=True)
    parser.add_argument('--profile', choices=PROFILES, default=PROFILE_DEVELOPMENT)
    parser.add_argument('--builtin-output-dir', required=True,
                        help='Output dir for libs on the require() search path.')
    parser.add_argument('--libs-manifest-path', required=True)
    parser.add_argument('--tests-manifest-path', required=True)
    parser.add_argument('--stamp-path', required=True)
    parser.add_argument('--depfile', required=True)
    return parser.parse_args()


def collect_builtin_lua_module_libs(sources: list[ComponentSource], output_dir: Path, manifest_path: Path) -> FileSyncPlan:
    plan = FileSyncPlan(output_dir, manifest_path)

    for source in sources:
        category_dir = source.root / 'lib'
        if not category_dir.is_dir():
            continue
        for script_path in sorted(category_dir.rglob('*.lua')):
            if not script_path.is_file():
                continue
            add_lib_script(plan, category_dir, script_path, source.name)

    return plan


def collect_builtin_lua_module_tests(sources: list[ComponentSource], output_dir: Path, manifest_path: Path) -> FileSyncPlan:
    plan = FileSyncPlan(output_dir, manifest_path)

    for source in sources:
        category_dir = source.root / 'test'
        if not category_dir.is_dir():
            continue
        for script_path in sorted(category_dir.rglob('*.lua')):
            if not script_path.is_file():
                continue
            add_test_script(plan, category_dir, script_path, source.name)

    return plan


def clear_generated_test_copies(output_dir: Path) -> None:
    """Remove only the generated skill copies, never component test sources."""
    if output_dir.is_dir():
        shutil.rmtree(output_dir)


def main() -> int:
    args = parse_args()
    build_dir = Path(args.build_dir).resolve()
    libs_output_dir = Path(args.builtin_output_dir).resolve()
    tests_output_dir = TESTS_DEST_DIR.resolve()
    libs_manifest = Path(args.libs_manifest_path).resolve()
    tests_manifest = Path(args.tests_manifest_path).resolve()
    stamp_path = Path(args.stamp_path).resolve()
    depfile_path = Path(args.depfile).resolve()

    sources = collect_build_component_sources(build_dir)
    libs_plan = collect_builtin_lua_module_libs(sources, libs_output_dir, libs_manifest)
    if args.profile == PROFILE_DEVICE_RUNTIME:
        # A fullclean has no prior manifest, so explicitly clear the generated
        # skill subtree before applying the empty plan. Component-owned tests
        # remain untouched under their lua_module/lua_driver sources.
        clear_generated_test_copies(tests_output_dir)
        tests_plan = FileSyncPlan(tests_output_dir, tests_manifest)
    else:
        tests_plan = collect_builtin_lua_module_tests(sources, tests_output_dir, tests_manifest)
    libs_plan.apply()
    tests_plan.apply()
    write_depfile(depfile_path, stamp_path, list(libs_plan.input_paths) + list(tests_plan.input_paths))
    write_stamp(stamp_path)
    console.success(
        f'CLAW lua module resource sync updated {libs_plan.count} libs into {libs_output_dir} '
        f'and {tests_plan.count} tests into {tests_output_dir}'
    )
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except LuaSyncError as exc:
        console.error(f'sync_lua_module_resources.py: error: {exc}')
        sys.exit(1)
