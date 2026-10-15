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
  /** Event time of the store's most recent event (X1 B7: the Sales page opens a simulation on its day). */
  lastEventAt?: string | null;
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

export type TillKind = "void" | "refund" | "no_sale" | "price_override" | "drawer_open" | "shift_open" | "shift_close";

export interface TimelineMarker {
  t: string;
  /** Register changes, and (X1 B7) till events at the tills serving the queue. */
  kind: "register.opened" | "register.closed" | `till.${TillKind}`;
  registerId: string;
  amountMinor?: number | null;
  currency?: string | null;
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

/** X1 B3/B7: billing reads. Money in integer minor units, quantities in thousandths of the unit. */
export type Bucket = "15m" | "hour" | "day";
export type Tender = "cash" | "card" | "upi" | "wallet" | "credit" | "voucher" | "other";

interface BillingWindow {
  storeId: string;
  simRunId: string | null;
  from: string;
  to: string;
}

export interface SalesBucket {
  start: string;
  currency: string;
  bills: number;
  salesMinor: number;
  taxMinor: number;
  lines: number;
  voidedBills: number;
}

export interface Sales extends BillingWindow {
  bucket: Bucket;
  timeZone: string;
  tillId: string | null;
  buckets: SalesBucket[];
  totals: Omit<SalesBucket, "start">[];
}

export interface PaymentsBucket {
  start: string;
  currency: string;
  tender: Tender;
  inMinor: number;
  outMinor: number;
  netMinor: number;
  paymentsIn: number;
  paymentsOut: number;
}

export interface Payments extends BillingWindow {
  bucket: Bucket;
  timeZone: string;
  tillId: string | null;
  buckets: PaymentsBucket[];
  totals: Omit<PaymentsBucket, "start">[];
}

export interface StockRow {
  sku: string;
  unit: string;
  inMilli: number;
  outMilli: number;
  netMilli: number;
  inByReason: Record<string, number>;
  outByReason: Record<string, number>;
  movements: number;
  level: { qtyMilli: number; at: string } | null;
}

export interface Stock extends BillingWindow {
  skus: StockRow[];
  next: string | null;
}

export interface TrafficBucket {
  start: string;
  visitors: number | null;
  queues: { queueId: string; meanLength: number | null; maxLength: number | null }[];
}

export interface Traffic extends BillingWindow {
  bucket: Bucket;
  timeZone: string;
  entranceZones: string[];
  buckets: TrafficBucket[];
  truncated: boolean;
}

/** X1 B5f: one billing connector source on an edge box, from the box's heartbeat (edge.heartbeat 1.1.0). */
export interface EdgeConnector {
  id: string;
  type: "watch" | "sql" | "http" | "tally" | "push";
  ok: boolean;
  stale: boolean;
  failures: number;
  lastSuccessAgeSeconds?: number | null;
  events?: number;
  rowErrors?: number;
}

export interface EdgeAgent {
  agentId: string;
  source: string;
  at: string;
  stale: boolean;
  agentVersion: string;
  uptimeSeconds: number;
  alarms: string[];
  cameras: unknown[];
  outbox: Record<string, number>;
  connectors: EdgeConnector[];
  clockOffsetMs: number | null;
}

export interface EdgeHealth {
  storeId: string;
  generatedAt: string;
  staleAfterSeconds: number;
  agents: EdgeAgent[];
}
