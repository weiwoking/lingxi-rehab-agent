# 085223 focused frame audit

| Frame | Class | Layer | Old support | Fused support | Confidence | Status | Top hypotheses (mm/probability) |
|---|---|---|---:|---:|---:|---|---|
| frame_000000 | bottle | lower | 894.710 | 877.141 | 0.920 | ok | 877.1/1.00 |
| frame_000000 | refrigerator | upper | 802.057 | 800.533 | 0.910 | ok | 800.5/1.00 |
| frame_000000 | bottle | upper | 1014.159 | 1013.151 | 0.882 | ok | 1013.2/1.00 |
| frame_000000 | sports ball | lower | 884.724 | 877.141 | 0.920 | ok | 877.1/1.00 |
| frame_000001 | person | upper | 773.767 | 798.029 | 0.894 | ok | 798.0/1.00 |
| frame_000001 | bottle | lower | 870.648 | 868.262 | 0.920 | ok | 868.3/1.00 |
| frame_000001 | sports ball | lower | 870.346 | 868.262 | 0.920 | ok | 868.3/1.00 |
| frame_000001 | bottle | upper | 1039.989 | 1092.070 | 0.895 | ok | 1092.1/1.00 |
| frame_000009 | bottle | lower | 725.645 | 650.662 | 0.920 | ok | 650.7/1.00 |
| frame_000009 | bottle | upper | 1028.696 | 1025.611 | 0.888 | ok | 1025.6/1.00 |
| frame_000009 | sports ball | lower | 661.193 | 650.662 | 0.920 | ok | 650.7/1.00 |
| frame_000010 | bottle | lower | 726.435 | 689.485 | 0.418 | degraded | 658.4/0.56, 731.4/0.44 |
| frame_000010 | sports ball | lower | 670.540 | 689.485 | 0.418 | degraded | 658.4/0.56, 731.4/0.44 |
| frame_000010 | bottle | upper | 1019.873 | 1028.288 | 0.891 | ok | 1028.3/1.00 |

The distribution fusion softens local within-frame winner changes only when multiple hypotheses exist. It cannot stop large frame-to-frame jumps when the complete single-frame candidate distribution itself moves to a different height region or omits the correct neighborhood. That limitation is the Phase A NO-GO reason, not an invitation to add temporal state.
