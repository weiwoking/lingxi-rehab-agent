# W04G — one-group physical workflow

Use this only after GPT/user explicitly approves the one new capture.

1. Do not move the ChArUco board.
2. Put the vehicle at roughly the middle working distance.
3. Shift the whole vehicle sideways by a noticeable amount.
4. Turn the front so the fixed board is fully visible again with clear image margin.
5. Do not measure or enter an angle or left/right distance.
6. Score one exported preview frame or screenshot:
   - `TOO_FRONTAL` → move sideways a little more and aim back at the board;
   - `TOO_OBLIQUE` → reduce the sideways offset slightly;
   - board/visibility warning → correct only the stated visibility issue;
   - `GOOD` → stop repositioning.
7. Hold still for 2–3 seconds and capture exactly one 11-frame scan as W04G.

Preview command:

```powershell
Set-Location 'C:\ec\_calibration_workspaces\stage3a_camera_world_20260818_01'
python .\stage3a_oblique_gravity_score.py score-frame --image <exported-image-or-image.rgb565>
```

The scorer is read-only. It does not capture, register, bind, select latest, export a candidate, or promote anything.
