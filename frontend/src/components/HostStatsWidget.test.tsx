import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import HostStatsWidget from './HostStatsWidget'

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

const sampleStats = {
  sampled_at: '2026-10-17T00:00:00Z',
  cached: false,
  cpu_percent: 23,
  ram_used_bytes: 8 * 1024 ** 3,
  ram_total_bytes: 16 * 1024 ** 3,
  ram_percent: 50,
  disk_path: '/app/mounts',
  disk_used_bytes: 100 * 1024 ** 3,
  disk_total_bytes: 500 * 1024 ** 3,
  disk_percent: 20,
  cpu_temp_c: null,
  gpu_temp_c: 60,
  gpu_power_watts: 20,
  cpu_power_watts: null,
  uptime_seconds: 90000,
  gpu_name: 'Test GPU',
  gpu_percent: 5,
  vram_used_bytes: 4 * 1024 ** 3,
  vram_total_bytes: 16 * 1024 ** 3,
  sources: {
    cpu: 'container', ram: 'container', disk: 'container', cpu_temp: 'host',
    gpu_temp: 'gpu', cpu_power: 'host', gpu_power: 'gpu', uptime: 'container',
    note: 'container note',
  },
}

describe('HostStatsWidget', () => {
  beforeEach(() => {
    authState = { user: { is_admin: true } }
    hostStatsMock.mockReset()
    hostStatsMock.mockResolvedValue(sampleStats)
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

  it('renders live polled values for a superadmin', async () => {
    render(<HostStatsWidget />)

    // Polls on mount.
    await waitFor(() => expect(hostStatsMock).toHaveBeenCalled())

    // Title plus humanised metric values are shown.
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()
    expect(await screen.findByText('23%')).toBeInTheDocument() // CPU
    expect(screen.getByText('60°C')).toBeInTheDocument() // GPU temp
    expect(screen.getByText('20 W')).toBeInTheDocument() // GPU power
    expect(screen.getByText('1d 1h')).toBeInTheDocument() // uptime
    expect(screen.getByText('8.0 GB / 16.0 GB')).toBeInTheDocument() // RAM used/total
  })

  it('can be minimised to a chip and restored', async () => {
    render(<HostStatsWidget />)
    expect(await screen.findByText('hostStats.title')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'hostStats.collapse' }))

    // Full card is gone; a restore chip remains.
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
