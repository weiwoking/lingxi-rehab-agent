#
# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lua_sync_common import ComponentSource, FileSyncPlan, LuaSyncConsole, LuaSyncError, fail
from lua_sync_common import collect_build_component_sources, write_depfile, write_stamp


console = LuaSyncConsole()

# Docs are bundled into the generated builtin_lua_modules skill so they can be
# addressed at runtime via {CUR_SKILL_DIR}/scripts/docs/<name>. skill_builder
# picks them up from this source-tree location and syncs them into the image.
GENERATED_SKILL_DIR = Path(__file__).resolve().parents[1] / 'skills' / 'builtin_lua_modules'
DOCS_DEST_DIR = GENERATED_SKILL_DIR / 'scripts' / 'docs'
PROFILE_DEVELOPMENT = 'development'
PROFILE_DEVICE_RUNTIME = 'device-runtime'
PROFILES = (PROFILE_DEVELOPMENT, PROFILE_DEVICE_RUNTIME)
THREAD_DEVICE_TEST_EXAMPLE = '''local ok, output = thread.run("/system/skills/builtin_lua_modules/scripts/builtin/test/thread_child_a.lua", {
    mode = "sync",
}, {
    timeout_ms = 5000,
})
assert(ok, output)
print(output)

'''
HTTP_SERVER_DEVELOPMENT_SKILL_SECTION = '''## Packaged Skill Example

The repository includes a ready-to-run skill example:

```text
components/lua_modules/lua_module_http_server/skills/http_server_lua_demo/
```

It starts a background Lua web page at `/lua/lua_demo/`, shows "Hello World",
and logs every switch toggle from the browser in the Lua script output.
'''


def is_self_component(source: ComponentSource) -> bool:
    return source.name == 'lua_module_builder'


def iter_module_doc_mappings(source: ComponentSource) -> list[tuple[str, Path]]:
    doc_path = source.root / 'README.md'
    if not doc_path.is_file():
        fail(f"Component '{source.name}' must provide README.md at {doc_path}")
    return [(f'{source.name}.md', doc_path)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Sync Lua module markdown docs into the builtin_lua_modules skill.')
    parser.add_argument('--build-dir', required=True)
    parser.add_argument('--profile', choices=PROFILES, default=PROFILE_DEVELOPMENT)
    parser.add_argument('--manifest-path', required=True)
    parser.add_argument('--stamp-path', required=True)
    parser.add_argument('--depfile', required=True)
    return parser.parse_args()


def collect_lua_module_docs(sources: list[ComponentSource], output_dir: Path, manifest_path: Path) -> FileSyncPlan:
    plan = FileSyncPlan(output_dir, manifest_path)

    for source in sources:
        if is_self_component(source):
            continue
        for output_name, doc_path in iter_module_doc_mappings(source):
            plan.add(output_name, doc_path, source.name)

    return plan


def collect_lua_module_doc_entries(sources: list[ComponentSource]) -> list[dict]:
    entries: list[dict] = []

    for source in sources:
        if is_self_component(source):
            continue
        for output_name, doc_path in iter_module_doc_mappings(source):
            entries.append({'source_name': source.name, 'doc_path': doc_path.resolve(), 'file': output_name})

    entries.sort(key=lambda entry: (str(entry['source_name']), str(entry['file'])))
    return entries


def render_lua_module_doc(
    source_name: str,
    content: str,
    profile: str,
) -> str:
    if (
        profile == PROFILE_DEVICE_RUNTIME
        and source_name == 'lua_module_thread'
    ):
        if content.count(THREAD_DEVICE_TEST_EXAMPLE) != 1:
            fail(
                'lua_module_thread device-test example changed; '
                'update the explicit device-runtime documentation rule'
            )
        return content.replace(THREAD_DEVICE_TEST_EXAMPLE, '')
    if (
        profile == PROFILE_DEVICE_RUNTIME
        and source_name == 'lua_module_http_server'
    ):
        if content.count(HTTP_SERVER_DEVELOPMENT_SKILL_SECTION) != 1:
            fail(
                'lua_module_http_server development-skill section changed; '
                'update the explicit device-runtime documentation rule'
            )
        return content.replace(HTTP_SERVER_DEVELOPMENT_SKILL_SECTION, '')
    return content


def main() -> int:
    args = parse_args()
    build_dir = Path(args.build_dir).resolve()
    output_dir = DOCS_DEST_DIR.resolve()
    manifest_path = Path(args.manifest_path).resolve()
    stamp_path = Path(args.stamp_path).resolve()
    depfile_path = Path(args.depfile).resolve()

    sources = collect_build_component_sources(build_dir, name_prefix=('lua_module_', 'lua_driver_'))
    plan = collect_lua_module_docs(sources, output_dir, manifest_path)
    plan.apply()
    for entry in collect_lua_module_doc_entries(sources):
        output_path = output_dir / str(entry['file'])
        source_name = str(entry['source_name'])
        content = Path(entry['doc_path']).read_text(encoding='utf-8')
        output_path.write_text(
            render_lua_module_doc(source_name, content, args.profile),
            encoding='utf-8',
        )
    write_depfile(depfile_path, stamp_path, plan.input_paths)
    write_stamp(stamp_path)
    console.success(f'CLAW lua docs sync updated {plan.count} markdown files into {output_dir}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except LuaSyncError as exc:
        console.error(f'sync_lua_module_docs.py: error: {exc}')
        sys.exit(1)
