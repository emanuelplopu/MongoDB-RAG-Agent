import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import HostStatsWidget, { formatBytes, formatUptime, worstBackup } from './HostStatsWidget'

const tMock = (key: string) => key
const hostStatsMock = vi.fn()
const overviewMock = vi.fn()

let authState: { user: { is_admin: boolean } | null }

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock, i18n: { language: 'en' } }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  systemApi: {
    hostStats: (...args: unknown[]) => hostStatsMock(...args),
  },
  telemetryApi: {
    overview: (...args: unknown[]) => overviewMock(...args),
  },
}))

vi.mock('./LocalizedLink', () => ({
  LocalizedLink: ({ to, children, className, title, 'aria-label': ariaLabel }: {
    to: string; children: React.ReactNode; className?: string; title?: string; 'aria-label'?: string
  }) => (
    <a href={to} className={className} title={title} aria-label={ariaLabel}>{children}</a>
  ),
}))

const GB = 1024 ** 3

const sources = {
  cpu: 'host', ram: 'host', disk: 'host', cpu_temp: 'host', gpu_temp: 'gpu', cpu_power: 'host',
  gpu_power: 'gpu', uptime: 'host', note: 'note',
}

const agentStats = {
  sampled_at: '2026-10-17T00:00:00Z',
  cached: false,
  source: 'host-agent',
  agent_online: true,
  agent_sample_age_seconds: 2,
  sensors_status: 'ok',
  cpu_percent: 23,
  ram_used_bytes: 8 * GB,
  ram_total_bytes: 16 * GB,
  ram_percent: 50,
  disk_path: 'C:\\',
  disk_used_bytes: 100 * GB,
  disk_total_bytes: 500 * GB,
  disk_percent: 20,
  cpu_temp_c: 61,
  gpu_temp_c: 60,
  gpu_power_watts: 20,
  cpu_power_watts: 36,
  uptime_seconds: 90000,
  gpu_name: 'Test GPU',
  gpu_percent: 5,
  vram_used_bytes: 4 * GB,
  vram_total_bytes: 16 * GB,
  containers: [
    { name: 'rag-backend-quellex', cpu_cores: 3.4, mem_bytes: 5 * GB, mem_limit_bytes: null },
    { name: 'rag-mongodb', cpu_cores: 0.25, mem_bytes: GB, mem_limit_bytes: null },
    { name: 'rag-gateway-quellex', cpu_cores: 0, mem_bytes: 1024 ** 2, mem_limit_bytes: null },
  ],
  activity: [
    {
      tenant: 'quellex', source: 'api', job_id: 'job-1', job_status: 'running', phase: 'processing',
      step: 'embed', current_file: '/app/mounts/a.pdf', processed_files: 3, total_files: 10, chat_active: false,
    },
  ],
  sources,
}

const containerStats = {
  ...agentStats,
  source: 'container',
  agent_online: false,
  agent_sample_age_seconds: null,
  sensors_status: null,
  cpu_temp_c: null,
  gpu_temp_c: null,
  gpu_power_watts: null,
  cpu_power_watts: null,
  gpu_name: null,
  containers: [],
  activity: [],
}

const overview = {
  generated_at: '2026-10-17T00:00:00Z',
  db: {
    sampled_at: '2026-10-16T23:58:00Z', age_seconds: 120, databases: 2, documents: 47823, chunks: 299988,
    data_bytes: 3.3 * GB, storage_bytes: 2.3 * GB, index_bytes: 8 * 1024 ** 2, size_on_disk_bytes: 2.32 * GB,
    oplog_bytes: 21.5 * GB, embedding_coverage_pct: 97, search_indexes_ok: true, search_lag: 12000,
  },
  anomalies: { hours: 24, critical: 1, warning: 3, info: 0, top: ['Search index not ready', 'Backup missing, old or empty'] },
  processes: [
    {
      key: 'quellex:api', tenant: 'quellex', source: 'api', host: 'api-host', last_seen_at: '2026-10-17T00:00:00Z', age_seconds: 5,
      online: true, job_id: 'job-1', job_status: 'running', phase: 'processing', step: 'embed', processed_files: 3, total_files: 10,
      files_per_hour: 1550, eta_seconds: 9 * 86400 + 11 * 3600,
    },
    {
      key: 'shared:worker', tenant: 'shared', source: 'worker', host: 'worker-host', last_seen_at: '2026-10-17T00:00:00Z', age_seconds: 12,
      online: true, job_id: null, job_status: null, phase: null, step: null, processed_files: null, total_files: null,
      files_per_hour: null, eta_seconds: null,
    },
  ],
  chat: { hours: 24, requests: 12, errors: 1, p50_ms: 20000, p95_ms: 41000 },
  backups: [
    { database: 'rag_a', profile_key: 'a', count: 2, newest_at: '2026-10-15T22:42:38Z', age_seconds: 9 * 86400, size_bytes: 0, status: 'completed' },
  ],
  llm: { sampled_at: '2026-10-17T00:00:00Z', models: [{ name: 'gemma4:12b', size_bytes: 8e9, vram_bytes: 7 * GB, ctx: 32768 }] },
}

