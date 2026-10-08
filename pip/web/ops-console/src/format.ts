export function clock(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toISOString().slice(11, 19);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 19)} UTC`;
}

export function duration(fromIso: string, toIso: string): string {
  const s = Math.max(0, Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

const RULE_NAMES: Record<string, string> = {
  "R-QUEUE-001": "Long checkout queue",
  "R-DWELL-001": "Long fitting-room visit",
  "R-ABS-001": "No register opened",
};

export function ruleName(ruleId: string): string {
  return RULE_NAMES[ruleId] ?? ruleId;
}

const STATUS_LABELS: Record<string, string> = {
  OPEN: "Open",
  ACKNOWLEDGED: "Acknowledged",
  CONFIRMED: "Confirmed",
  DISMISSED: "Dismissed",
  AUTO_RESOLVED: "Cleared by itself",
  CLOSED: "Closed",
};

export function statusLabel(s: string): string {
  return STATUS_LABELS[s] ?? s;
}

export function describeEvent(type: string, data: Record<string, unknown>): string {
  switch (type) {
    case "zone.entered":
      return `Visit started in ${String(data["zoneId"])}`;
    case "zone.exited":
      return `Visit ended in ${String(data["zoneId"])}`;
    case "queue.joined":
      return `Joined ${String(data["queueId"])}`;
    case "queue.left":
      return `Served at ${String(data["queueId"])}`;
    case "queue.length":
      return `${String(data["queueId"])}: ${String(data["length"])} waiting, ${String(data["openRegisters"])} open`;
    case "register.opened":
      return `${String(data["registerId"])} opened`;
    case "register.closed":
      return `${String(data["registerId"])} closed`;
    case "clock.tick":
      return "Clock";
    default:
      return type;
  }
}
