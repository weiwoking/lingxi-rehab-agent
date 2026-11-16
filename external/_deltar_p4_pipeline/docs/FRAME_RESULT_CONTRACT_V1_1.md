# Frame Result Contract v1.1

`deltar_scan_frame_result.v1.1` is the final PC-to-P4 frame contract. Historical `v1` output is not upgraded implicitly. The scan input schemas remain `deltar_scan_input.v1`, `deltar_scan_frame.v1`, and `rehab_tof_raw.v1`.

Every frame records frozen model/profile provenance, instances, logical object groups, eroded-region evidence, formal Stage 4.1 attempts and canvas state, selected geometry, visibility, distance, three height objects, diagnostics, and processing data. Runtime validation rejects missing or misspelled nested fields, non-finite numbers, malformed hashes/codes, duplicate identities, and absolute paths.

Matched groups always retain both segmentation primary and detection fallback. Stage 4.1 selects segmentation first, then matched detection fallback. `disagreed` selects no geometry; a single canvas is provisional and never absolute-valid.
