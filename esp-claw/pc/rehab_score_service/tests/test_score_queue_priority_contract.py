import threading
import time
import unittest

from cloud_interface.score_job_worker import QUEUE_MAX_JOBS, ScoreJobWorker


class RecordingWorker(ScoreJobWorker):
    def __init__(self):
        super().__init__(store=None, scorer=lambda **kwargs: {})
        self.processed: list[str] = []
        self.started: list[str] = []
        self._processed_event = threading.Event()
        self._block_event = threading.Event()
        self.block_on: str | None = None

    def process_one(self, job_id: str) -> None:
        self.started.append(job_id)
        if job_id == self.block_on:
            self._block_event.wait(timeout=2)
        self.processed.append(job_id)
        self._processed_event.set()

    def wait_for_count(self, count: int) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            if len(self.processed) >= count:
                return
            self._processed_event.wait(timeout=0.05)
            self._processed_event.clear()
        self.fail(f"worker processed {len(self.processed)} jobs, expected {count}")

    def fail(self, message: str) -> None:
        raise AssertionError(message)


class ScoreQueuePriorityContractTests(unittest.TestCase):
    def test_live_jobs_are_processed_before_recovery_backlog(self) -> None:
        worker = RecordingWorker()
        self.assertTrue(worker.enqueue("RECOVERY001", source="recovery"))
        self.assertTrue(worker.enqueue("RECOVERY002", source="recovery"))
        self.assertTrue(worker.enqueue("LIVE001"))
        worker.start()
        try:
            worker.wait_for_count(3)
        finally:
            worker.stop()
        self.assertEqual(worker.processed, ["LIVE001", "RECOVERY001", "RECOVERY002"])

    def test_live_fifo_and_recovery_fifo_are_preserved(self) -> None:
        worker = RecordingWorker()
        for job_id in ("RECOVERY001", "RECOVERY002"):
            self.assertTrue(worker.enqueue(job_id, source="recovery"))
        for job_id in ("LIVE001", "LIVE002"):
            self.assertTrue(worker.enqueue(job_id))
        worker.start()
        try:
            worker.wait_for_count(4)
        finally:
            worker.stop()
        self.assertEqual(worker.processed, ["LIVE001", "LIVE002", "RECOVERY001", "RECOVERY002"])

    def test_recovery_backlog_does_not_consume_live_capacity(self) -> None:
        worker = RecordingWorker()
        for index in range(QUEUE_MAX_JOBS * 2):
            self.assertTrue(worker.enqueue(f"RECOVERY{index:03d}", source="recovery"))
        for index in range(QUEUE_MAX_JOBS):
            self.assertTrue(worker.enqueue(f"LIVE{index:03d}"))
        self.assertFalse(worker.enqueue("LIVE_OVERFLOW"))
        snapshot = worker.snapshot()
        self.assertEqual(snapshot["live_queue_depth"], QUEUE_MAX_JOBS)
        self.assertEqual(snapshot["recovery_backlog"], QUEUE_MAX_JOBS * 2)

    def test_recovery_job_can_be_promoted_to_live_without_duplicate_execution(self) -> None:
        worker = RecordingWorker()
        self.assertTrue(worker.enqueue("JOB001", source="recovery"))
        self.assertTrue(worker.enqueue("JOB002", source="recovery"))
        self.assertTrue(worker.enqueue("JOB001"))
        worker.start()
        try:
            worker.wait_for_count(2)
        finally:
            worker.stop()
        self.assertEqual(worker.processed, ["JOB001", "JOB002"])

    def test_duplicate_live_enqueue_is_not_executed_twice(self) -> None:
        worker = RecordingWorker()
        self.assertTrue(worker.enqueue("LIVE001"))
        self.assertTrue(worker.enqueue("LIVE001"))
        worker.start()
        try:
            worker.wait_for_count(1)
        finally:
            worker.stop()
        self.assertEqual(worker.processed, ["LIVE001"])

    def test_active_recovery_can_delay_one_live_but_not_backlog(self) -> None:
        worker = RecordingWorker()
        worker.block_on = "RECOVERY001"
        self.assertTrue(worker.enqueue("RECOVERY001", source="recovery"))
        self.assertTrue(worker.enqueue("RECOVERY002", source="recovery"))
        worker.start()
        try:
            deadline = time.time() + 2
            while "RECOVERY001" not in worker.started and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(worker.enqueue("LIVE001"))
            worker._block_event.set()
            worker.wait_for_count(3)
        finally:
            worker.stop()
        self.assertEqual(worker.processed, ["RECOVERY001", "LIVE001", "RECOVERY002"])

    def test_snapshot_exposes_lightweight_queue_state(self) -> None:
        worker = RecordingWorker()
        worker.enqueue("LIVE001")
        worker.enqueue("RECOVERY001", source="recovery")
        snapshot = worker.snapshot()
        self.assertFalse(snapshot["running"])
        self.assertIsNone(snapshot["active_job_id"])
        self.assertEqual(snapshot["live_queue_depth"], 1)
        self.assertEqual(snapshot["recovery_backlog"], 1)
        self.assertEqual(snapshot["queued_total"], 2)


if __name__ == "__main__":
    unittest.main()
