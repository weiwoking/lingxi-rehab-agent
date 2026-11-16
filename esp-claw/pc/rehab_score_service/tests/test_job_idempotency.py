import tempfile
import unittest

from _job_test_support import (
    SESSION_ID,
    csv_bytes,
    make_api,
    reservation_headers,
    reserve_and_upload,
    task_bytes,
)


class JobIdempotencyTests(unittest.TestCase):
    def test_identical_create_reuses_job_and_conflicts_do_not_overwrite(self):
        csv_data = csv_bytes()
        task_data = task_bytes()
        with tempfile.TemporaryDirectory() as temp:
            client, _, _ = make_api(temp)
            first = client.post("/api/v1/score/jobs", content=task_data, headers=reservation_headers(csv_data))
            second = client.post("/api/v1/score/jobs", content=task_data, headers=reservation_headers(csv_data))
            self.assertEqual(first.status_code, 201)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(first.json()["job_id"], second.json()["job_id"])
            changed = reservation_headers(csv_data)
            changed["x-csv-size"] = str(len(csv_data) + 1)
            conflict = client.post("/api/v1/score/jobs", content=task_data, headers=changed)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["error_code"], "session_content_conflict")
            changed_task_bytes = task_data + b" "
            conflict = client.post(
                "/api/v1/score/jobs",
                content=changed_task_bytes,
                headers=reservation_headers(csv_data),
            )
            self.assertEqual(conflict.status_code, 409)

    def test_duplicate_put_does_not_rewrite_accepted_input(self):
        data = csv_bytes()
        with tempfile.TemporaryDirectory() as temp:
            client, store, _ = make_api(temp)
            _, upload = reserve_and_upload(client, data)
            self.assertEqual(upload.status_code, 202)
            path = store.sessions_root / SESSION_ID / "input" / "DATA_SEQ0000000001.csv"
            before = path.stat().st_mtime_ns
            duplicate = client.put(
                f"/api/v1/score/jobs/{SESSION_ID}/csv",
                content=data,
                headers={"content-type": "text/csv"},
            )
            self.assertEqual(duplicate.status_code, 200)
            self.assertEqual(path.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
