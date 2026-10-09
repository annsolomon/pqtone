import { describe, expect, it } from "vitest";
import { percent, rateWithInterval, reasons, sampleNote, seconds } from "./metrics";

describe("review metrics formatting", () => {
  it("formats rates with their interval", () => {
    expect(percent(0.8)).toBe("80%");
    expect(percent(null)).toBe("–");
    expect(rateWithInterval({ confirmRate: 0.8, confirmRateLow: 0.4902, confirmRateHigh: 0.9433 })).toBe("80% (49–94%)");
    expect(rateWithInterval({ confirmRate: null, confirmRateLow: null, confirmRateHigh: null })).toBe("–");
  });

  it("formats durations", () => {
    expect([null, 4.4, 59.6, 90, 3599, 3660].map(seconds)).toEqual(["–", "4 s", "1 min 00 s", "1 min 30 s", "59 min 59 s", "1 h 01 min"]);
  });

  it("lists dismiss reasons most common first with readable labels", () => {
    expect(reasons({ DUPLICATE: 1, FALSE_POSITIVE: 3, TEST_EVENT: 1 })).toBe("False alarm 3, Duplicate 1, Test or drill 1");
    expect(reasons({})).toBe("–");
    expect(reasons({ NEW_CODE: 2 })).toBe("NEW_CODE 2");
  });

  it("warns about small samples", () => {
    expect(sampleNote({ decided: 0 })).toBe("No decisions yet.");
    expect(sampleNote({ decided: 3 })).toBe("Only 3 decided; treat the rate as a rough guide.");
    expect(sampleNote({ decided: 20 })).toBeNull();
  });
});
