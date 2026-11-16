# Core Class Contract

Stage 6 core classes are:

`person`, `chair`, `dining table`, `cup`, `bottle`, `book`, `tv`.

`person` is user/environment context, not a manipulable object. Chair, table and TV provide seating/work-surface/display context. Cup, bottle and book are directly tied to the current rehabilitation task library.

Existing COCO labels such as `banana`, `bowl`, `mouse`, `remote`, `sink`, `spoon`, `sports ball`, and `toothbrush` remain supported but are not launch-core. `towel`, `block`, `comb`, `sandbag`, `target_zone`, and `demo_small_object` are future custom classes only when an approved task actually requires them. Stage 5.2B performs no training.

The classification was checked read-only against `C:\ec\rehab_decision_kb_v1`, including its README, task libraries, YOLO label map and evidence summary. A class with only 1-4 official benchmark objects is marked insufficient; no stable performance conclusion is made from it.
