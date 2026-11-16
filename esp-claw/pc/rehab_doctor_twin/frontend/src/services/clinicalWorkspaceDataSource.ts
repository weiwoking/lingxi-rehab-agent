import type { ClinicalWorkspaceDataSource } from "./ClinicalWorkspaceDataSourceContract";
import { DemoClinicalWorkspaceDataSource } from "./DemoClinicalWorkspaceDataSource";
import { HttpClinicalWorkspaceDataSource } from "./HttpClinicalWorkspaceDataSource";

const mode = import.meta.env.VITE_CLINICAL_DATA_MODE ?? "live";
const liveSource = new HttpClinicalWorkspaceDataSource();
const demoSource = new DemoClinicalWorkspaceDataSource();

export const clinicalWorkspaceDataSource: ClinicalWorkspaceDataSource = mode === "live" ? liveSource : demoSource;
export const clinicalWorkspaceDataMode = mode === "live" ? "live" : "demo";
