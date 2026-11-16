# Coordinate Systems

Camera optical frame:

```text
             +X image right
            /
 optical O +---------->
           |        +Z camera forward
           |
           v +Y image down
```

Ground frame:

```text
                 +Z up
                  ^
                  |
 floor Z=0  O-----+----> +X right
                 /
                / +Y forward
```

Frozen convention:

```text
P_ground_h = T_ground_from_camera @ P_camera_h
```

Points are column vectors and names mean target from source. Row-vector mixing is forbidden. Code validates orthogonality, determinant +1, homogeneity, invertibility, and inverse round trips.

A level synthetic camera at height `h` uses `Xg=Xc`, `Yg=Zc`, `Zg=-Yc+h`. Non-zero pitch/yaw/roll live in the rotation block and are analytically tested; no undocumented device sign is guessed.
