import { describe, expect, it } from 'vitest'
import { formatDuration, formatPercent } from './format'

describe('formatDuration', () => {
  it('scales from seconds to weeks with at most two units', () => {
    expect(formatDuration(0)).toBe('0s')
    expect(formatDuration(45.4)).toBe('45s')
    expect(formatDuration(200)).toBe('3m 20s')
    expect(formatDuration(2 * 3600 + 14 * 60 + 59)).toBe('2h 14m')
    expect(formatDuration(29 * 3600)).toBe('1d 5h')
    expect(formatDuration(11176 * 60)).toBe('7d 18h')
    expect(formatDuration(13 * 86400 + 6 * 3600)).toBe('13d 6h')
    expect(formatDuration(14 * 86400)).toBe('2w')
    expect(formatDuration(17 * 86400 + 3600)).toBe('2w 3d')
  })

  it('drops a zero second unit and rounds whole seconds', () => {
    expect(formatDuration(59.6)).toBe('1m')
    expect(formatDuration(7200)).toBe('2h')
  })

  it('prefixes estimates with a tilde', () => {
    expect(formatDuration(0.4, true)).toBe('<1s')
    expect(formatDuration(75, true)).toBe('~1m 15s')
    expect(formatDuration(3 * 86400, true)).toBe('~3d')
  })

  it('returns a dash for unknown values', () => {
    expect(formatDuration(null)).toBe('-')
    expect(formatDuration(undefined)).toBe('-')
    expect(formatDuration(Number.NaN)).toBe('-')
    expect(formatDuration(-5)).toBe('-')
  })
})

describe('formatPercent', () => {
  it('shows one decimal clamped to 0..100', () => {
    expect(formatPercent(4.9014)).toBe('4.9%')
    expect(formatPercent(0)).toBe('0.0%')
    expect(formatPercent(100)).toBe('100.0%')
    expect(formatPercent(103)).toBe('100.0%')
    expect(formatPercent(null)).toBe('-')
  })
})
