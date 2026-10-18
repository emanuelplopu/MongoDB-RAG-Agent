import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import TimeSeriesChart, { nearestIndex, niceStep, ChartSeries } from './TimeSeriesChart'

const T0 = Date.UTC(2026, 9, 18, 10, 0, 0)
const times = [0, 1, 2, 3, 4].map(i => T0 + i * 60_000)
const formatTime = (ms: number) => new Date(ms).toISOString().slice(11, 16)

function renderChart(series: ChartSeries[], extra: Partial<Parameters<typeof TimeSeriesChart>[0]> = {}) {
  const onHoverIndex = vi.fn()
  const utils = render(
    <TimeSeriesChart title="Temperature" unit="°C" times={times} series={series} hoverIndex={null}
      onHoverIndex={onHoverIndex} emptyLabel="No data" formatTime={formatTime} {...extra} />,
  )
  return { ...utils, onHoverIndex }
}

describe('TimeSeriesChart', () => {
  it('shows a legend for two series but not for one', () => {
    const two = renderChart([
      { key: 'cpu', label: 'CPU package', slot: 1, values: [50, 52, 55, 60, 61] },
      { key: 'gpu', label: 'GPU', slot: 2, values: [40, 41, 42, 45, 47] },
    ])
    expect(screen.getByLabelText('Temperature legend')).toHaveTextContent('CPU package')
    two.unmount()

    renderChart([{ key: 'cpu', label: 'CPU', slot: 1, values: [1, 2, 3, 4, 5] }])
    expect(screen.queryByLabelText('Temperature legend')).not.toBeInTheDocument()
  })

  it('shows the empty label when no series has a value', () => {
    renderChart([{ key: 'cpu', label: 'CPU', slot: 1, values: [null, null, null, null, null] }])
    expect(screen.getByText('No data')).toBeInTheDocument()
  })

  it('breaks the line at missing values', () => {
    const { container } = renderChart([{ key: 'cpu', label: 'CPU', slot: 1, values: [10, 20, null, 30, 40] }])
    const lines = container.querySelectorAll('path[fill="none"]')
    expect(lines).toHaveLength(2)
  })

  it('labels the last value of each series', () => {
    renderChart([{ key: 'cpu', label: 'CPU', slot: 1, values: [10, 20, 30, 40, 61] }], { format: v => `${v}°` })
    expect(screen.getAllByText('61°').length).toBeGreaterThan(0)
  })

  it('shows every series in the crosshair tooltip', () => {
    renderChart([
      { key: 'cpu', label: 'CPU package', slot: 1, values: [50, 52, 55, 60, 61] },
      { key: 'gpu', label: 'GPU', slot: 2, values: [40, 41, null, 45, 47] },
    ], { hoverIndex: 2, bands: [{ start: times[1], end: times[3], label: 'Ingestion: processing' }], format: v => `${v}°` })
    const tooltip = screen.getByRole('status')
    expect(tooltip).toHaveTextContent('55°')
    expect(tooltip).toHaveTextContent('—') // GPU has no value in that bucket
    expect(tooltip).toHaveTextContent('Ingestion: processing')
  })

  it('moves the crosshair with the arrow keys', () => {
    const { onHoverIndex } = renderChart([{ key: 'cpu', label: 'CPU', slot: 1, values: [1, 2, 3, 4, 5] }])
    fireEvent.keyDown(screen.getByRole('group', { name: 'Temperature' }), { key: 'ArrowRight' })
    expect(onHoverIndex).toHaveBeenCalledWith(0)
    fireEvent.keyDown(screen.getByRole('group', { name: 'Temperature' }), { key: 'Escape' })
    expect(onHoverIndex).toHaveBeenCalledWith(null)
  })
})

describe('chart helpers', () => {
  it('niceStep picks round steps', () => {
    expect(niceStep(100)).toBe(25)
    expect(niceStep(80)).toBe(20)
    expect(niceStep(7)).toBe(2)
    expect(niceStep(0)).toBe(1)
  })

  it('nearestIndex finds the closest time', () => {
    expect(nearestIndex(times, times[2] + 10_000)).toBe(2)
    expect(nearestIndex(times, times[2] + 50_000)).toBe(3)
    expect(nearestIndex(times, 0)).toBe(0)
  })
})
