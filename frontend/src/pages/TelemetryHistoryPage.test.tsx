import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import TelemetryHistoryPage, { bucketGrid } from './TelemetryHistoryPage'

const navigateMock = vi.fn()
const historyMock = vi.fn()
const anomaliesMock = vi.fn()
const jobMock = vi.fn()
const reportHtmlMock = vi.fn()

let authState: { user: { is_admin: boolean } | null; isLoading: boolean }

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigateMock }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: unknown) => (typeof opts === 'string' ? opts : key),
    i18n: { language: 'en' },
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => {
  class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    telemetryApi: {
      history: (...a: unknown[]) => historyMock(...a),
      anomalies: (...a: unknown[]) => anomaliesMock(...a),
      job: (...a: unknown[]) => jobMock(...a),
      jobReportHtml: (...a: unknown[]) => reportHtmlMock(...a),
    },
  }
})

const START = '2026-10-18T10:00:00+00:00'
const END = '2026-10-18T10:01:00+00:00'

function hostPoint(t: string, cpu: number) {
  return {
    t, samples: 2, cpu_pct: cpu, cpu_pct_max: cpu, mem_pct: 40, mem_pct_max: 41, mem_used_bytes: 1, mem_total_bytes: 2,
    gpu_util_pct: 10, gpu_util_pct_max: 11, vram_used_bytes: 1, vram_total_bytes: 2, gpu_temp_c: 55, gpu_temp_c_max: 56,
    cpu_temp_c: 61, cpu_temp_c_max: 62, gpu_power_w: 20, gpu_power_w_max: 21, cpu_power_w: 35, cpu_power_w_max: 36,
    disk_read_bps: 0, disk_write_bps: 0, net_rx_bps: 0, net_tx_bps: 0, backend_ms_max: 3, backend_down: 0, gpu_throttled: 0,
  }
}

const history = {
  start: START,
  end: END,
  bucket_seconds: 10,
  agent_samples: 12,
  host: [hostPoint('2026-10-18T10:00:00+00:00', 30), hostPoint('2026-10-18T10:00:10+00:00', 45)],
  containers: {
    'rag-backend-quellex': [{ t: '2026-10-18T10:00:00+00:00', cpu_cores: 2.5, cpu_cores_max: 3, mem_bytes: 1, mem_bytes_max: 1 }],
  },
  activity: [{ t: '2026-10-18T10:00:00+00:00', files_ok: 3, files_failed: 1, files_other: 0, file_ms_avg: 1, chats: 2, chat_errors: 0, chat_ms_avg: 1 }],
  phases: [{ job_id: 'job-1', phase: 'processing', start: START, end: END, open: true }],
  jobs: [{ job_id: 'job-1', status: 'running', tenant: 'quellex', source: 'api', first_ts: START, last_ts: END, files: 4, failed: 1 }],
}

const anomalyReport = {
  start: START,
  end: END,
  anomalies: [{
    kind: 'memory_pressure', severity: 'warning', title: 'Host memory pressure', detail: 'RAM above 90 %', count: 12,
    episodes: [{ start: START, end: END, samples: 12, peak: 93, detail: null }], examples: [],
  }],
}

const jobReport = {
  job_id: 'job-1', status: 'completed', tenant: 'quellex', source: 'api', start: START, end: END, running: false,
  files: 4, outcomes: { ok: 3, error: 1 }, total_bytes: 1000, total_pages: 6, total_chunks: 12, files_per_hour: 240,
  phases: [{ phase: 'processing', start: START, end: END, duration_s: 60, host: { cpu_pct: 30 }, containers: {} }],
  steps: [{ step: 'convert', files: 4, p50_ms: 900, p95_ms: 1200, max_ms: 1300, total_ms: 3600, share_pct: 70 }],
  by_ext: [{ ext: 'pdf', files: 4, ok: 3, failed: 1, empty: 0, p50_ms: 900, p95_ms: 1200, max_ms: 1300, bytes: 1000, pages: 6, ms_per_page: 600 }],
  slowest: [{ path: '/app/mounts/a.pdf', ext: 'pdf', outcome: 'ok', duration_ms: 1300, size_bytes: 10, pages: 2, chunks: 3, steps: { convert: 900 }, error_type: null, error: null, ts: END }],
  failures: [], unfinished: [{ path: '/app/mounts/hang.msg', ext: 'msg', outcome: 'unfinished', duration_ms: null, size_bytes: null, pages: null, chunks: null, steps: {}, error_type: null, error: null, ts: END }],
  throughput: [{ t: START, files_ok: 3, files_failed: 1, files_other: 0, file_ms_avg: 1, chats: 0, chat_errors: 0, chat_ms_avg: null }],
  throughput_bucket_seconds: 10, anomalies: [], agent_samples: 12,
}

