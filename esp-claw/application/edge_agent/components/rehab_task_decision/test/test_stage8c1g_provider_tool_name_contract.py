from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[5]
APP = ROOT / "application" / "edge_agent"

BASE_ROOT_VISIBLE_SOURCES = (
    ROOT / "components/claw_capabilities/cap_agent_mgr/src/cap_agent_mgr.c",
    ROOT / "components/claw_capabilities/cap_files/src/cap_files.c",
    ROOT / "components/claw_capabilities/cap_scheduler/src/cap_scheduler.c",
    ROOT / "components/claw_capabilities/cap_lua/src/cap_lua.c",
    ROOT / "components/claw_capabilities/cap_skill_mgr/src/cap_skill_mgr.c",
    ROOT / "components/claw_modules/claw_memory/src/claw_memory_cap.c",
    ROOT / "components/claw_capabilities/cap_llm_inspect/src/cap_llm_inspect.c",
    ROOT / "components/claw_capabilities/cap_http_request/src/cap_http_request.c",
    ROOT / "components/claw_capabilities/cap_web_search/src/cap_web_search.c",
    ROOT / "components/claw_capabilities/cap_router_mgr/src/cap_router_mgr.c",
)
DECISION_CAPABILITIES = (
    APP
    / "components/rehab_task_decision/src/"
    "rehab_task_decision_coordinator_capabilities.c"
)
ENVIRONMENT_CAPABILITIES = (
    APP
    / "components/rehab_environment_scan/src/"
    "rehab_environment_scan_capabilities.c"
)
INTEGRATION_CAPABILITIES = (
    APP / "components/rehab_integration/src/rehab_capabilities.c"
)
CAP_BUILDER = (
    ROOT / "components/claw_modules/claw_cap/src/claw_cap.c"
).read_text(encoding="utf-8-sig")

DESCRIPTOR_RE = re.compile(
    r'\{\s*\.id\s*=\s*"(?P<id>[^"]+)"'
    r'(?P<body>.*?)\.execute\s*=\s*[^,]+,\s*\}',
    re.S,
)
NAME_RE = re.compile(r'\.name\s*=\s*"(?P<name>[^"]*)"')
DESCRIPTION_RE = re.compile(
    r'\.description\s*=\s*(?P<value>(?:"(?:[^"\\]|\\.)*"\s*)+),',
    re.S,
)
C_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
PROVIDER_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def descriptors(path: Path) -> list[dict[str, object]]:
    source = path.read_text(encoding="utf-8-sig")
    result: list[dict[str, object]] = []
    for match in DESCRIPTOR_RE.finditer(source):
        body = match.group("body")
        name = NAME_RE.search(body)
        if name is None:
            raise AssertionError(f"descriptor without name in {path}")
        description_match = DESCRIPTION_RE.search(body)
        description = ""
        if description_match is not None:
            description = "".join(
                ast.literal_eval(literal)
                for literal in C_STRING_RE.findall(
                    description_match.group("value")
                )
            )
        result.append(
            {
                "id": match.group("id"),
                "name": name.group("name"),
                "callable": "CLAW_CAP_FLAG_CALLABLE_BY_LLM" in body,
                "root_only": "CLAW_CAP_FLAG_ROOT_AGENT_ONLY" in body,
                "description": description,
                "source": str(path.relative_to(ROOT)),
            }
        )
    return result


