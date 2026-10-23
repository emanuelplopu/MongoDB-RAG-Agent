/** Axis and lookup helpers shared by the telemetry charts. */

/** Round tick step: 1, 2, 2.5 or 5 x 10^n. */
export function niceStep(range: number, ticks = 4): number {
  if (!(range > 0)) return 1
  const raw = range / ticks
  const pow = Math.pow(10, Math.floor(Math.log10(raw)))
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (m * pow >= raw) return m * pow
  }
  return 10 * pow
}

const TICK_STEPS_MS = [
  60e3, 2 * 60e3, 5 * 60e3, 10 * 60e3, 15 * 60e3, 30 * 60e3,
  3600e3, 2 * 3600e3, 3 * 3600e3, 6 * 3600e3, 12 * 3600e3,
  86400e3, 2 * 86400e3, 7 * 86400e3,
]

/** Round time ticks (whole minutes/hours/days in local time) between t0 and t1, at most `maxTicks`. */
export function timeTicks(t0: number, t1: number, maxTicks: number): number[] {
  const span = Math.max(t1 - t0, 1)
  const step = TICK_STEPS_MS.find(s => span / s <= Math.max(maxTicks - 1, 1)) ?? TICK_STEPS_MS[TICK_STEPS_MS.length - 1]
  const localOffset = -new Date(t0).getTimezoneOffset() * 60e3
  const ticks: number[] = []
  for (let t = Math.ceil((t0 + localOffset) / step) * step - localOffset; t <= t1; t += step) ticks.push(t)
  return ticks
}

/** Index of the time nearest to `x` (binary search). */
export function nearestIndex(times: number[], t: number): number {
  let lo = 0
  let hi = times.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (times[mid] < t) lo = mid
    else hi = mid
  }
  return Math.abs(times[hi] - t) < Math.abs(times[lo] - t) ? hi : lo
}
