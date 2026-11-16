# Scan Result Contract v1.1

`deltar_scan_result.v1.1` is the only formal future P4 scan-result contract. Stage 6 `deltar_scan_result.v1` remains inspect-only legacy data.

Each scan object strictly contains identity/class/scope, unique frame IDs, the best formal Stage 4.1 observation, visibility, distance, visible/physical/bottom height objects, placement, aggregation state, warnings, rejection state, and model/function provenance. Aggregation ignores observations without formal selected geometry and never combines top/bottom values across frames.

Single-canvas distance may remain provisional. Disagreed geometry is excluded. Absolute height flags are copied from formal Stage 4.1 validity and tightened by visibility requirements.
