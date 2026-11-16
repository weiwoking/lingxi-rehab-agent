import { showcaseDoctorNotes } from "../data/showcaseRecords";

export interface DoctorNoteRecord {
  record_id: string;
  note: string;
  updated_at: string;
}

export function doctorNoteKey(recordId: string) {
  return `rehab_doctor_note:${recordId}`;
}

export function loadDoctorNote(recordId: string) {
  const fallback = showcaseDoctorNotes[recordId] ?? "";
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(doctorNoteKey(recordId));
    if (!raw) return fallback;
    const value = JSON.parse(raw) as Partial<DoctorNoteRecord>;
    return typeof value.note === "string" ? value.note : fallback;
  } catch {
    return fallback;
  }
}

export function saveDoctorNote(recordId: string, note: string) {
  if (typeof window === "undefined") return;
  const payload: DoctorNoteRecord = {
    record_id: recordId,
    note,
    updated_at: new Date().toISOString()
  };
  window.localStorage.setItem(doctorNoteKey(recordId), JSON.stringify(payload));
}
