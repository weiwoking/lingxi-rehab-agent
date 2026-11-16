# Class Coverage

The frozen official-demo selection covers all requested priority classes: cup (1 object record), bottle (2), book (1), chair (25), dining table (2), and person (1). It also contains couch (4), TV (7), cake (2), and sandwich (1). Counts are Stage 3 output object records, including matched and retained detection-only instances.

COCO detection and segmentation directly support cup, bottle, book, chair, dining table, and person. The read-only rehabilitation knowledge base explicitly refers to cup, bottle, book, chair, and table; person is contextual rather than a manipulable object. The knowledge base also mentions task-specific concepts such as target zones, blocks, towels, balls, beanbags, and demo objects. Generic target zones and domain-specific rehab objects not represented by COCO require future custom detection/training or separate geometry, and are outside Stage 3.

No knowledge-base file was modified.
