import type {
  ClinicianPrescription,
  PatientTrainingTrend,
  SafetyIntervention,
  SessionTaskDecision,
  TherapistReview
} from "../contracts/clinicalWorkspace";

export interface ClinicalWorkspaceDataSource {
  getPrescription(patientId: string): Promise<ClinicianPrescription>;
  savePrescription(patientId: string, prescription: ClinicianPrescription): Promise<ClinicianPrescription>;
  getTaskDecision(patientId: string, sessionId: string): Promise<SessionTaskDecision>;
  getSafetyInterventions(patientId: string, sessionId: string): Promise<SafetyIntervention[]>;
  getTrainingTrend(patientId: string, limit?: number): Promise<PatientTrainingTrend>;
  getTherapistReview(patientId: string, sessionId: string): Promise<TherapistReview>;
  saveTherapistReview(review: TherapistReview): Promise<TherapistReview>;
}