def provider_tools(
    sources: tuple[Path, ...], caller: str
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    visible: list[dict[str, object]] = []
    for path in sources:
        for descriptor in descriptors(path):
            if not descriptor["callable"]:
                continue
            if caller == "sub" and descriptor["root_only"]:
                continue
            visible.append(descriptor)
    serialized = [
        {
            "type": "function",
            "function": {
                "name": descriptor["name"],
                "description": descriptor["description"],
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for descriptor in visible
    ]
    return visible, json.loads(json.dumps(serialized, ensure_ascii=False))


class ProviderToolNameContractTest(unittest.TestCase):
    def setUp(self) -> None:
        default_sources = (
            BASE_ROOT_VISIBLE_SOURCES
            + (DECISION_CAPABILITIES, INTEGRATION_CAPABILITIES)
        )
        session_sources = (
            BASE_ROOT_VISIBLE_SOURCES
            + (
                DECISION_CAPABILITIES,
                ENVIRONMENT_CAPABILITIES,
                INTEGRATION_CAPABILITIES,
            )
        )
        self.root_default_descriptors, self.root_default = provider_tools(
            default_sources, "root"
        )
        self.root_session_descriptors, self.root_session = provider_tools(
            session_sources, "root"
        )
        self.sub_session_descriptors, self.sub_session = provider_tools(
            session_sources, "sub"
        )

    def assert_provider_names_valid_unique(
        self, tools: list[dict[str, object]]
    ) -> None:
        names = [tool["function"]["name"] for tool in tools]
        self.assertTrue(names)
        for name in names:
            self.assertIsInstance(name, str)
            self.assertTrue(name)
            self.assertRegex(name, PROVIDER_NAME_RE)
        duplicates = [
            name for name, count in Counter(names).items() if count > 1
        ]
        self.assertEqual(duplicates, [])

    def test_builder_serializes_descriptor_name_to_provider_function_name(
        self,
    ) -> None:
        builder = CAP_BUILDER[
            CAP_BUILDER.index("char *claw_cap_build_llm_tools_json") :
            CAP_BUILDER.index("esp_err_t claw_cap_call_from_core")
        ]
        self.assertIn(
            'cJSON_AddStringToObject(item, "name", slot->descriptor.name)',
            builder,
        )
        self.assertIn(
            'cJSON_AddStringToObject(function, "name", name->valuestring)',
            builder,
        )

    def test_root_default_provider_names_are_complete_valid_and_unique(
        self,
    ) -> None:
        self.assertEqual(len(self.root_default), 57)
        self.assert_provider_names_valid_unique(self.root_default)
        names = [tool["function"]["name"] for tool in self.root_default]
        self.assertEqual(names[46], "rehab_task_decision_formal_input_get")
        self.assertEqual(names[47], "rehab_task_decision_commit")
        self.assertEqual(names[48], "rehab_task_decision_status")
        self.assertNotIn("Rehab task decision", names)
        self.assertNotIn("Rehab task decision status", names)

    def test_root_decision_and_environment_session_is_valid_and_unique(
        self,
    ) -> None:
        self.assertEqual(len(self.root_session), 60)
        self.assert_provider_names_valid_unique(self.root_session)
        names = [tool["function"]["name"] for tool in self.root_session]
        for required in (
            "rehab_task_decision_formal_input_get",
            "rehab_task_decision_commit",
            "rehab_task_decision_status",
            "rehab_environment_scan_start",
            "rehab_environment_scan_status",
            "rehab_environment_scan_cancel",
        ):
            self.assertIn(required, names)

    def test_formal_decision_ids_equal_provider_names(self) -> None:
        formal = descriptors(DECISION_CAPABILITIES)
        self.assertEqual(
            [(item["id"], item["name"]) for item in formal],
            [
                (
                    "rehab_task_decision_formal_input_get",
                    "rehab_task_decision_formal_input_get",
                ),
                (
                    "rehab_task_decision_commit",
                    "rehab_task_decision_commit",
                ),
                (
                    "rehab_task_decision_status",
                    "rehab_task_decision_status",
                ),
            ],
        )

    def test_formal_descriptions_are_uncapped_and_preserved_by_provider(self) -> None:
        formal = {
            item["name"]: item for item in descriptors(DECISION_CAPABILITIES)
        }
        provider = {
            item["function"]["name"]: item["function"]
            for item in self.root_default
        }
        formal_input = formal[
            "rehab_task_decision_formal_input_get"
        ]["description"]
        commit = formal["rehab_task_decision_commit"]["description"]
        status = formal["rehab_task_decision_status"]["description"]
        self.assertLessEqual(len(formal_input.encode("utf-8")), 220)
        self.assertLessEqual(len(commit.encode("utf-8")), 220)
        self.assertLessEqual(len(status.encode("utf-8")), 220)
        self.assertIn("complete frozen", formal_input.lower())
        self.assertIn("call once", formal_input.lower())
        self.assertIn("machine facts override memory", formal_input.lower())
        self.assertIn("retry once", commit.lower())
        self.assertIn("retry_allowed=true", commit.lower())
        self.assertIn("never reread formal input", commit.lower())
        self.assertIn("explicitly asks", status.lower())
        self.assertIn("once", status.lower())
        self.assertIn("never start", status.lower())
        self.assertIn("more than once", status.lower())
        self.assertEqual(
            provider["rehab_task_decision_formal_input_get"]["description"],
            formal_input,
        )
        self.assertEqual(
            provider["rehab_task_decision_commit"]["description"], commit
        )
        self.assertEqual(
            provider["rehab_task_decision_status"]["description"], status
        )
        self.assertIn("#define CLAW_CAP_TOOL_DESCRIPTION_MAX 256", CAP_BUILDER)
        self.assertIn(
            "claw_cap_add_capped_description(item, "
            "slot->descriptor.description, slot->descriptor.name)",
            CAP_BUILDER,
        )

    def test_sub_agent_provider_excludes_formal_root_only_tools(self) -> None:
        self.assert_provider_names_valid_unique(self.sub_session)
        names = [tool["function"]["name"] for tool in self.sub_session]
        self.assertNotIn("rehab_task_decision_formal_input_get", names)
        self.assertNotIn("rehab_task_decision_commit", names)
        self.assertNotIn("rehab_task_decision_status", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
