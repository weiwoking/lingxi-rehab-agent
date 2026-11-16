import hashlib
import inspect
import json
import tempfile
import unittest

from cloud_interface import score_job_api
from cloud_interface.score_job_store import CSV_MAX_BYTES, CSV_HEADER
from _job_test_support import (
    SESSION_ID,
    csv_bytes,
    make_api,
    reservation_headers,
    reserve_and_upload,
    task_bytes,
)


class JobApiContractTests(unittest.TestCase):
    def test_any_valid_task_name_reserves_without_a_scoring_whitelist(self):
        data = csv_bytes()
        for task_name in ("患侧手臂前抬训练", "喝水动作模拟", "坐位躯干控制训练"):
            with self.subTest(task_name), tempfile.TemporaryDirectory() as temp:
                client, store, _ = make_api(temp)
                response = client.post(
                    "/api/v1/score/jobs",
                    content=task_bytes(task_name=task_name),
                    headers=reservation_headers(data),
                )
                self.assertEqual(response.status_code, 201, response.text)
                self.assertTrue((store.staging_root / SESSION_ID).is_dir())
                status = store.read_status(SESSION_ID)
                self.assertEqual(status["task_name"], task_name)
                self.assertNotIn("task_number", status)
                self.assertNotIn("target_repetition_count", status)

    def test_invalid_seven_field_contract_stays_rejected(self):
        data = csv_bytes()
        invalid = json.loads(task_bytes().decode("utf-8"))
        invalid.pop("end_reason")
        with tempfile.TemporaryDirectory() as temp:
            client, _, _ = make_api(temp)
            response = client.post(
                "/api/v1/score/jobs",
                content=json.dumps(invalid, ensure_ascii=False).encode("utf-8"),
                headers=reservation_headers(data),
            )
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error_code"], "invalid_task_contract")

    def test_create_stream_upload_status_and_not_ready_result(self):
        with tempfile.TemporaryDirectory() as temp:
            client, store, _ = make_api(temp)
            create, upload = reserve_and_upload(client)
            self.assertEqual(create.status_code, 201, create.text)
            self.assertEqual(upload.status_code, 202, upload.text)
            self.assertEqual(upload.json()["state"], "accepted")
            status = client.get(f"/api/v1/score/jobs/{SESSION_ID}")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["schema_version"], "rehab_score_job_status_v1")
            result = client.get(f"/api/v1/score/jobs/{SESSION_ID}/result")
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["error_code"], "result_not_ready")
            self.assertTrue((store.sessions_root / SESSION_ID).is_dir())

    def test_limits_and_invalid_identifiers(self):
        data = csv_bytes()
        with tempfile.TemporaryDirectory() as temp:
            client, _, _ = make_api(temp)
            oversized = client.post(
                "/api/v1/score/jobs",
                content=b"x" * (16 * 1024 + 1),
                headers=reservation_headers(data),
            )
            self.assertEqual(oversized.status_code, 413)
            too_large = reservation_headers(data)
            too_large["x-csv-size"] = str(CSV_MAX_BYTES + 1)
            response = client.post("/api/v1/score/jobs", content=task_bytes(), headers=too_large)
            self.assertEqual(response.status_code, 413)
            invalid = reservation_headers(data)
            invalid["x-session-id"] = "../TASK002"
            response = client.post("/api/v1/score/jobs", content=task_bytes(), headers=invalid)
            self.assertEqual(response.status_code, 422)
            invalid_task = task_bytes().replace(b"DATA_SEQ0000000001.csv", b"DATA_SEQ1.csv")
            response = client.post(
                "/api/v1/score/jobs",
                content=invalid_task,
                headers=reservation_headers(data),
            )
            self.assertEqual(response.status_code, 422)

    def test_waiting_queue_is_bounded_at_eight(self):
        data = csv_bytes()
        with tempfile.TemporaryDirectory() as temp:
            client, _, _ = make_api(temp)
            for index in range(1, 10):
                session_id = f"20260714_TASK{index:03d}"
                headers = reservation_headers(data)
                headers["x-session-id"] = session_id
                created = client.post(
                    "/api/v1/score/jobs", content=task_bytes(), headers=headers
                )
                self.assertEqual(created.status_code, 201)
                uploaded = client.put(
                    f"/api/v1/score/jobs/{session_id}/csv",
                    content=data,
                    headers={"content-type": "text/csv"},
                )
                self.assertEqual(uploaded.status_code, 202 if index <= 8 else 503)
            invalid = reservation_headers(data)
            invalid["x-csv-sha256"] = "A" * 64
            response = client.post("/api/v1/score/jobs", content=task_bytes(), headers=invalid)
            self.assertEqual(response.status_code, 422)

    def test_hash_size_header_and_sequence_fail_without_formal_session(self):
        cases = []
        good = csv_bytes()
        cases.append((good + b"x", good, "csv_size_mismatch"))
        wrong_hash = bytearray(good)
        wrong_hash[-3] = ord("1") if wrong_hash[-3] != ord("1") else ord("2")
        cases.append((bytes(wrong_hash), good, "csv_sha256_mismatch"))
        bad_header = csv_bytes(header=CSV_HEADER.replace("log_seq", "bad_seq", 1))
        cases.append((bad_header, bad_header, "invalid_csv_header"))
        bad_seq = csv_bytes(3, 4)
        cases.append((bad_seq, bad_seq, "csv_log_seq_mismatch"))
        for uploaded, reserved, expected_code in cases:
            with self.subTest(expected_code), tempfile.TemporaryDirectory() as temp:
                client, store, _ = make_api(temp)
                headers = reservation_headers(reserved)
                if expected_code == "csv_size_mismatch":
                    headers["x-csv-size"] = str(len(good))
                created = client.post("/api/v1/score/jobs", content=task_bytes(), headers=headers)
                self.assertEqual(created.status_code, 201)
                response = client.put(
                    f"/api/v1/score/jobs/{SESSION_ID}/csv",
                    content=uploaded,
                    headers={"content-type": "text/csv"},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error_code"], expected_code)
                self.assertFalse((store.sessions_root / SESSION_ID).exists())

    def test_csv_route_uses_request_stream_and_never_request_body(self):
        source = inspect.getsource(score_job_api)
        self.assertIn("request.stream()", source)
        self.assertNotIn("request.body", source)


if __name__ == "__main__":
    unittest.main()
