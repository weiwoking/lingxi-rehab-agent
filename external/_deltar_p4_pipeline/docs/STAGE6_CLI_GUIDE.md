# Stage 6 CLI guide

Commands are non-interactive and print one short JSON status line. Detailed artifacts live under the output directory.

```powershell
python -m deltar_p4_pipeline.cli scan-validate --scan-dir C:\data\scan_0001 --profile-bundle profiles
python -m deltar_p4_pipeline.cli scan-run --scan-dir C:\data\scan_0001 --output-dir C:\data\scan_0001_result --profile-bundle profiles
python -m deltar_p4_pipeline.cli scan-inspect --result C:\data\scan_0001_result\result.json
python -m deltar_p4_pipeline.cli profile-validate --profile-bundle profiles
```

`scan-run` creates `result.json`, `job_report.json`, `frame_results/`, and `logs/`. The output directory must not already exist. `--overwrite=false` is the only supported overwrite mode. `--resume` is explicitly rejected because Stage 6 has no recovery transaction.

Exit codes: 0 success or a normal rejected scan; 2 input validation; 3 profile/model hash validation; 4 model inference; 5 frame processing; 6 aggregation; 7 output exists/write. A status `rejected` inside a valid result still exits 0 because the program completed its contract.
