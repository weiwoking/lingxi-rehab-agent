import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cloud_interface.score_job_store import JobError, ScoreJobStore, parse_task_bytes


SESSION_ID = "20260721_TASK002"
CSV_NAME = "DATA_SEQ0000000001.csv"


def task_bytes(task_name="患侧手臂前抬训练", **updates):
    task = {
        "task_name": task_name,
        "csv_file": CSV_NAME,
        "start_time": "2026-07-21T16:24:00+08:00",
        "end_time": "2026-07-21T16:25:14+08:00",
        "start_log_seq": 1,
        "end_log_seq": 1576,
        "end_reason": "user_stop",
    }
    task.update(updates)
    return json.dumps(task, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class TaskSupportContractTests(unittest.TestCase):
    def test_every_complete_task_name_is_preserved(self):
        for task_name in ("患侧手臂前抬训练", "喝水动作模拟", "坐位躯干控制训练"):
            with self.subTest(task_name):
                parsed = parse_task_bytes(task_bytes(task_name))
                self.assertEqual(parsed["task_name"], task_name)

    def test_contract_errors_remain_rejected(self):
        invalid = json.loads(task_bytes("喝水动作模拟").decode("utf-8"))
        invalid.pop("end_reason")
        with self.assertRaises(JobError) as raised:
            parse_task_bytes(json.dumps(invalid, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.error_code, "invalid_task_contract")

    def test_reservation_is_idempotent_conflict_safe_and_persistent(self):
        task = task_bytes()
        csv_sha256 = hashlib.sha256(b"frozen-csv-content").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ScoreJobStore(root)
            created, first = store.create_or_reuse(
                SESSION_ID, len(b"frozen-csv-content"), csv_sha256, task
            )
            reused, second = store.create_or_reuse(
                SESSION_ID, len(b"frozen-csv-content"), csv_sha256, task
            )
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(created["task_sha256"], reused["task_sha256"])

            restarted = ScoreJobStore(root)
            self.assertEqual(restarted.read_status(SESSION_ID)["state"], "uploading")

            with self.assertRaises(JobError) as raised:
                restarted.create_or_reuse(
                    SESSION_ID,
                    len(b"different-csv-content"),
                    hashlib.sha256(b"different-csv-content").hexdigest(),
                    task,
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.error_code, "session_content_conflict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
