# Stage 6 PC Packaging Plan

Stage 6 is planned but blocked by the Stage 5 PARTIAL verdict. When unblocked it will freeze: scan-directory input, batch CLI, immutable model/profile manifest, job states, `result.json`, cross-frame object aggregation, a PC-local service contract, and the future P4 upload contract.

It must preserve fail-closed GT separation, model hashes, calibration/profile identity, direct/letterbox semantics, structured error codes and per-stage timings. No FastAPI service, P4 upload, SD recorder, build, flash or monitor work is implemented in Stage 5.
