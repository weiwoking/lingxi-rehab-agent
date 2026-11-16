export type AnatomyType = "muscle" | "bone";
export type AnatomySide = "right" | "left" | "midline" | "unknown";

export interface AnatomyManifestItem {
  businessId: string;
  chineseName: string;
  englishName: string;
  zhName: string;
  enName: string;
  type: AnatomyType;
  region: string;
  side: AnatomySide;
  possibleNodeNames: string[];
  matchedNodeNames: string[];
  aliases: string[];
  emgChannel: "CH1" | "CH2" | "CH3" | "CH4" | null;
  semgChannel: "CH1" | "CH2" | "CH3" | "CH4" | null;
  visibleByDefault: boolean;
  focusUpperLimb: boolean;
}

const rightVariants = (base: string) => [base, `${base}.000`, `${base}.001`, `${base}.002`, `${base}_L`, `${base}.L`];
export const rightBoneBusinessIds = {
  clavicle: "clavicle_right",
  scapula: "scapula_right",
  humerus: "humerus_right",
  radius: "radius_right",
  ulna: "ulna_right",
  handBones: "hand_bones_right"
} as const;

export const anatomyManifest: AnatomyManifestItem[] = [
  {
    businessId: "deltoid_right",
    chineseName: "右侧三角肌",
    englishName: "Right deltoid",
    zhName: "右侧三角肌",
    enName: "Right deltoid",
    type: "muscle",
    region: "\u80a9\u90e8",
    side: "right",
    possibleNodeNames: [...rightVariants("Acromial part of deltoid muscle"), ...rightVariants("Clavicular part of deltoid muscle"), ...rightVariants("Scapular spinal part of deltoid muscle"), ...rightVariants("Deltoid muscle")],
    matchedNodeNames: [],
    aliases: ["right deltoid", "deltoid right", "deltoid muscle right", "deltoideus sinister", "deltoid L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "finger_extensors_right",
    chineseName: "右侧指伸肌",
    englishName: "Right finger extensors",
    zhName: "右侧指伸肌",
    enName: "Right finger extensors",
    type: "muscle",
    region: "\u524d\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Extensor digitorum"), ...rightVariants("Extensor digitorum muscle"), ...rightVariants("Extensor indicis"), ...rightVariants("Extensor digiti minimi"), ...rightVariants("Deep part of posterior compartment of forearm"), ...rightVariants("Superficial part of posterior compartment of forearm")],
    matchedNodeNames: [],
    aliases: ["right finger extensors", "finger extensors right", "extensor digitorum right", "extensor digitorum sinister", "finger extensor L"],
    emgChannel: "CH1",
    semgChannel: "CH1",
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "biceps_brachii_right",
    chineseName: "右侧肱二头肌",
    englishName: "Right biceps brachii",
    zhName: "右侧肱二头肌",
    enName: "Right biceps brachii",
    type: "muscle",
    region: "\u4e0a\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Long head of biceps brachii"), ...rightVariants("Short head of biceps brachii"), ...rightVariants("Biceps brachii muscle")],
    matchedNodeNames: [],
    aliases: ["right biceps brachii", "biceps brachii right", "biceps brachii muscle right", "biceps brachii sinister", "biceps L"],
    emgChannel: "CH2",
    semgChannel: "CH2",
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "triceps_brachii_right",
    chineseName: "右侧肱三头肌",
    englishName: "Right triceps brachii",
    zhName: "右侧肱三头肌",
    enName: "Right triceps brachii",
    type: "muscle",
    region: "\u4e0a\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Medial head of triceps brachii"), ...rightVariants("Lateral head of triceps brachii"), ...rightVariants("Long head of triceps brachii"), ...rightVariants("Triceps brachii muscle")],
    matchedNodeNames: [],
    aliases: ["right triceps brachii", "triceps brachii right", "triceps brachii muscle right", "triceps brachii sinister", "triceps L"],
    emgChannel: "CH3",
    semgChannel: "CH3",
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "extensor_carpi_ulnaris_right",
    chineseName: "右侧尺侧腕伸肌",
    englishName: "Right extensor carpi ulnaris",
    zhName: "右侧尺侧腕伸肌",
    enName: "Right extensor carpi ulnaris",
    type: "muscle",
    region: "\u524d\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Ulnar head of extensor carpi ulnaris"), ...rightVariants("Extensor carpi ulnaris"), ...rightVariants("Humeral head of extensor carpi ulnaris")],
    matchedNodeNames: [],
    aliases: ["right extensor carpi ulnaris", "extensor carpi ulnaris right", "extensor carpi ulnaris sinister", "ECU right", "ECU L"],
    emgChannel: "CH4",
    semgChannel: "CH4",
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "scapula_right",
    chineseName: "右侧肩胛骨",
    englishName: "Right scapula",
    zhName: "右侧肩胛骨",
    enName: "Right scapula",
    type: "bone",
    region: "\u80a9\u80db\u5e26",
    side: "right",
    possibleNodeNames: [...rightVariants("Scapula"), ...rightVariants("Spine of scapula")],
    matchedNodeNames: [],
    aliases: ["right scapula", "scapula right", "scapula sinister", "scapula L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "clavicle_right",
    chineseName: "右侧锁骨",
    englishName: "Right clavicle",
    zhName: "右侧锁骨",
    enName: "Right clavicle",
    type: "bone",
    region: "\u80a9\u80db\u5e26",
    side: "right",
    possibleNodeNames: [...rightVariants("Clavicle"), ...rightVariants("Body of clavicle")],
    matchedNodeNames: [],
    aliases: ["right clavicle", "clavicle right", "clavicula sinister", "clavicle L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "humerus_right",
    chineseName: "右侧肱骨",
    englishName: "Right humerus",
    zhName: "右侧肱骨",
    enName: "Right humerus",
    type: "bone",
    region: "\u4e0a\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Humerus"), ...rightVariants("Body of humerus")],
    matchedNodeNames: [],
    aliases: ["right humerus", "humerus right", "humerus sinister", "humerus L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "radius_right",
    chineseName: "右侧桡骨",
    englishName: "Right radius",
    zhName: "右侧桡骨",
    enName: "Right radius",
    type: "bone",
    region: "\u524d\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Radius"), ...rightVariants("Body of radius")],
    matchedNodeNames: [],
    aliases: ["right radius", "radius right", "radius sinister", "radius L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "ulna_right",
    chineseName: "右侧尺骨",
    englishName: "Right ulna",
    zhName: "右侧尺骨",
    enName: "Right ulna",
    type: "bone",
    region: "\u524d\u81c2",
    side: "right",
    possibleNodeNames: [...rightVariants("Ulna"), ...rightVariants("Body of ulna")],
    matchedNodeNames: [],
    aliases: ["right ulna", "ulna right", "ulna sinister", "ulna L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  },
  {
    businessId: "hand_bones_right",
    chineseName: "右手骨",
    englishName: "Right hand bones",
    zhName: "右手骨",
    enName: "Right hand bones",
    type: "bone",
    region: "\u624b\u90e8",
    side: "right",
    possibleNodeNames: [...rightVariants("Bones of hand"), ...rightVariants("Capitate bone"), ...rightVariants("Hamate bone"), ...rightVariants("Trapezium bone"), ...rightVariants("Trapezoid bone"), ...rightVariants("First metacarpal bone"), ...rightVariants("Second metacarpal bone"), ...rightVariants("Third metacarpal bone"), ...rightVariants("Fourth metacarpal bone"), ...rightVariants("Fifth metacarpal bone"), ...rightVariants("Distal phalanx of first finger of hand"), ...rightVariants("Distal phalanx of second finger of hand"), ...rightVariants("Distal phalanx of third finger of hand"), ...rightVariants("Distal phalanx of fourth finger of hand"), ...rightVariants("Distal phalanx of fifth finger of hand"), ...rightVariants("Middle phalanx of second finger of hand"), ...rightVariants("Middle phalanx of third finger of hand"), ...rightVariants("Middle phalanx of fourth finger of hand"), ...rightVariants("Middle phalanx of fifth finger of hand"), ...rightVariants("Proximal phalanx of first finger of hand"), ...rightVariants("Proximal phalanx of second finger of hand"), ...rightVariants("Proximal phalanx of third finger of hand"), ...rightVariants("Proximal phalanx of fourth finger of hand"), ...rightVariants("Proximal phalanx of fifth finger of hand"), ...rightVariants("Phalanges of hand")],
    matchedNodeNames: [],
    aliases: ["right hand bones", "hand bones right", "metacarpal right", "phalanges hand right", "hand bones L"],
    emgChannel: null,
    semgChannel: null,
    visibleByDefault: true,
    focusUpperLimb: true
  }
];

export const semgToBusinessId = {
  CH1: "finger_extensors_right",
  CH2: "biceps_brachii_right",
  CH3: "triceps_brachii_right",
  CH4: "extensor_carpi_ulnaris_right"
} as const;

export function normalizeAnatomyName(value: string) {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/gu, "")
    .replace(/\.(?:r|l)(?=\.|$)/gu, "")
    .replace(/(?:[._-]\d+)$/u, "")
    .replace(/[_\-.]+/gu, " ")
    .replace(/(?<=[a-z])\d+$/u, "")
    .replace(/\b(right|sinister)\b/gu, "right")
    .replace(/\b(l)\b/gu, "right")
    .replace(/\s+/gu, " ")
    .trim();
}

function candidateNames(item: AnatomyManifestItem) {
  return [...item.possibleNodeNames, ...item.matchedNodeNames, ...item.aliases].map(normalizeAnatomyName);
}

export function manifestItemForNodeName(nodeName: string) {
  const normalized = normalizeAnatomyName(nodeName);
  return anatomyManifest.find((item) => candidateNames(item).includes(normalized));
}

export function manifestItemForBusinessId(businessId: string) {
  return anatomyManifest.find((item) => item.businessId === businessId);
}

export function manifestItemForObjectName(object: { name?: string; parent?: unknown; userData?: Record<string, unknown> }) {
  let current: { name?: string; parent?: unknown; userData?: Record<string, unknown> } | null = object;
  while (current) {
    const explicitBusinessId = current.userData?.businessId ?? current.userData?.anatomyBusinessId ?? current.userData?.structure_id;
    if (typeof explicitBusinessId === "string") {
      const mapped = manifestItemForBusinessId(explicitBusinessId);
      if (mapped) return mapped;
    }
    if (current.name) {
      const item = manifestItemForNodeName(current.name);
      if (item) return item;
    }
    current = current.parent as { name?: string; parent?: unknown; userData?: Record<string, unknown> } | null;
  }
  return undefined;
}
