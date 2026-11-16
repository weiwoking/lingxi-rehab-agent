# Candidate Survival Audit

R0 is the complete selector-input list recovered by read-only replay instrumentation; R0S is diagnostics only and may be truncated. The row-level JSON preserves cardinality and ±10/20/30/50 evidence.

- G001: frame-level R0 ±50 presence=100.0%; first-loss={'SURVIVED_TO_CURRENT_OUTPUT': 21, 'FINAL_SELECTOR': 1}
- G002: frame-level R0 ±50 presence=100.0%; first-loss={'SURVIVED_TO_CURRENT_OUTPUT': 22}
- SCAN_070604: frame-level R0 ±50 presence=100.0%; first-loss={'RAW_CANDIDATE_GENERATION': 12, 'HYPOTHESIS_CONSTRUCTION': 13, 'SURVIVED_TO_CURRENT_OUTPUT': 8, 'FINAL_SELECTOR': 1}
- SCAN_085223: frame-level R0 ±50 presence=81.8%; first-loss={'RAW_CANDIDATE_GENERATION': 4, 'FINAL_SELECTOR': 6, 'SURVIVED_TO_CURRENT_OUTPUT': 4, 'HYPOTHESIS_CONSTRUCTION': 8}
- SCAN_161718: frame-level R0 ±50 presence=100.0%; first-loss={'SURVIVED_TO_CURRENT_OUTPUT': 13, 'FINAL_SELECTOR': 7, 'HYPOTHESIS_CONSTRUCTION': 2}
- SCAN_165011: frame-level R0 ±50 presence=100.0%; first-loss={'HYPOTHESIS_CONSTRUCTION': 24, 'SURVIVED_TO_CURRENT_OUTPUT': 4, 'FINAL_SELECTOR': 5}

Lower-object ±50 first-loss counts: `{'SURVIVED_TO_CURRENT_OUTPUT': 72, 'FINAL_SELECTOR': 20, 'RAW_CANDIDATE_GENERATION': 16, 'HYPOTHESIS_CONSTRUCTION': 47}`. Diagnostic serialization loss is not a runtime blocker.
