/** Duration and progress formatting shared by the ingestion pages. */

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY

function twoUnits(big: number, bigUnit: string, small: number, smallUnit: string): string {
  return small > 0 ? `${big}${bigUnit} ${small}${smallUnit}` : `${big}${bigUnit}`
}

/**
 * A duration in seconds as a short two-unit string that stays readable at any scale:
 * "45s", "3m 20s", "2h 14m", "7d 18h", "2w 3d". Weeks start at 14 days, so a nine-day
 * job still reads "9d 6h" rather than a bare "1w". With `approx` the string gets a "~"
 * prefix and anything under a second reads "<1s". Null, NaN and negative values give "-".
 */
export function formatDuration(seconds: number | null | undefined, approx = false): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '-'
  if (approx && seconds < 1) return '<1s'
  const total = Math.round(seconds)
  let text: string
  if (total < MINUTE) text = `${total}s`
  else if (total < HOUR) text = twoUnits(Math.floor(total / MINUTE), 'm', total % MINUTE, 's')
  else if (total < DAY) text = twoUnits(Math.floor(total / HOUR), 'h', Math.floor((total % HOUR) / MINUTE), 'm')
  else if (total < 2 * WEEK) text = twoUnits(Math.floor(total / DAY), 'd', Math.floor((total % DAY) / HOUR), 'h')
  else text = twoUnits(Math.floor(total / WEEK), 'w', Math.floor((total % WEEK) / DAY), 'd')
  return approx ? `~${text}` : text
}

/** A progress share as "4.9%" (one decimal, clamped to 0..100); "-" when unknown. */
export function formatPercent(percent: number | null | undefined): string {
  if (percent == null || !Number.isFinite(percent)) return '-'
  return `${Math.min(100, Math.max(0, percent)).toFixed(1)}%`
}
