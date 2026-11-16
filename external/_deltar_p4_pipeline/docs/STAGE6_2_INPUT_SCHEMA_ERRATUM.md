# Stage 6.2 Input Schema Erratum

Stage 6.2 corrects the formal JSON Schema representation of the existing P4 input contract. It does not add, remove, or rename a business field; change the scan directory layout, units, file names, CLI names, or input versions; or alter any model or algorithm.

The only P4 input contracts remain `deltar_scan_input.v1`, `deltar_scan_frame.v1`, and `rehab_tof_raw.v1`. All three machine schemas use JSON Schema Draft 2020-12 and reject unknown fields at the top level and in nested objects.

The original frame schema was unsatisfiable because it combined a required-field list and `additionalProperties=false` without declaring any `properties`. Stage 6.2 supplies those declarations, adds the previously missing ToF schema, and expands the scan `device` and `profiles` objects.

`created_at` is RFC3339 date-time text with either `Z` or an explicit UTC offset; naive date-times are rejected. `pair_delta_us` is the non-negative absolute difference between camera and ToF timestamps. A scan sequence starts at zero and is contiguous within that scan; it is not a provider-global ToF sequence.

P4 `rehab_scan_capture` must serialize exactly the declared fields and values. Extension fields are not permitted. The runtime validator owns constraints that one JSON document cannot express: directory-name identity, directory count, contiguous sequence, capture-mode consistency, payload size, frame/ToF sequence and timestamp equality, and the absolute pair delta.

The formal Stage 6 profile identifiers are centralized in `scan_contracts.py`; tests prove exact schema/runtime agreement. This document is the **Stage 6.2 schema erratum** and does not constitute a v2 contract.
