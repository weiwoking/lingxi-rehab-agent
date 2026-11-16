# Third-Party Notices

Unless otherwise noted, original RehabProject code owned by its respective project authors is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0); see [LICENSE](LICENSE).

Third-party software, models, tools, documentation, data and assets remain subject to their respective licenses and notices. This document does not grant rights the project does not own, or automatically license every dataset, generated output or binary under AGPL-3.0. Original third-party license files and source headers are retained.

Scope: root Git tracked content reviewed on 2026-11-16, extended to the current `esp-claw/` snapshot on 2026-11-17. `esp-claw/` is a core directory of this monorepo; its upstream and separately licensed materials retain their own terms as described below. `hardware/`, `external/ESP-Claw_localappdata/`, and `external/P4RehabTTS/cache/` / `logs/` are local-only and outside this distribution. Installed Python/Conda/venv/Node environments are not redistributed by the root Git; dependency specifications describe separately obtained software, whose terms still apply when installed.

**Publication status: the eight remaining component groups are resolved for this root Git distribution.** No unresolved redistribution asset from these groups is tracked. Assets without a verified grant remain local-only and must be obtained directly from their owners; public availability is not a redistribution license. This is a scoped static review, not a blanket legal/provenance or combined-binary compliance certification.

## DELTAR

- Source: [zju3dv/deltar](https://github.com/zju3dv/deltar), recorded upstream commit `f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096`.
- Location: `external/_deltar_formal/work/deltar_f3c5175_win_cu128/`.
- Source license: **GPL-3.0**. Original text: [DELTAR LICENSE](external/_deltar_formal/work/deltar_f3c5175_win_cu128/LICENSE).
- Copyright: DELTAR authors and applicable contributors. The upstream README credits AdaBins, LoFTR and Twins; their attribution and any applicable inherited notices remain in force.
- Local compatibility changes remain part of the GPL-covered DELTAR work; the root license does not relicense these files. GPLv3/AGPLv3 section 13 permits qualifying combined works while preserving each part's own license and applying the relevant network-source requirements.
- Source / split metadata: GPL-3.0. `train_test_split/nyu_sync.json` matches the pinned upstream source after CRLF normalization and lists sample paths, not sample images.
- Weights / samples: no separate redistribution grant was established for the externally hosted NYU checkpoint or demo. Both `nyu.pt` copies, `assets/demo.zip`, and the extracted `data/demo/` are **local-only, excluded from Git**. Generated demo image/depth files were not tracked; no rights to redistribute them are inferred. The sample frames are DELTAR demo captures and are not assumed to be NYU images merely because the model was trained on NYU.
- Direct acquisition: [official DELTAR download folder](https://drive.google.com/drive/folders/1ZGUdagrmFDr90Lm6qG1FkbZR_Tgpmr64). Drive file IDs: `nyu.pt` = `12ETE0LKHQvwGIL54M7dU_QEPVhNR6HQh`; `demo.zip` = `1QST5VneyVqPakXML1wfrBQXinsoseg_i`. Runtime setup restores only the two required weight copies. No inference/training is performed by setup.
- All expected paths, byte sizes and SHA256 values are recorded in [local-only asset inventory](third_party/local-only-assets.csv); automatic recovery details are in [runtime manifest](third_party/runtime-assets.json).

## Ultralytics YOLO

- Source: [Ultralytics](https://github.com/ultralytics/ultralytics), [official licensing guidance](https://www.ultralytics.com/license).
- License: **AGPL-3.0 community route**, unless an applicable separately obtained Ultralytics license applies. No Enterprise license was established in this review.
- Copyright: Ultralytics and respective project fine-tuning contributors.
- No installed Ultralytics package source is tracked by the root Git. The repository contains importing/inference scripts, model metadata and the following weights/import material:

| Asset | Root Git location | Provenance / terms |
|---|---|---|
| Scan Detect v5b | `external/_deltar_p4_pipeline/models/rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt` | Project fine-tuned candidate imported from separate training workspace; Pilot P15 epoch 2; AGPL-3.0 default model route |
| Historical Detect v3 epoch 4 | `external/_deltar_p4_pipeline/models/rehab_scan_yolo11n_coco80_volleyball_detect_v3_epoch4_exp.pt` | Project experimental YOLO derivative; exact training provenance less complete; Ultralytics obligations retained |
| Historical Detect v3 epoch 10 | `external/_deltar_p4_pipeline/models/rehab_scan_yolo11n_coco80_volleyball_detect_v3_epoch10_exp.pt` | Project experimental YOLO derivative; exact training provenance less complete; Ultralytics obligations retained |
| YOLO11n-seg | `external/_deltar_p4_pipeline/models/yolo11n-seg.pt` | Recorded official Ultralytics assets auto-download, release v8.4.0; AGPL-3.0 |
| v5b import archive | `external/model_provenance/rehab_scan_detect_v5b_exp_20260807_150118.zip` | Contains the same v5b checkpoint plus metadata; same model obligations apply inside ZIP |
| DELTAR NYU checkpoint | `external/_deltar_formal/assets/nyu.pt` and `external/_deltar_formal/work/deltar_f3c5175_win_cu128/weights/nyu.pt` | Official DELTAR import; local-only, not distributed by Git. Separate weight grant not established; fetch directly from official source |

Self-training/fine-tuning does not remove the upstream terms. Before any public distribution or relevant network deployment, preserve notices and meet applicable complete corresponding-source requirements, including application, modifications, configuration and training materials where required. Import metadata points to training materials outside this root Git; full source/training-data compliance was not certified. The `esp-claw/` snapshot and its additional model/asset notices are covered by the scoped review below. Original image/data rights are separate from the model license. Non-commercial use is not an exemption from compliance, and AGPL itself contains no added non-commercial restriction.

## QGP_EVMotor and its embedded libraries

- Vendor: Qiguopai / QGPMaker; [vendor site](http://www.7gp.cn), [recorded author URL](https://github.com/qiguopai).
- Location: `s3/libraries/QGP_EVMotor/`.
- **Core: LOCAL_ONLY + OFFICIAL_DOWNLOAD.** No core redistribution grant was established. All 18 core / example / metadata files and the duplicate vendor-reference TXT beside the Car sketch match the [official QGP_EVMotor ZIP](https://www.7gp.cn/wp-content/uploads/2025/11/QGP_EVMotor.zip) byte-for-byte; archive SHA256: `4890ab078efe5cffbf0dcf4d2e2a7a975d7ec0a9581f5f8586689e50ad4e2b56`. Package metadata states version `1.0.0`; the URL is the November 2025 vendor package. [Official setup instructions](https://www.7gp.cn/archives/1409). The core, `library.properties`, examples, and precompiled `src/esp32s3/libqgpmotor.a` are excluded from root Git while retained locally. The Car sketch still requires this library. Run the fetch script before compiling; it restores only hash-verified missing core files and never overwrites differing files. Embedded licenses do not cover the core.

| Embedded component | Files under `s3/libraries/QGP_EVMotor/src/` | License / attribution |
|---|---|---|
| NimBLE-Arduino / esp-nimble-cpp / Apache Mynewt | `NimBLE*`, `nimconfig*`, `nimble/` except TinyCrypt | Apache-2.0; Ryan Powell, contributors, Apache Software Foundation, Espressif and Runtime Inc. Preserve [LICENSE](s3/libraries/QGP_EVMotor/src/nimble/LICENSE), [NOTICE](s3/libraries/QGP_EVMotor/src/nimble/NOTICE) and headers |
| TinyCrypt / micro-ecc | `nimble/ext/tinycrypt/` | BSD-3-Clause / BSD-2-Clause as individually stated; Intel, Kenneth MacKay, Chris Morrison and contributors; retain [LICENSE](s3/libraries/QGP_EVMotor/src/nimble/ext/tinycrypt/LICENSE), [AUTHORS](s3/libraries/QGP_EVMotor/src/nimble/ext/tinycrypt/AUTHORS) and headers |
| HID headers | `HIDKeyboardTypes.h`, `HIDTypes.h` | MIT, mbed.org; complete grants retained in headers |
| SBUS | `sbus.h`, `sbus.cpp` | MIT, Bolder Flight Systems Inc (2022); complete grants retained in headers |
| PS2X controller | `PS2X_lib.h`, `PS2X_lib.cpp` | GPL-3.0-or-later explicitly in header; Shutter, Bill Porter, Eric Wetzel, Kurt Eckhardt and contributors |
| WFJoystick | `WFJoystick.h`, `WFJoystick.cpp` | GPL-3.0-or-later explicitly in header; Qiguopai / respective contributors |
| MPU6050_light | `MPU6050_light.h`, `MPU6050_light.cpp` | MIT; Romain JL. FETICK, Tockn. Both files match upstream `507a8fbefeffb857095c2ab0b8e4a042d4d1be96` after CRLF normalization only; upstream metadata 1.2.1. [Fixed-commit license](third_party/licenses/MPU6050_light-MIT.txt) retained |
| PubSubClient | `PubSubClient.h`, `PubSubClient.cpp` | MIT, Nicholas O'Leary (2008-2020). Both files byte-identical to upstream v2.8 `2d228f2f862a95846c65a8518c79f48dfc8f188c`. [MIT text](third_party/licenses/PubSubClient-MIT.txt) retained |
| Encoder headers | `ESP32Encoder.h`, `InterruptEncoder.h` | Kevin Harrington / hephaestus and contributors; both headers byte-identical to madhephaestus/ESP32Encoder 0.11.8 `fab1634220b42e4ba13d09fe4774be904f3f54f4`. [Upstream four-condition BSD-style license](third_party/licenses/ESP32Encoder-upstream.txt), including mandatory acknowledgment below; no endorsement |

The tracked [DELTAR GPLv3 text](external/_deltar_formal/work/deltar_f3c5175_win_cu128/LICENSE) is also a copy of the GPL version referenced by the controller headers. No third-party source copyright header was altered.

Required encoder acknowledgment (retain in every distribution of these headers):

> This product includes software developed by the "Universidad de Palermo, Argentina" (http://www.palermo.edu/).

The encoder upstream license contains no separate copyright line; authorship above follows its metadata and the preserved source header. The license is preserved verbatim and is not relabeled MIT or BSD-3-Clause. These source notices do not certify a separately built combined firmware.

## Local-only vendor hardware references

Local-only vendor hardware references are not distributed in this repository. This includes the local TI documents, WitMotion tools and bundled Resource Hacker / EasyUI software. Their original local files and historical notices remain with the developer; they are not publication blockers for this root repository's tracked scope. The separately copied WitMotion C SDK in the S3 nodes remains in scope below.

## WitMotion C SDK in the S3 nodes

The six vendor files `wit_c_sdk.c`, `wit_c_sdk.h`, `REG.h` under both `s3/node_left/components/BSP/IMU/` and `s3/node_right/components/BSP/IMU/` are **LOCAL_ONLY + OFFICIAL_DOWNLOAD**, excluded from root Git. The project `wit_imu` wrappers are retained unchanged. Both S3 BSP builds need the SDK files; a fresh clone must run setup first.

Source: [official SDK repository](https://github.com/WITMOTION/WitStandardProtocol_JY901/tree/e8f4e3afc02263e2ef15052dc758f45efaebe12f), commit `e8f4e3afc02263e2ef15052dc758f45efaebe12f`; [vendor download guidance](https://wit-motion.com/SDK.html). All six files match the fixed official source bytes. No applicable source redistribution grant was found in the SDK/archive/official guidance. The fetch script obtains the SDK directly from pinned official raw URLs and verifies each SHA256; it does not grant a redistribution license. See [expected paths and hashes](third_party/local-only-assets.csv).

## Local-only ST documentation

Two VL53L5CX references remain on the developer's disk and are excluded from Git:

| Document | Version/date | Expected local path | Official source |
|---|---|---|---|
| DS13754 datasheet | Rev 13, September 2024 | `external/_deltar_p4_pipeline/docs_external/vl53l5cx_ds13754.pdf` | [ST datasheet](https://www.st.com/resource/en/datasheet/vl53l5cx.pdf) |
| UM2884 ULD user manual | Rev 7, August 2025 | `external/_deltar_p4_pipeline/docs_external/vl53l5cx_um2884.pdf` | [ST manual](https://www.st.com/resource/en/user_manual/um2884-a-guide-to-using-the-vl53l5cx-multizone-timeofflight-ranging-sensor-with-wide-field-of-view-ultra-lite-driver-uld-stmicroelectronics.pdf) |

Copyright STMicroelectronics 2024 / 2025; all rights reserved. The final PDF pages grant no IP license, and [ST website terms](https://www.st.com/content/st_com/en/about/terms-of-use.html) restrict redistribution without prior written permission. No such permission was established. Neither document is a runtime dependency, so setup does not download them. Exact SHA256 values are in the [local-only inventory](third_party/local-only-assets.csv).

## Data, generated media and provenance evidence

DELTAR demo samples and their image/depth derivatives are excluded from root Git; no redistribution grant is claimed. NYU/SUN RGB-D/Hypersim download descriptions in audit scripts are references; the local audit records no new download of these full datasets. Do not claim a dataset is distributed merely because its URL is recorded. Project captures/scoring/calibration evidence retain their provenance; this notice does not automatically apply a software license to all raw data or media.

## Supplemental upstream notices

The notices below reproduce identified upstream grants/attributions without changing original source files. They apply to the source revisions identified above. The separately hosted vendor core / SDK / data / model files listed as local-only are not distributed under these grants.

### MPU6050_light upstream MIT notice

Source: [MPU6050_light upstream MIT notice](https://github.com/rfetick/MPU6050_light/blob/507a8fbefeffb857095c2ab0b8e4a042d4d1be96/LICENSE).

```text
MIT License

Copyright (c) 2020 Romain JL. FETICK

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### PubSubClient upstream MIT notice

Source: [PubSubClient upstream MIT notice](https://github.com/knolleary/pubsubclient/blob/2d228f2f862a95846c65a8518c79f48dfc8f188c/LICENSE.txt).

```text
Copyright (c) 2008-2020 Nicholas O'Leary

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```


## ESP-Claw and RehabProject integration

- Upstream/source: [Espressif ESP-Claw](https://github.com/espressif/esp-claw).
- Repository location: `esp-claw/`. Upstream/framework license: **Apache-2.0**; the original [ESP-Claw LICENSE](esp-claw/LICENSE) and source copyright/SPDX notices are retained unchanged. The root AGPL-3.0 does not relicense this third-party code.
- This snapshot includes RehabProject changes for ESP32-P4 rehabilitation control, sensors, UI, Doctor/Patient, scoring, vision, TTS, launchers and local path integration. It is a modified integration, not an unmodified upstream release. Existing per-file modification/copyright notices remain authoritative; this inventory identifies the modified distribution without claiming ownership of upstream files.
- `esp-claw/components/common/esp_painter/font/basic_font_*.c` carry **CC0-1.0** SPDX notices, retained verbatim.
- Documentation follows the project license subject to [docs/LICENSE](esp-claw/docs/LICENSE) and [image notices](esp-claw/docs/src/assets/images/LICENSE). Breadboard image: Guhuru, CC0, [source](https://commons.wikimedia.org/wiki/File:Breadboard.png). WS2812 image: SparkFun Electronics, CC BY 2.0, [source](https://commons.wikimedia.org/wiki/File:12664-02a.jpg); the upstream notice's truncated license URL is clarified as [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/), without editing that notice. Logos and the assets singled out for possible additional permission by `docs/LICENSE` are retained locally but excluded from the root Git snapshot pending a specific grant; no trademark endorsement or permission is claimed.
- `esp-claw/pc/rehab_doctor_twin/frontend/public/models/body.glb`: Body Anatomy 3D Viewer by hpfrei / Z-Anatomy, **CC BY-SA 4.0**. [Upstream source](https://github.com/hpfrei/body-anatomy-3d-viewer), [retained attribution/license](esp-claw/pc/rehab_doctor_twin/frontend/public/licenses/body-anatomy-3d-viewer-LICENSE.txt), [local provenance audit](esp-claw/pc/rehab_doctor_twin/frontend/public/models/body_model_audit.json). It is a separately licensed static anatomical asset used by the project; original attribution is retained. Project runtime transforms do not imply relicensing the model.
- `esp-claw/pc/vision_interpreter/models/` and `esp-claw/pc/vision_interpreter/yolo11n-cls.pt`: Ultralytics YOLO base and project identity fine-tuned PT/ONNX weights follow the **AGPL-3.0 community model route** described above; fine-tuning does not remove upstream obligations. Local model handoff/evaluation metadata is retained. Training data and full training provenance are not certified by this static source review.
- The two `rehab_cn_20.bin` files under `esp-claw/application/edge_agent/{tools/rehab_ui_font/generated,fatfs_image/system/ui/fonts}/` are generated subsets of Adobe Source Han Sans SC Normal, **SIL Open Font License 1.1**. Their project font name is Rehab UI; no Reserved Font Name is claimed for the derivative. The source input remains a separately obtained ignored managed dependency; the exact adjacent copyright and OFL text applicable to that input is reproduced below so the distributed subset retains it.
- `managed_components/`, builds, environments and dependencies ignored by `esp-claw/.gitignore` are not distributed by this snapshot. Their own licenses still apply when obtained or built. No original ESP-Claw commit history is imported.

### Source Han Sans subset: retained copyright and OFL

```text
Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font
Name 'Source'. Source is a trademark of Adobe in the United States
and/or other countries.

This Font Software is licensed under the SIL Open Font License,
Version 1.1.

This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL

-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font
creation efforts of academic and linguistic communities, and to
provide a free and open framework in which fonts may be shared and
improved in partnership with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply to
any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software
components as distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to,
deleting, or substituting -- in part or in whole -- any of the
components of the Original Version, by changing formats or by porting
the Font Software to a new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed,
modify, redistribute, and sell modified and unmodified copies of the
Font Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components, in
Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the
corresponding Copyright Holder. This restriction only applies to the
primary font name as presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created using
the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.

```

### Espressif board code: Modified MIT exception

The following source files explicitly carry `LicenseRef-Espressif-Modified-MIT`, not Apache-2.0 or the root AGPL. Their original 2026 Espressif copyright and SPDX notices are retained:

- `esp-claw/application/edge_agent/boards/espressif/esp32_s31_korvo1/setup_device.c`
- `esp-claw/application/edge_agent/boards/espressif/esp_box_3/setup_device.c`
- `esp-claw/application/edge_agent/boards/m5stack/m5stack_cores3/power_manager.h`
- `esp-claw/application/edge_agent/boards/m5stack/m5stack_tab5/setup_device.c`

The identified license permits source redistribution for use exclusively with Espressif Systems products. These board drivers are distributed here for their ESP32 board integration only; use or redistribution for non-Espressif products is prohibited. This restriction is not a general open-source grant and must not be removed. [Official board-manager license](https://github.com/espressif/esp-board-manager/blob/main/esp_board_manager/LICENSE); exact text from the locally obtained board-manager distribution follows:

```text
Espressif Modified MIT License

 Copyright (c) 2025 Espressif Systems (Shanghai) CO., LTD

 Permission is hereby granted for use EXCLUSIVELY with Espressif Systems products.
 This includes the right to use, copy, modify, merge, publish, distribute, and sublicense
 the Software, subject to the following conditions:

 1. This Software MUST BE USED IN CONJUNCTION WITH ESPRESSIF SYSTEMS PRODUCTS.
 2. The above copyright notice and this permission notice shall be included in all copies
    or substantial portions of the Software.
 3. Redistribution of the Software in source or binary form FOR USE WITH NON-ESPRESSIF PRODUCTS
    is strictly prohibited.

 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
 PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
 FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
 OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 DEALINGS IN THE SOFTWARE.

 SPDX-License-Identifier: LicenseRef-Espressif-Modified-MIT

```
