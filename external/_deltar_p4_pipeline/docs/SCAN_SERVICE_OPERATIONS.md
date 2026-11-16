# PC Scan Service Operations

Start locally:

```powershell
& C:\ec\_conda_envs\vision_interpreter\python.exe `
  C:\ec\_deltar_p4_pipeline\scripts\run_scan_service.py `
  --host 127.0.0.1 --port 8030
```

For the future private P4 link, explicitly select the interface, for example `--host 192.168.137.1`. Phase A intentionally defaults to loopback and supplies neither authentication nor TLS.

The service uses Vision Python only for HTTP/storage/scheduling and always launches the frozen Python 3.11 executable for `scan-validate` and `scan-run`. The worker receives an explicit `PYTHONPATH`, a small Windows/CUDA allowlist, `PYTHONNOUSERSITE=1`, and no shell. Validation times out after 30 seconds; processing defaults to 180 seconds.

Data layout is `incoming/`, `validated/`, `results/`, and `failed/` below the configured data root. Uploads are chunk-written to `.tmp`, flushed and fsynced, then no-overwrite renamed. A validated upload is atomically renamed to `validated/<scan_id>`. No validated/result scan is overwritten.

On restart, valid completed result/job pairs are reconstructed. An incomplete validated scan becomes `stale_failed_no_resume`; interrupted `.uploading` directories move to `failed`. Jobs are never resumed or automatically retried. There is no database or persistent queue.

Use `scripts/test_scan_service_client.py --scan-dir <scan>` to POST, poll, download, and verify both IDs. Recommended polling is every 5 seconds with a total wait of at least 180 seconds. The current worker remains cold-started per job; YOLO/model startup is not resident.
