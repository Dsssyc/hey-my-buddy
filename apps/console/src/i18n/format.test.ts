import { describe, expect, it } from "vitest";
import { formatClockMinutes, formatCompact, formatDuration, formatMoment, formatMomentMinutes, formatNumber, formatRelative } from "./format";

describe("the numbers and the times the detail writes", () => {
  it("writes a length of time in the units that matter, with the seconds only when asked for", () => {
    expect(formatDuration(12_000)).toBe("12 秒");
    expect(formatDuration(106_000)).toBe("1 分 46 秒");
    expect(formatDuration(32 * 60_000 + 20_000)).toBe("32 分");
    expect(formatDuration(2 * 3_600_000 + 24 * 60_000)).toBe("2 时 24 分");
    expect(formatDuration(2 * 3_600_000)).toBe("2 时");
    expect(formatDuration(188_000, "fine")).toBe("3 分 08 秒");
    expect(formatDuration(3_725_000, "fine")).toBe("1 时 02 分 05 秒");
    expect(formatDuration(0)).toBe("0 秒");
    expect(formatDuration(-5000)).toBe("0 秒");
    expect(formatDuration(0, "minutes")).toBe("0 分");
    expect(formatDuration(2 * 3_600_000, "minutes")).toBe("2 时 0 分");
    expect(formatDuration(2 * 3_600_000 + 59_000, "minutes")).toBe("2 时 0 分");
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
    expect(formatRelative(now - 19 * 60_000, now)).toBe("19 分钟前");
    expect(formatRelative(now - 40_000, now)).toBe("40 秒前");
    expect(formatRelative(now - 3 * 3_600_000, now)).toBe("3 小时前");
    expect(formatRelative(now - 2 * 86_400_000, now)).toBe("2 天前");
    expect(formatRelative(now + 5000, now)).toBe("0 秒前");
  });

  it("writes an exact instant as short as it can be without misleading: today the clock, this year the date, another year the year", () => {
    const now = new Date(2026, 8, 26, 12, 0, 0).getTime();
    expect(formatMoment(new Date(2026, 8, 26, 9, 5, 7).getTime(), now)).toBe("09:05:07");
    expect(formatMoment(new Date(2026, 8, 25, 23, 59, 59).getTime(), now)).toBe("09-25 23:59:59");
    expect(formatMoment(new Date(2025, 11, 31, 8, 0, 0).getTime(), now)).toBe("2025-12-31 08:00:00");
    expect(formatMomentMinutes(new Date(2026, 8, 26, 9, 5, 7).getTime(), now)).toBe("09:05");
    expect(formatMomentMinutes(new Date(2026, 8, 25, 23, 59, 59).getTime(), now)).toBe("09-25 23:59");
    expect(formatMomentMinutes(new Date(2025, 11, 31, 8, 0, 0).getTime(), now)).toBe("2025-12-31 08:00");
  });
});
