import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cloud_interface.score_job_store import ScoreJobStore
from _job_test_support import SESSION_ID, csv_bytes, full_result, make_api, reserve_and_upload, task_bytes


class JobRecoveryTests(unittest.TestCase):
    def _accepted(self, temp):
        client, store, worker = make_api(temp)
        reserve_and_upload(client)
        return client, store, worker

    def test_worker_success_atomic_result_and_input_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            client, store, worker = self._accepted(temp)
            session = store.sessions_root / SESSION_ID
            task_hash = hashlib.sha256((session / "input" / "task.json").read_bytes()).hexdigest()
            csv_hash = hashlib.sha256((session / "input" / "DATA_SEQ0000000001.csv").read_bytes()).hexdigest()
            worker.process_one(SESSION_ID)
            status = store.read_status(SESSION_ID)
            self.assertEqual(status["state"], "succeeded")
            self.assertTrue((session / "runtime" / "job_state.json").is_file())
            self.assertFalse((session / "job_status.json").exists())
            self.assertTrue((session / "scoring" / "full_result.json").is_file())
            self.assertFalse(store.path_resolver.scoring_staging_session_path(SESSION_ID).exists())
            self.assertEqual(hashlib.sha256((session / "input" / "task.json").read_bytes()).hexdigest(), task_hash)
            self.assertEqual(hashlib.sha256((session / "input" / "DATA_SEQ0000000001.csv").read_bytes()).hexdigest(), csv_hash)
            compact = client.get(f"/api/v1/score/jobs/{SESSION_ID}/result")
            self.assertEqual(compact.status_code, 200, compact.text)
            self.assertEqual(compact.json()["schema_version"], "rehab_score_result_v1")
            recovered = ScoreJobStore(Path(temp))
            self.assertEqual(recovered.recover(), [])
            self.assertEqual(recovered.read_status(SESSION_ID)["state"], "succeeded")
            retry = client.post(f"/api/v1/score/jobs/{SESSION_ID}/retry")
            self.assertEqual(retry.status_code, 409)

    def test_failed_retry_reuses_job_and_increments_count(self):
        with tempfile.TemporaryDirectory() as temp:
            client, store, _ = self._accepted(temp)
            store.update_state(SESSION_ID, "failed", "synthetic_failure", "test")
            retry = client.post(f"/api/v1/score/jobs/{SESSION_ID}/retry")
            self.assertEqual(retry.status_code, 202, retry.text)
            self.assertEqual(retry.json()["job_id"], SESSION_ID)
            self.assertEqual(retry.json()["retry_count"], 1)

    def test_aggregate_public_result_succeeds_without_action_artifacts(self):
        def incomplete_scorer(**kwargs):
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True)
            payload = full_result()
            del payload["actions"]
            (output / "score_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return payload

        with tempfile.TemporaryDirectory() as temp:
            client, store, worker = make_api(temp, incomplete_scorer)
            reserve_and_upload(client)
            worker.process_one(SESSION_ID)
            self.assertEqual(store.read_status(SESSION_ID)["state"], "succeeded")
            self.assertTrue((store.sessions_root / SESSION_ID / "scoring").exists())
            result = client.get(f"/api/v1/score/jobs/{SESSION_ID}/result")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()), 20)
            self.assertNotIn("actions", result.json())

    def test_recovery_rules(self):
        for initial, expected, should_enqueue in (
            ("accepted", "accepted", True),
            ("scoring", "accepted", True),
            ("failed", "failed", False),
        ):
            with self.subTest(initial), tempfile.TemporaryDirectory() as temp:
                _, store, _ = self._accepted(temp)
                store.update_state(SESSION_ID, initial)
                if initial == "scoring":
                    store.path_resolver.scoring_staging_session_path(SESSION_ID).mkdir(parents=True)
                recovered = ScoreJobStore(Path(temp))
                queued = recovered.recover()
                self.assertEqual(recovered.read_status(SESSION_ID)["state"], expected)
                self.assertEqual(SESSION_ID in queued, should_enqueue)
                self.assertFalse(store.path_resolver.scoring_staging_session_path(SESSION_ID).exists())

    def test_recovery_commits_atomically_published_result_after_state_write_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            _, store, worker = self._accepted(temp)
            worker.process_one(SESSION_ID)
            store.update_state(SESSION_ID, "scoring", completed_at=None)

            recovered = ScoreJobStore(Path(temp))
            self.assertEqual(recovered.recover(), [])
            self.assertEqual(recovered.read_status(SESSION_ID)["state"], "succeeded")
            self.assertTrue(
                (recovered.path_resolver.session_scoring_path(SESSION_ID) / "full_result.json").is_file()
            )

    def test_result_fact_wins_over_failed_state(self):
        with tempfile.TemporaryDirectory() as temp:
            _, store, worker = self._accepted(temp)
            worker.process_one(SESSION_ID)
            store.update_state(SESSION_ID, "failed", "late_state_write", "synthetic")

            recovered = ScoreJobStore(Path(temp))
            self.assertEqual(recovered.recover(), [])
            self.assertEqual(recovered.read_status(SESSION_ID)["state"], "succeeded")

    def test_deleted_session_is_not_recovered_and_orphan_staging_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            client, store, _ = make_api(temp)
            create = client.post(
                "/api/v1/score/jobs",
                content=task_bytes(),
                headers={
                    "content-type": "application/json",
                    "x-session-id": SESSION_ID,
                    "x-csv-size": str(len(csv_bytes())),
                    "x-csv-sha256": hashlib.sha256(csv_bytes()).hexdigest(),
                },
            )
            self.assertEqual(create.status_code, 201)
            staging = store.path_resolver.score_staging_session_path(SESSION_ID)
            self.assertTrue(staging.is_dir())
            shutil.rmtree(store.path_resolver.resolve_score_session(SESSION_ID))

            recovered = ScoreJobStore(Path(temp))
            self.assertEqual(recovered.recover(), [])
            self.assertFalse(staging.exists())
            with self.assertRaises(Exception):
                recovered.read_status(SESSION_ID)

    def test_corrupt_session_does_not_block_recovery_of_valid_session(self):
        with tempfile.TemporaryDirectory() as temp:
            _, store, worker = self._accepted(temp)
            bad_runtime = Path(temp) / "sessions" / "20260714_TASK003" / "runtime"
            bad_runtime.mkdir(parents=True)
            (bad_runtime / "job_state.json").write_text("{broken", encoding="utf-8")

            recovered = ScoreJobStore(Path(temp))
            self.assertEqual(recovered.recover(), [SESSION_ID])
            worker.process_one("20260714_TASK003")
            worker.process_one(SESSION_ID)
            self.assertEqual(store.read_status(SESSION_ID)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
