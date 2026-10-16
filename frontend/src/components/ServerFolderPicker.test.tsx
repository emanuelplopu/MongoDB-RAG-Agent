import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ServerFolderPicker from './ServerFolderPicker'

const browseFoldersMock = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, paramsOrFallback?: Record<string, unknown> | string) => {
      if (typeof paramsOrFallback === 'string') return paramsOrFallback
      if (key === 'profiles.folderListTruncated') return `Only the first ${paramsOrFallback?.count} folders are shown`
      return key
    },
  }),
}))

vi.mock('../api/client', () => ({
  profilesApi: {
    browseFolders: (...args: unknown[]) => browseFoldersMock(...args),
  },
}))

const rootsListing = {
  roots: ['/app/mounts', '/app/documents'],
  path: null,
  parent: null,
  entries: [
    { name: '/app/mounts', path: '/app/mounts', exists: true },
    { name: '/app/documents', path: '/app/documents', exists: false },
  ],
  truncated: false,
}

const mountsListing = {
  roots: ['/app/mounts', '/app/documents'],
  path: '/app/mounts',
  parent: null,
  entries: [{ name: 'kanzlei', path: '/app/mounts/kanzlei', exists: true }],
  truncated: false,
}

const kanzleiListing = {
  roots: ['/app/mounts', '/app/documents'],
  path: '/app/mounts/kanzlei',
  parent: '/app/mounts',
  entries: [],
  truncated: true,
}

describe('ServerFolderPicker', () => {
  beforeEach(() => {
    browseFoldersMock.mockReset()
    browseFoldersMock.mockImplementation(async (path?: string | null) => {
      if (!path) return rootsListing
      if (path === '/app/mounts') return mountsListing
      if (path === '/app/mounts/kanzlei') return kanzleiListing
      throw new Error(`Folder '${path}' is not reachable from the server`)
    })
  })

  it('starts at the roots, flags unmounted roots, and cannot select a root listing', async () => {
    render(<ServerFolderPicker onSelect={vi.fn()} onClose={vi.fn()} />)

    expect(await screen.findByText('/app/mounts')).toBeInTheDocument()
    expect(screen.getByText('not mounted')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\/app\/documents/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Use this folder' })).toBeDisabled()
    expect(browseFoldersMock).toHaveBeenCalledWith(null)
  })

  it('navigates down and up and reports the chosen folder', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ServerFolderPicker onSelect={onSelect} onClose={vi.fn()} />)

    await user.click(await screen.findByText('/app/mounts'))
    await user.click(await screen.findByText('kanzlei'))
    expect(await screen.findByText('No sub-folders')).toBeInTheDocument()
    expect(screen.getByText('Only the first 0 folders are shown')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Use this folder' }))
    expect(onSelect).toHaveBeenCalledWith('/app/mounts/kanzlei')

    await user.click(screen.getByRole('button', { name: 'Up' }))
    await waitFor(() => expect(browseFoldersMock).toHaveBeenLastCalledWith('/app/mounts'))
    await user.click(screen.getByRole('button', { name: 'Document roots' }))
    await waitFor(() => expect(browseFoldersMock).toHaveBeenLastCalledWith(null))
  })

  it('opens at the initial path and shows server errors', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<ServerFolderPicker onSelect={vi.fn()} onClose={onClose} initialPath="/app/mounts/gone" />)

    expect(await screen.findByText("Folder '/app/mounts/gone' is not reachable from the server")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Use this folder' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
  })
})
