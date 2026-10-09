export type Role = "viewer" | "operator" | "reviewer" | "admin";

export interface Me {
  username: string;
  name?: string;
  roles: Role[];
}

export interface Zone {
  id: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  kind: "entrance" | "sales" | "monitored" | "checkout";
}

export interface Layout {
  storeId: string;
  name: string;
  width: number;
  height: number;
  zones: Zone[];
  queues: { id: string; zoneId: string }[];
  /** queueId: the queue the register serves (milestone S2); absent = the first queue. */
  registers: { id: string; queueId?: string }[];
}

export interface QueueState {
  length: number;
  openRegisters: number;
}

export interface StoreState {
  storeId: string;
  simRunId: string | null;
  queues: Record<string, QueueState>;
  zones: Record<string, number>;
}

export interface LiveEvent {
  id: string;
  type: string;
  time: string;
  storeId: string;
  subject: string;
  simRunId: string | null;
  data: Record<string, unknown>;
}

export type IncidentStatus = "OPEN" | "ACKNOWLEDGED" | "CONFIRMED" | "DISMISSED" | "AUTO_RESOLVED" | "CLOSED";

export interface Incident {
  incidentId: string;
  ruleId: string;
  ruleVersion: string;
  mode: "enforce" | "shadow";
  storeId: string;
  simRunId: string | null;
  key: string;
  subject: string | null;
  severity: "low" | "medium" | "high";
  summary: string;
  onsetAt: string;
  detectedAt: string;
  resolvedAt: string | null;
  status: IncidentStatus;
  evidence: string[];
  attrs: Record<string, unknown>;
  updatedAt: string;
  version: number;
}

export interface Review {
  action: string;
  from: IncidentStatus;
  to: IncidentStatus;
  reasonCode: string | null;
  note: string | null;
  actor: string;
  at: string;
}

export interface PipelineHealth {
  status: "ok" | "degraded";
  lastHeartbeatAt: string | null;
  staleAfterSeconds: number;
}

/** Milestone C4: GET /api/incidents/{id}/timeline. */
export interface TimelinePoint {
  t: string;
  length?: number;
  openRegisters?: number | null;
  entries?: number;
}

export interface TimelineMarker {
  t: string;
  kind: "register.opened" | "register.closed";
  registerId: string;
}

export interface IncidentTimelineData {
  incidentId: string;
  from: string;
  to: string;
  onsetAt: string;
  detectedAt: string;
  resolvedAt: string | null;
  threshold: number | null;
  kind: "queue" | "zone" | "none";
  target: string | null;
  series: TimelinePoint[];
  markers: TimelineMarker[];
  truncated: boolean;
}

/** Milestone C5: GET /api/admin/review-metrics. */
export interface RuleReviewMetrics {
  ruleId: string;
  incidents: number;
  acted: number;
  decided: number;
  confirmed: number;
  dismissed: number;
  undecided: number;
  confirmRate: number | null;
  confirmRateLow: number | null;
  confirmRateHigh: number | null;
  timeToActionP50Seconds: number | null;
  timeToActionP90Seconds: number | null;
  dismissReasons: Record<string, number>;
}

export interface ReviewMetrics {
  days: number;
  generatedAt: string;
  rules: RuleReviewMetrics[];
}

/** Milestone R6: GET /api/admin/rule-scores. */
export interface RuleScore {
  ruleId: string;
  ruleVersion: string;
  mode: "enforce" | "shadow";
  runs: number;
  lastMeasuredAt: string;
  lastRunId: string;
  lastScenario: string;
  tp: number;
  fp: number;
  fn: number;
  precision: number | null;
  recall: number | null;
  precisionLow: number | null;
  recallLow: number | null;
  precisionMin: number;
  recallMin: number;
  minN: number | null;
  minLowerBound: number | null;
  ready: boolean;
  reasons: string[];
}

export interface RuleScores {
  windowDays: number;
  generatedAt: string;
  rules: RuleScore[];
}
