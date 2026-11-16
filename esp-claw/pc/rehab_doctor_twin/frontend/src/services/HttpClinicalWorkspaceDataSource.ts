import type {
  ClinicianPrescription,
  PatientTrainingTrend,
  SafetyIntervention,
  SessionTaskDecision,
  TherapistReview
} from "../contracts/clinicalWorkspace";
import type { ClinicalWorkspaceDataSource } from "./ClinicalWorkspaceDataSourceContract";

export class HttpClinicalWorkspaceDataSource implements ClinicalWorkspaceDataSource {
  async getPrescription(patientId: string): Promise<ClinicianPrescription> {
    return request<{ prescription: ClinicianPrescription }>(
      `/api/clinical/patients/${encodeURIComponent(patientId)}/prescription`
    ).then((value) => value.prescription);
  }

  async savePrescription(patientId: string, prescription: ClinicianPrescription): Promise<ClinicianPrescription> {
    return request<{ prescription: ClinicianPrescription }>(
      `/api/clinical/patients/${encodeURIComponent(patientId)}/prescription`,
      { method: "PUT", body: JSON.stringify(prescription) }
    ).then((value) => value.prescription);
  }

  async getTaskDecision(patientId: string, sessionId: string): Promise<SessionTaskDecision> {
    return request<{ decision: SessionTaskDecision }>(
      sessionUrl(patientId, sessionId, "decision")
    ).then((value) => value.decision);
  }

  async getSafetyInterventions(patientId: string, sessionId: string): Promise<SafetyIntervention[]> {
    return request<{ items: SafetyIntervention[] }>(
      sessionUrl(patientId, sessionId, "safety")
    ).then((value) => value.items);
  }

  async getTrainingTrend(patientId: string, limit = 7): Promise<PatientTrainingTrend> {
    return request<{ trend: PatientTrainingTrend }>(
      `/api/clinical/patients/${encodeURIComponent(patientId)}/trend?limit=${limit}`
    ).then((value) => value.trend);
  }

  async getTherapistReview(patientId: string, sessionId: string): Promise<TherapistReview> {
    return request<{ review: TherapistReview }>(
      sessionUrl(patientId, sessionId, "review")
    ).then((value) => value.review);
  }

  async saveTherapistReview(review: TherapistReview): Promise<TherapistReview> {
    return request<{ review: TherapistReview }>(
      sessionUrl(review.patient_id, review.session_id, "review"),
      { method: "PUT", body: JSON.stringify(review) }
    ).then((value) => value.review);
  }
}

function sessionUrl(patientId: string, sessionId: string, suffix: string) {
  return `/api/clinical/patients/${encodeURIComponent(patientId)}/sessions/${encodeURIComponent(sessionId)}/${suffix}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(body.detail ?? "Request failed");
  }
  return response.json();
}
