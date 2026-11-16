# Stage 5 Dataset Audit

Stage 5 uses three traceable branches. A is 14 analytic ray-intersection scenes with exact camera, depth, masks and object geometry. B is five analytic billboards whose chair appearance is cut from frozen DELTAR demo sample `official_000`; geometry remains analytic. C is the existing official DELTAR demo archive (287 HDF5 frames) used for official regression and appearance provenance.

No new public data was downloaded. The official NYU Depth V2 labeled file is about 2.8 GB and does not state a sufficiently explicit dataset license on its download page. SUN RGB-D's image archive is 6.4 GB. Hypersim is CC BY-SA 3.0, but its official image ZIPs are 1–20 GB and the complete release is about 1.9 TB. None met all Stage 5 gates: official source, direct sub-1-GB subset, RGB/depth/intrinsics, and explicit licensing.

Machine-readable URLs, decisions, sizes, hashes, and limitations are in `outputs_stage5/dataset_audit.json` and `asset_manifest.json`. Public RGB-D without a 3D mesh may only use `tof_simulation_geometry=co_located_approximation`; no translated-view parallax may be invented.