describe('HostStatsWidget', () => {
  beforeEach(() => {
    authState = { user: { is_admin: true } }
    hostStatsMock.mockReset()
    overviewMock.mockReset()
    hostStatsMock.mockResolvedValue(agentStats)
    overviewMock.mockResolvedValue(overview)
    try {
      localStorage.clear()
    } catch {
      /* ignore */
    }
  })

  it('does not render for non-superadmin users and never polls', () => {
    authState = { user: { is_admin: false } }
    render(<HostStatsWidget />)

    expect(screen.queryByText('hostStats.title')).not.toBeInTheDocument()
    expect(hostStatsMock).not.toHaveBeenCalled()
    expect(overviewMock).not.toHaveBeenCalled()
  })

  it('does not render for guests', () => {
    authState = { user: null }
    render(<HostStatsWidget />)
    expect(screen.queryByText('hostStats.title')).not.toBeInTheDocument()
  })

  it('renders host-agent figures, activity with pace and the busiest containers', async () => {
    render(<HostStatsWidget />)

    await waitFor(() => expect(hostStatsMock).toHaveBeenCalled())
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()
    expect(screen.getByText('hostStats.sourceHost')).toBeInTheDocument()
    expect(screen.getByText('23% · 36 W')).toBeInTheDocument() // CPU utilisation + package power
    expect(screen.getByText('8.0 GB / 16.0 GB')).toBeInTheDocument() // RAM used/total
    expect(screen.getByText('5% · 4.0 GB · 20 W')).toBeInTheDocument() // GPU util, VRAM, power
    expect(screen.getByText('CPU 61°C · GPU 60°C')).toBeInTheDocument()
    expect(screen.getByText('1d 1h')).toBeInTheDocument() // uptime
    expect(await screen.findByText('hostStats.ingesting 3 / 10 (30%) · embed')).toBeInTheDocument()
    // Pace and ETA of the running job come from the overview.
    expect(await screen.findByText('1,550 hostStats.filesPerHour · hostStats.eta 9d 11h')).toBeInTheDocument()
    // Busiest containers, "rag-" prefix stripped; idle containers left out.
    expect(screen.getByText('backend-quellex')).toBeInTheDocument()
    expect(screen.getByText('3.40 hostStats.cores · 5.0 GB')).toBeInTheDocument()
    expect(screen.queryByText('gateway-quellex')).not.toBeInTheDocument()
    expect(screen.getByText('hostStats.history').closest('a')).toHaveAttribute('href', '/system/telemetry')
    expect(screen.queryByText('hostStats.caveatContainer')).not.toBeInTheDocument()
  })

  it('shows the overview rows: database, LLM, worker, chat, backup and the anomaly badge', async () => {
    render(<HostStatsWidget />)

    expect(await screen.findByText('299,988 hostStats.chunks · 2.3 GB · hostStats.indexLag 12,000 · 97% hostStats.embedded')).toBeInTheDocument()
    expect(screen.getByText('gemma4:12b · 7.0 GB hostStats.vram')).toBeInTheDocument()
    expect(screen.getByText('hostStats.worker')).toBeInTheDocument()
    expect(screen.getByText('hostStats.idle')).toBeInTheDocument()
    expect(screen.queryByText('hostStats.api')).not.toBeInTheDocument() // the online API is covered by its activity row
    expect(screen.getByText('12 hostStats.requests · p95 41.0 s')).toBeInTheDocument()
    const backup = screen.getByText('hostStats.ago · 0 B')
    expect(backup.className).toContain('text-amber-700') // 9 days old and empty
    const badge = screen.getByTitle('Search index not ready · Backup missing, old or empty')
    expect(badge).toHaveAttribute('href', '/system/telemetry')
    expect(badge).toHaveTextContent('1')
    expect(badge).toHaveTextContent('3')
    expect(badge.className).toContain('text-red-700')
  })

  it('flags an offline worker and hides the badge without findings', async () => {
    overviewMock.mockResolvedValue({
      ...overview,
      anomalies: { hours: 24, critical: 0, warning: 0, info: 2, top: [] },
      processes: [{ ...overview.processes[1], online: false, age_seconds: 7200 }],
      llm: { sampled_at: '2026-10-17T00:00:00Z', models: [] },
    })
    render(<HostStatsWidget />)

    const offline = await screen.findByText('hostStats.offline · 2h 0m')
    expect(offline.className).toContain('text-amber-700')
    expect(screen.getByText('hostStats.llmIdle')).toBeInTheDocument()
    expect(screen.queryByTitle(/Search index/)).not.toBeInTheDocument()
  })

  it('keeps the host rows when the overview fails', async () => {
    overviewMock.mockRejectedValue(new Error('nope'))
    render(<HostStatsWidget />)

    expect(await screen.findByText('23% · 36 W')).toBeInTheDocument()
    await waitFor(() => expect(overviewMock).toHaveBeenCalled())
    expect(screen.queryByText('hostStats.db')).not.toBeInTheDocument()
    expect(screen.queryByText('hostStats.backup')).not.toBeInTheDocument()
    expect(screen.getByText('hostStats.ingesting 3 / 10 (30%) · embed')).toBeInTheDocument()
  })

  it('marks the container fallback while the host agent is offline', async () => {
    hostStatsMock.mockResolvedValue(containerStats)
    render(<HostStatsWidget />)

    expect(await screen.findByText('hostStats.sourceContainer')).toBeInTheDocument()
    expect(screen.getByText('hostStats.caveatContainer')).toBeInTheDocument()
    expect(screen.getByText('23%')).toBeInTheDocument() // no power appended
    expect(screen.getAllByText('—').length).toBeGreaterThan(0) // temperature unavailable
  })

  it('can be minimised to a chip and restored', async () => {
    render(<HostStatsWidget />)
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'hostStats.collapse' }))

    expect(screen.queryByText('hostStats.title')).not.toBeInTheDocument()
    const chip = screen.getByRole('button', { name: 'hostStats.expand' })
    expect(chip).toBeInTheDocument()

    fireEvent.click(chip)
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()
  })

  it('can be dismissed', async () => {
    render(<HostStatsWidget />)
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'hostStats.dismiss' }))
    expect(screen.queryByText('hostStats.title')).not.toBeInTheDocument()
  })

  it('shows an error state when the request fails', async () => {
    hostStatsMock.mockReset()
    hostStatsMock.mockRejectedValue(new Error('boom'))

    render(<HostStatsWidget />)

    expect(await screen.findByText('hostStats.error')).toBeInTheDocument()
  })
})

describe('formatters', () => {
  it('formats bytes and uptime', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(256 * GB)).toBe('256 GB')
    expect(formatUptime(45)).toBe('45s')
    expect(formatUptime(3 * 3600 + 120)).toBe('3h 2m')
  })

  it('picks the backup that needs attention most', () => {
    const fresh = { database: 'a', profile_key: null, count: 1, newest_at: '2026-10-16T00:00:00Z', age_seconds: 3600, size_bytes: 10, status: 'completed' }
    const empty = { ...fresh, database: 'b', size_bytes: 0 }
    const none = { ...fresh, database: 'c', newest_at: null, age_seconds: null, size_bytes: null, status: null }
    const old = { ...fresh, database: 'd', age_seconds: 10 * 86400 }
    expect(worstBackup([])).toBeNull()
    expect(worstBackup([fresh, old])?.database).toBe('d')
    expect(worstBackup([fresh, old, empty])?.database).toBe('b')
    expect(worstBackup([fresh, old, empty, none])?.database).toBe('c')
  })
})
