import * as THREE from "three";

export function disposeThreeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const material = mesh.material;
    if (Array.isArray(material)) {
      material.forEach(disposeMaterial);
    } else if (material) {
      disposeMaterial(material);
    }
  });
}

function disposeMaterial(material: THREE.Material) {
  const maybeTextures = material as THREE.Material & Record<string, unknown>;
  for (const key of Object.keys(maybeTextures)) {
    const value = maybeTextures[key];
    if (value instanceof THREE.Texture) value.dispose();
  }
  material.dispose();
}

