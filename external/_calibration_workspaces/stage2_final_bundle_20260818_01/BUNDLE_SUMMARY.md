# Stage2 Final Calibration Bundle Summary

Status: `CANDIDATE_NOT_PROMOTED`  
Promotion executed: `false`  
Stage3 started: `false`  
Bundle SHA-256: `63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057`

## Authority closure

- Stage2A: `C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json` (`99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b`)
- Stage2B camera↔ToF: `C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\camera_tof.stage2b_all16_candidate.v2.json` (`b518d878be175d42906dd17f9c7e9ead74f33eef77ad11165dc5b2aacd76543e`)
- Stage2B DELTAR `fr`: `C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\candidate\deltar_fr.stage2b_all16_candidate.v2.json` (`90c6d7d57f70178231843fbc000d6aedd23ec262837c14df1f62a5f6c762ec9c`)
- Stage2C status: `VALIDATION_EVIDENCE_READY`; its diagnostic comparison is not a hard promotion gate.

No fitting, refitting, candidate mutation, runtime switch, formal-chain promotion, or Stage3A work was performed.

## Calibration content

- Camera raster: `640×360`; OpenCV Brown-5.
- Intrinsics: `fx=868.6083708422947`, `fy=867.5423504351968`, `cx=301.64638304931754`, `cy=178.8685576885749`.
- Distortion: `k1=0.06470939303026187`, `k2=-0.07937107551146777`, `p1=0.00660415543035739`, `p2=-0.006674189708110315`, `k3=-0.7517780193167731`.
- Stage2A held-out RMS/P95/max: `0.157685` / `0.286514` / `0.630078` px.
- Camera-from-ToF convention: `p_camera = R * p_tof + t`, column vectors, millimetres.
- ToF measurement semantics: `axial_z`.
- Raw zone authority: raw index `0..63`, identity canonical gauge; physical D4 orientation remains `UNRESOLVED_GAUGE`.
- `fr`: `[64,4]`, `[sy,sx,ey,ex]`, source-camera pixels, continuous half-open model-derived bounds, reference tables at 400/600/1000 mm.

## Validation reading

- Stage2B core held-out: median `3.120` mm, P95 `9.043` mm, RMS `4.683` mm, max `19.972` mm.
- Stage2C independent core: median `4.716` mm, P95 `12.778` mm, RMS `6.702` mm, max `25.170` mm.
- Stage2C is worse than the Stage2B reference on the reported aggregate metrics, but shows no tens-to-hundreds-of-millimetres core geometry collapse. This is evidence for the decision owner, not an automatic pass or failure.

## Mechanical/world boundary

The old Stage7C camera-world pose predates the new vehicle mounting and is inventory only. It is not carried as authority in this bundle and must not be reused. Stage3A may begin only after the camera↔ToF assembly is mechanically fixed, adhesive has cured, and the Stage2 residual trade-off has explicit owner acceptance.
