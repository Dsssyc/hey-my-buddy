import { describe, expect, it } from "vitest";
import { formatClockMinutes, formatCompact, formatDuration, formatNumber, formatRelative } from "./format";

describe("the numbers and the times the detail writes", () => {
  it("writes a length of time in the units that matter, with the seconds only when asked for", () => {
    expect(formatDuration(12_000)).toBe("12 秒");
    expect(formatDuration(106_000)).toBe("1 分 46 秒");
    expect(formatDuration(32 * 60_000 + 20_000)).toBe("32 分");
    expect(formatDuration(2 * 3_600_000 + 24 * 60_000)).toBe("2 时 24 分");
    expect(formatDuration(2 * 3_600_000)).toBe("2 时");
    expect(formatDuration(188_000, true)).toBe("3 分 08 秒");
    expect(formatDuration(3_725_000, true)).toBe("1 时 02 分 05 秒");
    expect(formatDuration(0)).toBe("0 秒");
    expect(formatDuration(-5000)).toBe("0 秒");
  });

  it("writes a count of tokens short above a thousand, and whole below", () => {
    expect(formatCompact(999)).toBe("999");
    expect(formatCompact(23_400)).toBe("23.4k");
    expect(formatCompact(18_000)).toBe("18.0k");
    expect(formatNumber(12_000)).toBe("12,000");
  });

  it("writes a clock time of 24 hours and a time as long ago as it is", () => {
    expect(formatClockMinutes(new Date(2026, 8, 26, 9, 5).getTime())).toBe("09:05");
    const now = new Date(2026, 8, 26, 12, 0, 0).getTime();
    expect(formatRelative(now - 19 * 60_000, now)).toContain("19");
    expect(formatRelative(now - 19 * 60_000, now)).toContain("分钟");
    expect(formatRelative(now + 5000, now)).toContain("0");
  });
});
