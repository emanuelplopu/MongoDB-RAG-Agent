import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import HostStatsWidget, { formatBytes, formatUptime } from './HostStatsWidget'

const tMock = (key: string) => key
const hostStatsMock = vi.fn()

let authState: { user: { is_admin: boolean } | null }

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../api/client', () => ({
  systemApi: {
    hostStats: (...args: unknown[]) => hostStatsMock(...args),
  },
}))

vi.mock('./LocalizedLink', () => ({
  LocalizedLink: ({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) => (
    <a href={to} className={className}>{children}</a>
  ),
}))

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
  ram_used_bytes: 8 * 1024 ** 3,
  ram_total_bytes: 16 * 1024 ** 3,
  ram_percent: 50,
  disk_path: 'C:\\',
  disk_used_bytes: 100 * 1024 ** 3,
  disk_total_bytes: 500 * 1024 ** 3,
  disk_percent: 20,
  cpu_temp_c: 61,
  gpu_temp_c: 60,
  gpu_power_watts: 20,
  cpu_power_watts: 36,
  uptime_seconds: 90000,
  gpu_name: 'Test GPU',
  gpu_percent: 5,
  vram_used_bytes: 4 * 1024 ** 3,
  vram_total_bytes: 16 * 1024 ** 3,
  containers: [
    { name: 'rag-backend-quellex', cpu_cores: 3.4, mem_bytes: 5 * 1024 ** 3, mem_limit_bytes: null },
    { name: 'rag-mongodb', cpu_cores: 0.25, mem_bytes: 1024 ** 3, mem_limit_bytes: null },
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

describe('HostStatsWidget', () => {
  beforeEach(() => {
    authState = { user: { is_admin: true } }
    hostStatsMock.mockReset()
    hostStatsMock.mockResolvedValue(agentStats)
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
  })

  it('does not render for guests', () => {
    authState = { user: null }
    render(<HostStatsWidget />)
    expect(screen.queryByText('hostStats.title')).not.toBeInTheDocument()
  })

  it('renders host-agent figures, activity and the busiest containers', async () => {
    render(<HostStatsWidget />)

    await waitFor(() => expect(hostStatsMock).toHaveBeenCalled())
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()
    expect(screen.getByText('hostStats.sourceHost')).toBeInTheDocument()
    expect(screen.getByText('23% · 36 W')).toBeInTheDocument() // CPU utilisation + package power
    expect(screen.getByText('8.0 GB / 16.0 GB')).toBeInTheDocument() // RAM used/total
    expect(screen.getByText('5% · 4.0 GB · 20 W')).toBeInTheDocument() // GPU util, VRAM, power
    expect(screen.getByText('CPU 61°C · GPU 60°C')).toBeInTheDocument()
    expect(screen.getByText('1d 1h')).toBeInTheDocument() // uptime
    expect(screen.getByText('hostStats.ingesting 3/10 · embed')).toBeInTheDocument()
    // Busiest containers, "rag-" prefix stripped; idle containers left out.
    expect(screen.getByText('backend-quellex')).toBeInTheDocument()
    expect(screen.getByText('3.40 hostStats.cores · 5.0 GB')).toBeInTheDocument()
    expect(screen.queryByText('gateway-quellex')).not.toBeInTheDocument()
    expect(screen.getByText('hostStats.history').closest('a')).toHaveAttribute('href', '/system/telemetry')
    expect(screen.queryByText('hostStats.caveatContainer')).not.toBeInTheDocument()
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
    expect(formatBytes(256 * 1024 ** 3)).toBe('256 GB')
    expect(formatUptime(45)).toBe('45s')
    expect(formatUptime(3 * 3600 + 120)).toBe('3h 2m')
  })
})
