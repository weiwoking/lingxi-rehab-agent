import * as THREE from "three";
import { manifestItemForBusinessId, manifestItemForObjectName } from "./modelManifest";

export class AnatomyPicker {
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2();

  pick(event: PointerEvent, rect: DOMRect, camera: THREE.Camera, meshes: THREE.Mesh[]) {
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, camera);
    const hit = this.raycaster.intersectObjects(meshes.filter((mesh) => mesh.visible), true)[0];
    if (!hit) return null;

    const mesh = hit.object as THREE.Mesh;
    const name = mesh.name || "未命名结构";
    const businessId = mesh.userData.anatomyBusinessId;
    const mapped = typeof businessId === "string" ? manifestItemForBusinessId(businessId) : manifestItemForObjectName(mesh);
    if (!mapped) return `${name}\n未映射到业务结构`;

    return [
      `${mapped.chineseName} / ${mapped.englishName}`,
      `类型：${mapped.type === "muscle" ? "肌肉" : "骨骼"} · 侧别：${mapped.side}`,
      `区域：${mapped.region}`,
      `原始节点：${name}`,
      `对应 sEMG：${mapped.emgChannel ?? "无独立肌电通道"}`,
      `实时肌电驱动：${mapped.emgChannel ? "是" : "否"}`
    ].join("\n");
  }
}
