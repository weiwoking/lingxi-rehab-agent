# Host tests

The implementation handoff's `SIMULATION_TEST.py` mirrors scan enumeration,
multipart encoding, service polling, result bounds/identity validation, abort,
and no-overwrite storage without requiring ESP-IDF or hardware.

`test_start_plan_allocation.py` extracts the real
`rehab_scan_transfer_start()` implementation and runs it in a compiled host
harness with mocked heap capabilities, locks, validation, and task creation.
It covers PSRAM success, internal-RAM fallback, double allocation failure,
every post-allocation error exit, complete plan copy before worker creation,
and exactly-once cleanup.

`test_result_contract.py` compiles the real
`rehab_scan_transfer_contract.c` with the vendored cJSON implementation. It
covers the PC v1.1 nullable height matrix, non-finite/negative values, exact
height fields and types, `absolute_valid` relationships, a one-object
`W_UNMEASURED_CALIBRATION` result, and the existing result regressions.

Run `run_host_tests.ps1 -Script <absolute SIMULATION_TEST.py path>` with the
frozen pipeline Python environment. The runner executes the result-contract
and start-plan host tests before the original 15-case simulation. Firmware
build/flash is not part of this test.
