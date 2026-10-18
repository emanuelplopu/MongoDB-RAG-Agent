/** Formatting and axis helpers shared by the telemetry history views. */

/** Every bucket start in [start, end) — the shared x axis, so missing buckets become gaps (or zeros for counts). */
export function bucketGrid(startIso: string, endIso: string, bucketSeconds: number): number[] {
  const step = bucketSeconds * 1000
  const first = Math.floor(Date.parse(startIso) / step) * step
  const end = Date.parse(endIso)
  const out: number[] = []
  for (let t = first; t < end && out.length < 5000; t += step) out.push(t)
  return out
}

/** "850 ms", "12.4 s", "3 min 05 s", "2 h 14 min". */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${String(Math.round(s % 60)).padStart(2, '0')} s`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

/** Bytes, base 1024: "512 B", "1.5 GB". */
export function formatBytesShort(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const e = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const v = bytes / Math.pow(1024, e)
  return `${v.toFixed(v >= 100 || e === 0 ? 0 : 1)} ${units[e]}`
}

/** A number with one decimal, or a dash. */
export function formatNumber(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toFixed(digits)
}

/**
 * Time formatters for a locale: axis ticks (time of day for spans up to 20 h, else day + time, so
 * a 24 h axis never shows the same clock time twice) and a full timestamp for tooltips (span 0).
 */
export function makeTimeFormat(locale: string) {
  const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' })
  const dayTime = new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  const full = new Intl.DateTimeFormat(locale, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  return {
    axis: (ms: number, span: number) => (span === 0 ? full.format(ms) : span <= 20 * 3600 * 1000 ? time.format(ms) : dayTime.format(ms)),
    iso: (iso: string) => full.format(Date.parse(iso)),
  }
}
