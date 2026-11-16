from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

import backend.app.cloud_sync_service as module


class _FakeCloudHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/v1/cloud/health":
            self._respond(200, {"status": "ready"})
        elif self.path.startswith("/api/v1/cloud/sessions?"):
            self._respond(200, {"sessions": []})
        elif self.path.startswith("/api/v1/cloud/sessions/"):
            self._respond(200, {"session_id": self.path.rsplit("/", 1)[-1]})
        else:
            self._respond(404, {"message": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/v1/cloud/sessions":
            self._respond(201, {"session_id": "20260803_TASK001"})
        else:
            self._respond(404, {"message": "not_found"})

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        content_length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(content_length) if content_length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class CloudSyncServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="cloud-sync-test-"))
        self.originals = {
            "TRAINING_DATA_ROOT": module.TRAINING_DATA_ROOT,
            "CLOUD_SYNC_DIR": module.CLOUD_SYNC_DIR,
            "QUEUE_FILE": module.QUEUE_FILE,
            "TMP_DIR": module.TMP_DIR,
            "_monotonic": module._monotonic,
        }
        module.TRAINING_DATA_ROOT = self.temp_dir / "training_data"
        module.CLOUD_SYNC_DIR = self.temp_dir / "cloud_sync"
        module.QUEUE_FILE = module.CLOUD_SYNC_DIR / "queue.json"
        module.TMP_DIR = module.CLOUD_SYNC_DIR / "tmp"
        (module.TRAINING_DATA_ROOT / "sessions").mkdir(parents=True)
        module.CLOUD_SYNC_DIR.mkdir(parents=True)
        module.TMP_DIR.mkdir(parents=True)
        self.now = 1000.0
        module._monotonic = lambda: self.now
        self.service = module.CloudSyncService()
        self.service._config = self._config(enabled=True, stable_seconds=10)
        self.service._started = True
        self.test_servers: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def tearDown(self) -> None:
        for server, thread in self.test_servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        module.TRAINING_DATA_ROOT = self.originals["TRAINING_DATA_ROOT"]
        module.CLOUD_SYNC_DIR = self.originals["CLOUD_SYNC_DIR"]
        module.QUEUE_FILE = self.originals["QUEUE_FILE"]
        module.TMP_DIR = self.originals["TMP_DIR"]
        module._monotonic = self.originals["_monotonic"]
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _start_cloud_server(self) -> tuple[ThreadingHTTPServer, str]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCloudHandler)
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.test_servers.append((server, thread))
        host, port = server.server_address
        return server, f"http://{host}:{port}"

    @staticmethod
    def _broken_proxy_env() -> dict[str, str]:
        broken_proxy = "http://127.0.0.1:1"
        return {
            "HTTP_PROXY": broken_proxy,
            "HTTPS_PROXY": broken_proxy,
            "ALL_PROXY": broken_proxy,
            "http_proxy": broken_proxy,
            "https_proxy": broken_proxy,
            "all_proxy": broken_proxy,
            "NO_PROXY": "",
            "no_proxy": "",
        }

    def _service_for_server(self, base_url: str) -> module.CloudSyncService:
        service = module.CloudSyncService()
        service._config = self._config(enabled=True)
        service._config["ESPCLAW_CLOUD_BASE_URL"] = base_url
        return service

    def _config(self, enabled: bool, stable_seconds: int = 0) -> dict[str, str]:
        return {
            "ESPCLAW_CLOUD_SYNC_ENABLED": "true" if enabled else "false",
            "ESPCLAW_CLOUD_BASE_URL": "http://cloud.invalid",
            "ESPCLAW_CLOUD_DEVICE_ID": "TEST-PC",
            "ESPCLAW_CLOUD_API_TOKEN": "test-token",
            "ESPCLAW_CLOUD_SUBJECT_SALT": "test-salt",
            "ESPCLAW_CLOUD_TIMEOUT_SECONDS": "1",
            "ESPCLAW_CLOUD_FILE_STABLE_SECONDS": str(stable_seconds),
            "ESPCLAW_CLOUD_HEALTH_INTERVAL_SECONDS": "1",
        }

    def _session_dir(self, session_id: str, task_name: str = "喝水动作模拟") -> Path:
        session_dir = module.TRAINING_DATA_ROOT / "sessions" / session_id
        session_dir.mkdir(parents=True)
        input_dir = session_dir / "input"
        input_dir.mkdir()
        task = {
            "task_name": task_name,
            "csv_file": "DATA_SEQ0000000001.csv",
            "start_time": "2026-08-03T09:00:00+00:00",
            "end_time": "2026-08-03T09:01:00+00:00",
            "end_reason": "completed",
            "start_log_seq": 1,
            "end_log_seq": 3,
        }
        (input_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        (input_dir / "DATA_SEQ0000000001.csv").write_text(
            "log_seq,imu1_qw,imu1_qx,emg_latest_1\n1,1,0,0.1\n2,1,0,0.2\n",
            encoding="utf-8",
            newline="",
        )
        return session_dir

    def _stable_item(self, session_id: str = "20260803_TASK001", task_id: str = "task_alpha") -> dict[str, Any]:
        session_dir = self._session_dir(session_id)
        self.service.refresh_session_records(
            [
                {
                    "session_id": session_id,
                    "task_id": task_id,
                    "task_finalized": True,
                    "status": "COMPLETED",
                }
            ]
        )
        self.service._discover_sessions()
        self.now += 11
        self.service._discover_sessions()
        return self.service._queue[session_id]

    def _complete_upload(self, status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
        item = self._stable_item()
        self.service._upload_multipart = lambda metadata, gz_path: (status_code, payload)  # type: ignore[method-assign]
        self.service._upload(item)
        return item

    def test_200_success_marks_synced_and_does_not_fall_through(self) -> None:
        item = self._complete_upload(200, {"id": "remote-1", "uploaded_at": "2026-08-03T09:02:00+00:00"})
        self.assertEqual(item["status"], module.STATE_SYNCED)
        self.assertIsNone(item.get("last_error"))
        self.assertIsNone(item.get("next_retry_at"))
        self.assertEqual(item.get("remote_record_id"), "remote-1")

    def test_201_success_marks_synced_and_does_not_retry(self) -> None:
        item = self._complete_upload(201, {"session_id": "20260803_TASK001"})
        self.assertEqual(item["status"], module.STATE_SYNCED)
        self.assertIsNone(item.get("last_error"))
        self.assertIsNone(item.get("next_retry_at"))

    def test_409_same_sha_idempotent_response_marks_synced(self) -> None:
        item = self._stable_item()
        self.service._upload_multipart = lambda metadata, gz_path: (  # type: ignore[method-assign]
            409,
            {
                "detail": {
                    "error_code": "session_already_exists",
                    "session_id": item["session_id"],
                    "csv_sha256": metadata["csv_sha256"],
                }
            },
        )
        self.service._upload(item)
        self.assertEqual(item["status"], module.STATE_SYNCED)
        self.assertIsNone(item.get("last_error"))

    def test_409_different_sha_marks_conflict(self) -> None:
        item = self._stable_item()
        payload = {
            "detail": {
                "error_code": "session_already_exists",
                "session_id": item["session_id"],
                "csv_sha256": "different",
            }
        }
        self.service._upload_multipart = lambda metadata, gz_path: (409, payload)  # type: ignore[method-assign]
        self.service._upload(item)
        self.assertEqual(item["status"], module.STATE_CONFLICT)
        self.assertEqual(item.get("last_error"), "remote_session_sha_conflict")
        self.assertIsNone(item.get("next_retry_at"))

    def test_cloud_offline_manual_upload_does_not_attempt_or_fail(self) -> None:
        item = self._stable_item()
        attempts = {"count": 0}
        self.service._request_json = lambda method, path: (_ for _ in ()).throw(OSError("offline"))  # type: ignore[method-assign]
        self.service._upload_multipart = lambda metadata, gz_path: attempts.__setitem__("count", attempts["count"] + 1)  # type: ignore[method-assign]
        result = self.service.request_upload(item["session_id"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "cloud_offline")
        self.assertEqual(item["status"], module.STATE_NOT_UPLOADED)
        self.assertEqual(item["attempt_count"], 0)
        self.assertEqual(attempts["count"], 0)

    def test_manual_upload_success_flows_through_queue(self) -> None:
        item = self._stable_item()
        self.service._cloud_online = True
        self.service._check_cloud_health = lambda force=False: setattr(self.service, "_cloud_online", True)  # type: ignore[method-assign]
        self.service._upload_multipart = lambda metadata, gz_path: (200, {"session_id": item["session_id"]})  # type: ignore[method-assign]
        result = self.service.request_upload(item["session_id"])
        self.assertTrue(result["accepted"])
        item = self.service._queue[item["session_id"]]
        self.assertEqual(item["status"], module.STATE_QUEUED)
        self.service._process_uploads()
        self.assertEqual(item["status"], module.STATE_SYNCED)

    def test_batch_upload_only_queues_eligible_records(self) -> None:
        first = self._stable_item("20260803_TASK001")
        second = self._stable_item("20260803_TASK002")
        third = self._stable_item("20260803_TASK003")
        second["status"] = module.STATE_NOT_READY
        second["last_error"] = "active_session"
        self.service._active_session_ids.add(second["session_id"])
        third["status"] = module.STATE_CONFLICT
        self.service._cloud_online = True
        self.service._check_cloud_health = lambda force=False: setattr(self.service, "_cloud_online", True)  # type: ignore[method-assign]
        result = self.service.upload_pending()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["queued_count"], 1)
        self.assertEqual(self.service._queue[first["session_id"]]["status"], module.STATE_QUEUED)
        self.assertEqual(self.service._queue[second["session_id"]]["status"], module.STATE_NOT_READY)
        self.assertEqual(self.service._queue[third["session_id"]]["status"], module.STATE_CONFLICT)

    def test_auto_upload_is_off_by_default_and_not_persistent(self) -> None:
        self.assertFalse(self.service.auto_upload_enabled)
        self.service.set_auto_upload(True)
        self.assertTrue(self.service.auto_upload_enabled)
        restarted = module.CloudSyncService()
        restarted._config = self._config(enabled=True)
        self.assertFalse(restarted.auto_upload_enabled)

    def test_auto_upload_processes_when_online_and_waits_when_offline(self) -> None:
        item = self._stable_item()
        self.service.set_auto_upload(True)
        self.service._cloud_online = False
        self.service._process_uploads()
        self.assertEqual(item["status"], module.STATE_NOT_UPLOADED)
        self.service._cloud_online = True
        self.service._upload_multipart = lambda metadata, gz_path: (200, {"session_id": item["session_id"]})  # type: ignore[method-assign]
        self.service._process_uploads()
        self.assertEqual(item["status"], module.STATE_SYNCED)

    def test_task_id_resolution_uses_real_metadata_and_missing_is_unknown(self) -> None:
        session_dir = self._session_dir("20260803_TASK010")
        self.assertEqual(
            module.CloudSyncService.resolve_cloud_task_id(session_dir, {"task_id": "task_alpha"}, None),
            "task_alpha",
        )
        self.assertEqual(
            module.CloudSyncService.resolve_cloud_task_id(session_dir, {"task_type": "task_beta"}, None),
            "task_beta",
        )
        self.assertEqual(
            module.CloudSyncService.resolve_cloud_task_id(session_dir, {}, {"task_name": "喝水动作模拟"}),
            "drink_water_right",
        )
        self.assertEqual(module.CloudSyncService.resolve_cloud_task_id(session_dir, {}, {"task_name": "未登记任务"}), "unknown")

    def test_growing_csv_stays_not_ready_until_two_stable_observations(self) -> None:
        session_id = "20260803_TASK004"
        session_dir = self._session_dir(session_id)
        self.service.refresh_session_records([{"session_id": session_id, "task_id": "task_a", "task_finalized": True}])
        self.service._discover_sessions()
        self.assertEqual(self.service._queue[session_id]["status"], module.STATE_NOT_READY)
        self.now += 5
        csv_path = session_dir / "input" / "DATA_SEQ0000000001.csv"
        with csv_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write("3,1,0,0.3\n")
        self.service._discover_sessions()
        self.assertEqual(self.service._queue[session_id]["status"], module.STATE_NOT_READY)
        self.now += 11
        self.service._discover_sessions()
        self.assertEqual(self.service._queue[session_id]["status"], module.STATE_NOT_UPLOADED)

    def test_stable_finalized_csv_becomes_not_uploaded(self) -> None:
        item = self._stable_item("20260803_TASK005", task_id="task_book")
        self.assertEqual(item["status"], module.STATE_NOT_UPLOADED)
        self.assertEqual(item["task_id"], "task_book")

    def test_active_session_is_not_uploadable(self) -> None:
        session_id = "20260803_TASK006"
        self._session_dir(session_id)
        self.service.refresh_session_records([{"session_id": session_id, "task_id": "task_a", "status": "RECORDING"}])
        self.service._discover_sessions()
        self.now += 11
        self.service._discover_sessions()
        self.assertEqual(self.service._queue[session_id]["status"], module.STATE_NOT_READY)
        self.assertEqual(self.service._queue[session_id]["last_error"], "active_session")

    def test_uploading_state_recovers_on_startup(self) -> None:
        self._session_dir("20260803_TASK007")
        module.QUEUE_FILE.write_text(
            json.dumps(
                {
                    "sessions": {
                        "20260803_TASK007": {
                            "session_id": "20260803_TASK007",
                            "status": module.STATE_UPLOADING,
                            "attempt_count": 1,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        service = module.CloudSyncService()
        service._load_queue()
        item = service._queue["20260803_TASK007"]
        self.assertEqual(item["status"], module.STATE_FAILED)
        self.assertEqual(item["last_error"], "upload_interrupted")

    def test_deleted_session_is_pruned_and_cannot_be_recovered_from_queue(self) -> None:
        session_id = "20260803_TASK008"
        item = self._stable_item(session_id)
        self.assertIn(session_id, self.service._queue)
        shutil.rmtree(module.TRAINING_DATA_ROOT / "sessions" / session_id)

        self.service._discover_sessions()

        self.assertNotIn(session_id, self.service._queue)
        persisted = json.loads(module.QUEUE_FILE.read_text(encoding="utf-8"))
        self.assertNotIn(session_id, persisted["sessions"])
        self.assertEqual(
            self.service.request_upload(session_id)["reason"],
            "session_not_found",
        )

    def test_queue_load_skips_ghost_session(self) -> None:
        session_id = "20260803_TASK009"
        module.QUEUE_FILE.write_text(
            json.dumps(
                {
                    "sessions": {
                        session_id: {
                            "session_id": session_id,
                            "status": module.STATE_QUEUED,
                            "csv_file": "DATA_SEQ0000000001.csv",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        service = module.CloudSyncService()
        service._load_queue()

        self.assertNotIn(session_id, service._queue)

    def test_loaded_queue_rebinds_legacy_csv_path_to_unified_input(self) -> None:
        session_id = "20260803_TASK007"
        session_dir = self._session_dir(session_id)
        csv_file = "DATA_SEQ0000000001.csv"
        module.QUEUE_FILE.write_text(
            json.dumps(
                {
                    "sessions": {
                        session_id: {
                            "session_id": session_id,
                            "status": module.STATE_SYNCED,
                            "csv_file": csv_file,
                            "csv_path": r"C:\legacy\runtime_data\sessions\old.csv",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        service = module.CloudSyncService()
        service._load_queue()

        self.assertEqual(
            Path(service._queue[session_id]["csv_path"]),
            (session_dir / "input" / csv_file).resolve(),
        )

    def test_feature_disabled_never_checks_health_or_uploads(self) -> None:
        item = self._stable_item()
        self.service._config = self._config(enabled=False)
        calls = {"health": 0}
        self.service._request_json = lambda method, path: calls.__setitem__("health", calls["health"] + 1)  # type: ignore[method-assign]
        result = self.service.request_upload(item["session_id"])
        auto = self.service.set_auto_upload(True)
        self.assertEqual(result["reason"], "feature_disabled")
        self.assertFalse(auto["auto_upload_enabled"])
        self.assertFalse(self.service.auto_upload_enabled)
        self.assertEqual(calls["health"], 0)

    def test_discovery_and_summary_do_not_hash_or_scan_csv(self) -> None:
        session_id = "20260803_TASK011"
        self._session_dir(session_id)
        self.service.refresh_session_records([{"session_id": session_id, "task_id": "task_a", "task_finalized": True}])
        self.service._sha256_file = lambda path: (_ for _ in ()).throw(AssertionError("sha should be lazy"))  # type: ignore[method-assign]
        self.service._csv_metadata = lambda path: (_ for _ in ()).throw(AssertionError("csv scan should be lazy"))  # type: ignore[method-assign]
        self.service._discover_sessions()
        self.now += 11
        self.service._discover_sessions()
        summary = self.service.summary()
        item = self.service._queue[session_id]
        self.assertEqual(summary["not_uploaded_count"], 1)
        self.assertEqual(item["status"], module.STATE_NOT_UPLOADED)
        self.assertNotIn("csv_sha256", item)
        self.assertNotIn("row_count", item)

    def test_upload_analysis_is_cached_when_file_does_not_change(self) -> None:
        item = self._stable_item("20260803_TASK012")
        original_sha = self.service._sha256_file
        original_metadata = self.service._csv_metadata
        calls = {"sha": 0, "metadata": 0}

        def counted_sha(path: Path) -> str:
            calls["sha"] += 1
            return original_sha(path)

        def counted_metadata(path: Path) -> tuple[int, int, int]:
            calls["metadata"] += 1
            return original_metadata(path)

        self.service._sha256_file = counted_sha  # type: ignore[method-assign]
        self.service._csv_metadata = counted_metadata  # type: ignore[method-assign]
        self.service._upload_multipart = lambda metadata, gz_path: (503, {"message": "temporary"})  # type: ignore[method-assign]
        self.service._upload(item)
        self.assertEqual(calls, {"sha": 1, "metadata": 1})
        item["status"] = module.STATE_QUEUED
        self.service._upload(item)
        self.assertEqual(calls, {"sha": 1, "metadata": 1})

    def test_health_ignores_broken_process_proxy(self) -> None:
        server, base_url = self._start_cloud_server()
        with mock.patch.dict(os.environ, self._broken_proxy_env(), clear=False):
            service = self._service_for_server(base_url)
            status, payload = service._request_json("GET", "/api/v1/cloud/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "ready")
        self.assertEqual(server.requests[-1]["path"], "/api/v1/cloud/health")  # type: ignore[attr-defined]
        self.assertIsNone(server.requests[-1]["authorization"])  # type: ignore[attr-defined]

    def test_upload_ignores_broken_process_proxy_and_keeps_authorization(self) -> None:
        server, base_url = self._start_cloud_server()
        gz_path = self.temp_dir / "synthetic.csv.gz"
        gz_path.write_bytes(b"synthetic-test-data")
        with mock.patch.dict(os.environ, self._broken_proxy_env(), clear=False):
            service = self._service_for_server(base_url)
            status, payload = service._upload_multipart({"session_id": "20260803_TASK001"}, gz_path)

        request = server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(status, 201)
        self.assertEqual(payload.get("session_id"), "20260803_TASK001")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/v1/cloud/sessions")
        self.assertEqual(request["authorization"], "Bearer test-token")
        self.assertIn(b"synthetic-test-data", request["body"])

    def test_remote_list_and_get_ignore_proxy_and_keep_authorization(self) -> None:
        server, base_url = self._start_cloud_server()
        with mock.patch.dict(os.environ, self._broken_proxy_env(), clear=False):
            service = self._service_for_server(base_url)
            list_status, list_payload = service._request_json(
                "GET",
                "/api/v1/cloud/sessions?limit=1",
                authenticated=True,
            )
            get_status, get_payload = service._request_json(
                "GET",
                "/api/v1/cloud/sessions/20260803_TASK001",
                authenticated=True,
            )

        self.assertEqual((list_status, list_payload), (200, {"sessions": []}))
        self.assertEqual(get_status, 200)
        self.assertEqual(get_payload.get("session_id"), "20260803_TASK001")
        requests = server.requests[-2:]  # type: ignore[attr-defined]
        self.assertEqual(
            [request["path"] for request in requests],
            [
                "/api/v1/cloud/sessions?limit=1",
                "/api/v1/cloud/sessions/20260803_TASK001",
            ],
        )
        self.assertTrue(all(request["authorization"] == "Bearer test-token" for request in requests))

    def test_direct_transport_is_instance_scoped_and_leaves_global_state_unchanged(self) -> None:
        previous_global_opener = urllib.request._opener
        previous_urlopen = urllib.request.urlopen
        proxy_env = self._broken_proxy_env()
        with mock.patch.dict(os.environ, proxy_env, clear=False):
            environment_before = {key: os.environ.get(key) for key in proxy_env}
            service = module.CloudSyncService()
            environment_after = {key: os.environ.get(key) for key in proxy_env}
            default_proxy_handler = urllib.request.ProxyHandler()

        direct_proxy_handlers = [
            handler
            for handler in service._direct_opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        # An empty ProxyHandler suppresses the default environment-backed
        # handler and registers no per-scheme proxy hooks in the opener.
        self.assertEqual(direct_proxy_handlers, [])
        self.assertEqual(default_proxy_handler.proxies.get("http"), proxy_env["HTTP_PROXY"])
        self.assertEqual(environment_after, environment_before)
        self.assertIs(urllib.request._opener, previous_global_opener)
        self.assertIs(urllib.request.urlopen, previous_urlopen)


if __name__ == "__main__":
    unittest.main()
