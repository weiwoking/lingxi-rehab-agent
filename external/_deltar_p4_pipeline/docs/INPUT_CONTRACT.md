# Input contract

Versioned sample identity is the sample directory name, never a file modification time. Every directory contains exactly the required facts `image.rgb565`, `tof.json`, and `profile.json` (extra provenance files are allowed).

## Image

`image.rgb565` is exactly 460,800 bytes: width 640, height 360, stride 1,280 bytes, tightly packed `RGB565_LE`. The decoder expands R5/G6/B5 by bit replication to an internal H×W×3 `uint8` **RGB** array. Size, stride, endian, channel order, and orientation are never guessed.

## Raw ToF JSON

`schema_version` is `rehab_tof_raw.v1`. `sequence` and `timestamp_us` are non-negative JSON integers. `distance_mm` and `range_sigma_mm` are flat finite non-negative numeric arrays; `target_status` is 0..255 integers and `nb_target_detected` is non-negative integers. All four arrays have exactly 64 entries. Strings, booleans as integers, nested arrays, implicit truncation, NaN, and Infinity are rejected.

`range_sigma_mm` is a standard deviation. It is never squared. At the raw P4 fact layer it may be zero; a zero distance or sigma does not implicitly define the mask. After the profile produces the effective mask, every `mask=true` zone must have `sigma_mm > 0`, because the official `Normal(mu, sigma)` requires positive scale. A masked zone may retain sigma zero. No valid zones yields `E_NO_VALID_ZONES`; non-positive sigma in a valid zone yields `E_EFFECTIVE_SIGMA_NONPOSITIVE`. No clamp or epsilon is inserted.

`tof.json`, `profile.json`, and optional `source_metadata.json` are all parsed as strict JSON. Decode errors, NaN, and Infinity are classified rather than leaking raw decoder exceptions.

## Profile boundary and versioning

`profile.json` is PC policy, not a P4 sensor fact. Its schema is `deltar_p4_profile.v1` and all strategies are explicit. Unknown schema versions or strategies fail closed. Candidate mappings are labelled `PLAUSIBLE_BUT_UNPROVEN`; official-demo values are labelled `EXPLICIT_REFERENCE`.