describe('TelemetryHistoryPage', () => {
  beforeEach(() => {
    authState = { user: { is_admin: true }, isLoading: false }
    for (const m of [navigateMock, historyMock, anomaliesMock, jobMock, reportHtmlMock]) m.mockReset()
    historyMock.mockResolvedValue(history)
    anomaliesMock.mockResolvedValue(anomalyReport)
    jobMock.mockResolvedValue(jobReport)
    try {
      localStorage.clear()
    } catch {
      /* ignore */
    }
  })

  it('redirects users who are not superadmins', () => {
    authState = { user: { is_admin: false }, isLoading: false }
    render(<TelemetryHistoryPage />)
    expect(navigateMock).toHaveBeenCalledWith('/dashboard')
    expect(historyMock).not.toHaveBeenCalled()
  })

  it('renders charts, anomalies and jobs for the default range', async () => {
    render(<TelemetryHistoryPage />)

    await waitFor(() => expect(historyMock).toHaveBeenCalledWith(6))
    expect(anomaliesMock).toHaveBeenCalledWith(6)
    for (const title of ['telemetry.chart.cpu', 'telemetry.chart.temperature', 'telemetry.chart.containers', 'telemetry.chart.activity']) {
      expect(await screen.findByText(title)).toBeInTheDocument()
    }
    expect(screen.getByText('Host memory pressure')).toBeInTheDocument()
    expect(screen.getByText('job-1')).toBeInTheDocument()
    expect(screen.queryByText('telemetry.noAgent')).not.toBeInTheDocument()
  })

  it('warns when the host agent sent no samples', async () => {
    historyMock.mockResolvedValue({ ...history, agent_samples: 0, host: [] })
    render(<TelemetryHistoryPage />)
    expect(await screen.findByText('telemetry.noAgent')).toBeInTheDocument()
  })

  it('explains a disabled history (503)', async () => {
    const { ApiError } = await import('../api/client')
    historyMock.mockRejectedValue(new (ApiError as unknown as new (m: string, s: number) => Error)('off', 503))
    render(<TelemetryHistoryPage />)
    expect(await screen.findByText('telemetry.disabled')).toBeInTheDocument()
  })

  it('refetches when the range changes', async () => {
    render(<TelemetryHistoryPage />)
    await waitFor(() => expect(historyMock).toHaveBeenCalledWith(6))
    fireEvent.click(screen.getByRole('radio', { name: 'telemetry.ranges.24h' }))
    await waitFor(() => expect(historyMock).toHaveBeenCalledWith(24))
  })

  it('switches to the table view', async () => {
    render(<TelemetryHistoryPage />)
    await screen.findByText('telemetry.chart.cpu')
    fireEvent.click(screen.getByRole('button', { name: 'telemetry.showTable' }))
    const table = screen.getByRole('table', { name: 'telemetry.tableLabel' })
    expect(table).toHaveTextContent('30.0')
    expect(table).toHaveTextContent('45.0')
  })

  it('opens a job report and downloads it with the session token', async () => {
    const createObjectURL = vi.fn(() => 'blob:report')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    reportHtmlMock.mockResolvedValue(new Blob(['<html></html>'], { type: 'text/html' }))

    render(<TelemetryHistoryPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'telemetry.openJob' }))

    expect(await screen.findByText('telemetry.job.title')).toBeInTheDocument()
    expect(jobMock).toHaveBeenCalledWith('job-1')
    expect(screen.getByText(/\/app\/mounts\/hang\.msg$/)).toBeInTheDocument() // "time · path"

    fireEvent.click(screen.getByRole('button', { name: 'telemetry.job.download' }))
    await waitFor(() => expect(reportHtmlMock).toHaveBeenCalledWith('job-1'))
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })
})

describe('bucketGrid', () => {
  it('covers the range in bucket steps aligned to the epoch', () => {
    const grid = bucketGrid('2026-10-18T10:00:03Z', '2026-10-18T10:00:31Z', 10)
    expect(grid.map(ms => new Date(ms).toISOString().slice(14, 19))).toEqual(['00:00', '00:10', '00:20', '00:30'])
  })
})
