import { useEffect, useMemo, useRef, useState, KeyboardEvent, PointerEvent } from 'react'
import { nearestIndex, niceStep, timeTicks } from './chartMath'

/** Categorical slot (fixed order; colors come from the `.viz-root` CSS tokens in index.css). */
export type SeriesSlot = 1 | 2 | 3 | 4

export interface ChartSeries {
  key: string
  label: string
  slot: SeriesSlot
  /** One value per entry of `times`; null = no data (the line breaks). */
  values: Array<number | null>
}

export interface ChartBand {
  start: number
  end: number
  label: string
}

export interface TimeSeriesChartProps {
  title: string
  /** Unit shown under the title and used by the default formatter. */
  unit: string
  /** Shared x positions (epoch ms), ascending. */
  times: number[]
  series: ChartSeries[]
  /** Background stretches (e.g. ingestion phases), drawn as a neutral wash. */
  bands?: ChartBand[]
  /** Fixed y maximum (e.g. 100 for percentages); auto otherwise. */
  yMax?: number
  /** Counts: whole-number y ticks only. */
  integer?: boolean
  /** Plot height in px (the axis band is added on top, so nothing is clipped). */
  height?: number
  /** Index into `times` highlighted by the crosshair (shared across charts). */
  hoverIndex: number | null
  onHoverIndex: (index: number | null) => void
  format?: (value: number) => string
  /** Shown instead of the plot when no series has a value. */
  emptyLabel: string
  /** Formats the crosshair time and the x ticks. */
  formatTime: (ms: number, span: number) => string
}

const PAD = { left: 44, right: 64, top: 10, bottom: 24 }
const SLOT_VAR: Record<SeriesSlot, string> = {
  1: 'var(--viz-series-1)',
  2: 'var(--viz-series-2)',
  3: 'var(--viz-series-3)',
  4: 'var(--viz-series-4)',
}

function defaultFormat(unit: string) {
  return (v: number) => `${v >= 100 ? Math.round(v) : v.toFixed(1)}${unit === '%' ? '%' : ` ${unit}`}`
}

/**
 * Small-multiple line chart for telemetry: one unit per chart (never two y-axes), thin 2px lines,
 * a 10% area wash for a single series, hairline grid, legend for 2+ series plus end-value labels,
 * a crosshair shared through `hoverIndex`, a tooltip listing every series, keyboard navigation
 * (arrow keys) and line breaks across missing buckets.
 */
export default function TimeSeriesChart({
  title,
  unit,
  times,
  series,
  bands = [],
  yMax,
  integer = false,
  height = 140,
  hoverIndex,
  onHoverIndex,
  format,
  emptyLabel,
  formatTime,
}: TimeSeriesChartProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  const fmt = format ?? defaultFormat(unit)

  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) setWidth(Math.round(w))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const visible = series.filter(s => s.values.some(v => v !== null && Number.isFinite(v)))
  const plotW = Math.max(width - PAD.left - PAD.right, 40)
  const t0 = times[0] ?? 0
  const t1 = times[times.length - 1] ?? 1
  const span = Math.max(t1 - t0, 1)

  const { top, step } = useMemo(() => {
    const max = Math.max(0, ...visible.flatMap(s => s.values.filter((v): v is number => v !== null && Number.isFinite(v))))
    const hi = yMax ?? (max > 0 ? max * 1.1 : 1)
    const st = integer ? Math.max(1, Math.round(niceStep(hi))) : niceStep(hi)
    return { top: yMax ?? Math.ceil(hi / st) * st, step: st }
  }, [visible, yMax, integer])

  const x = (t: number) => PAD.left + ((t - t0) / span) * plotW
  const y = (v: number) => PAD.top + height - (Math.min(Math.max(v, 0), top) / top) * height
  const bucket = times.length > 1 ? Math.min(...times.slice(1).map((t, i) => t - times[i])) : span

  /** Point runs that break at nulls and at gaps longer than 2.5 buckets. */
  const segments = (values: Array<number | null>): Array<Array<[number, number]>> => {
    const runs: Array<Array<[number, number]>> = []
    let current: Array<[number, number]> = []
    let prevT: number | null = null
    values.forEach((v, i) => {
      const t = times[i]
      const missing = v === null || !Number.isFinite(v)
      if (missing || (prevT !== null && t - prevT > bucket * 2.5)) {
        if (current.length) runs.push(current)
        current = []
      }
      if (!missing) {
        current.push([x(t), y(v as number)])
        prevT = t
      } else {
        prevT = null
      }
    })
    if (current.length) runs.push(current)
    return runs
  }
  const linePath = (run: Array<[number, number]>) =>
    run.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join('')
  const areaPath = (run: Array<[number, number]>) =>
    `${linePath(run)}L${run[run.length - 1][0].toFixed(1)},${y(0).toFixed(1)}L${run[0][0].toFixed(1)},${y(0).toFixed(1)}Z`

  const yTicks: number[] = []
  for (let v = 0; v <= top + 1e-9; v += step) yTicks.push(Number(v.toFixed(6)))
  const xTicks = timeTicks(t0, t1, Math.max(2, Math.min(7, Math.floor(plotW / 100))))

  // End labels: last value of each series; drop a label that would collide (legend + tooltip carry it).
  const endLabels = visible
    .map(s => {
      let i = s.values.length - 1
      while (i >= 0 && (s.values[i] === null || !Number.isFinite(s.values[i] as number))) i--
      return i >= 0 ? { s, i, v: s.values[i] as number, py: y(s.values[i] as number) } : null
    })
    .filter((l): l is { s: ChartSeries; i: number; v: number; py: number } => l !== null)
    .sort((a, b) => a.py - b.py)
    .filter((label, idx, all) => idx === 0 || label.py - all[idx - 1].py >= 13)

  const onPointerMove = (e: PointerEvent<SVGRectElement>) => {
    if (times.length === 0) return
    const rect = (e.currentTarget.ownerSVGElement ?? e.currentTarget).getBoundingClientRect()
    const px = e.clientX - rect.left
    const t = t0 + ((px - PAD.left) / plotW) * span
    onHoverIndex(nearestIndex(times, t))
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (times.length === 0) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      const base = hoverIndex ?? (e.key === 'ArrowRight' ? -1 : times.length)
      onHoverIndex(Math.min(Math.max(base + (e.key === 'ArrowRight' ? 1 : -1), 0), times.length - 1))
    } else if (e.key === 'Escape') {
      onHoverIndex(null)
    }
  }

  const hoverT = hoverIndex !== null && hoverIndex < times.length ? times[hoverIndex] : null
  const hoverBand = hoverT !== null ? bands.find(b => hoverT >= b.start && hoverT <= b.end) : undefined
  const tooltipLeft = hoverT !== null && x(hoverT) > width * 0.6
  const svgHeight = PAD.top + height + PAD.bottom

  return (
    <div className="viz-root rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h3 className="text-sm font-semibold" style={{ color: 'var(--viz-text-primary)' }}>{title}</h3>
          <p className="text-xs" style={{ color: 'var(--viz-text-secondary)' }}>{unit}</p>
        </div>
        {visible.length >= 2 && (
          <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs" style={{ color: 'var(--viz-text-secondary)' }} aria-label={`${title} legend`}>
            {visible.map(s => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="inline-block h-0.5 w-3 rounded-full" style={{ background: SLOT_VAR[s.slot] }} />
                {s.label}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div
        ref={wrapRef}
        className="relative outline-hidden focus-visible:ring-2 focus-visible:ring-primary/50 rounded-sm"
        tabIndex={visible.length ? 0 : -1}
        onKeyDown={onKeyDown}
        onBlur={() => onHoverIndex(null)}
        role="group"
        aria-label={title}
      >
        {visible.length === 0 ? (
          <div className="flex items-center justify-center text-xs" style={{ height: svgHeight, color: 'var(--viz-muted)' }}>
            {emptyLabel}
          </div>
        ) : (
          <svg width="100%" height={svgHeight} viewBox={`0 0 ${width} ${svgHeight}`} preserveAspectRatio="none" aria-hidden="true">
            {bands.map((b, i) => (
              <rect key={`band-${i}`} x={x(Math.max(b.start, t0))} y={PAD.top}
                width={Math.max(x(Math.min(b.end, t1)) - x(Math.max(b.start, t0)), 1)} height={height} fill="var(--viz-band)" />
            ))}
            {yTicks.map(v => (
              <g key={`y-${v}`}>
                <line x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} stroke={v === 0 ? 'var(--viz-axis)' : 'var(--viz-grid)'} strokeWidth={1} />
                <text x={PAD.left - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--viz-muted)" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(v)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => {
              const px = x(t)
              const anchor = px - PAD.left < 24 ? 'start' : PAD.left + plotW - px < 24 ? 'end' : 'middle'
              return (
                <text key={`x-${i}`} x={px} y={PAD.top + height + 16} fontSize={10} fill="var(--viz-muted)"
                  textAnchor={anchor} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {formatTime(t, span)}
                </text>
              )
            })}
            {visible.length === 1 && segments(visible[0].values).map((run, i) => (
              <path key={`area-${i}`} d={areaPath(run)} fill={SLOT_VAR[visible[0].slot]} opacity={0.1} />
            ))}
            {visible.map(s => segments(s.values).map((run, i) => (
              <path key={`${s.key}-${i}`} d={linePath(run)} fill="none" stroke={SLOT_VAR[s.slot]} strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round" />
            )))}
            {endLabels.map(({ s, i, v, py }) => (
              <g key={`end-${s.key}`}>
                <circle cx={x(times[i])} cy={py} r={4} fill={SLOT_VAR[s.slot]} stroke="var(--viz-surface)" strokeWidth={2} />
                <text x={PAD.left + plotW + 8} y={py + 3} fontSize={11} fill="var(--viz-text-secondary)" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(v)}
                </text>
              </g>
            ))}
            {hoverT !== null && (
              <g>
                <line x1={x(hoverT)} x2={x(hoverT)} y1={PAD.top} y2={PAD.top + height} stroke="var(--viz-crosshair)" strokeWidth={1} />
                {visible.map(s => {
                  const v = s.values[hoverIndex as number]
                  return v === null || !Number.isFinite(v) ? null : (
                    <circle key={`hover-${s.key}`} cx={x(hoverT)} cy={y(v)} r={4} fill={SLOT_VAR[s.slot]} stroke="var(--viz-surface)" strokeWidth={2} />
                  )
                })}
              </g>
            )}
            <rect x={PAD.left} y={PAD.top} width={plotW} height={height} fill="transparent"
              onPointerMove={onPointerMove} onPointerLeave={() => onHoverIndex(null)} />
          </svg>
        )}

        {hoverT !== null && visible.length > 0 && (
          <div
            role="status"
            className="pointer-events-none absolute top-1 z-10 min-w-36 rounded-lg border border-gray-200 bg-white/95 px-2.5 py-1.5 text-xs shadow-md dark:border-gray-700 dark:bg-gray-900/95"
            style={tooltipLeft ? { right: width - x(hoverT) + 10 } : { left: x(hoverT) + 10 }}
          >
            <div style={{ color: 'var(--viz-text-secondary)' }}>{formatTime(hoverT, 0)}</div>
            {hoverBand && <div style={{ color: 'var(--viz-text-secondary)' }}>{hoverBand.label}</div>}
            {visible.map(s => {
              const v = s.values[hoverIndex as number]
              return (
                <div key={`tip-${s.key}`} className="flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block h-0.5 w-3 rounded-full" style={{ background: SLOT_VAR[s.slot] }} />
                  <span className="font-semibold" style={{ color: 'var(--viz-text-primary)', fontVariantNumeric: 'tabular-nums' }}>
                    {v === null || !Number.isFinite(v) ? '—' : fmt(v)}
                  </span>
                  <span style={{ color: 'var(--viz-text-secondary)' }}>{s.label}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
