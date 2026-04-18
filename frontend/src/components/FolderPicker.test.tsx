import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { RemoteFile, RemoteFolder } from '../api/client'
import FolderPicker from './FolderPicker'

const browseFolderMock = vi.fn()

vi.mock('../api/client', () => ({
  cloudSourcesApi: {
    browseFolder: (...args: unknown[]) => browseFolderMock(...args),
  },
}))

const rootFolder: RemoteFolder = {
  id: 'root',
  name: 'Workspace',
  path: '/',
  has_children: true,
}

const reportsFolder: RemoteFolder = {
  id: 'reports',
  name: 'Reports',
  path: '/Reports',
  parent_id: 'root',
  has_children: true,
  children_count: 2,
  modified_at: '2026-04-01T00:00:00Z',
}

const nestedFolder: RemoteFolder = {
  id: 'quarterly',
  name: 'Quarterly',
  path: '/Reports/Quarterly',
  parent_id: 'reports',
  has_children: false,
  children_count: 0,
}

const reportFile: RemoteFile = {
  id: 'file-1',
  name: 'budget.xlsx',
  path: '/Reports/budget.xlsx',
  mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  size_bytes: 2048,
  modified_at: '2026-04-02T00:00:00Z',
}

describe('FolderPicker', () => {
  beforeEach(() => {
    browseFolderMock.mockReset()
  })

  it('renders folder contents, shows files, and selects the current folder', async () => {
    browseFolderMock.mockResolvedValue({
      current_folder: rootFolder,
      folders: [reportsFolder],
      files: [reportFile],
      has_more: true,
    })
    const onSelect = vi.fn()
    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <FolderPicker
        connectionId="conn-1"
        onSelect={onSelect}
        onClose={onClose}
        showFiles
      />
    )

    expect(await screen.findByText('Reports')).toBeInTheDocument()
    expect(screen.getByText('budget.xlsx')).toBeInTheDocument()
    expect(screen.getByText('2 KB • vnd.openxmlformats-officedocument.spreadsheetml.sheet')).toBeInTheDocument()
    expect(screen.getByText('More items available. Navigate into subfolders to see them.')).toBeInTheDocument()
    expect(screen.getByText('Current: Workspace')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Select Current Folder' }))

    expect(onSelect).toHaveBeenCalledWith(rootFolder)
    expect(browseFolderMock).toHaveBeenCalledWith('conn-1', { folder_id: undefined })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('supports navigating folders and confirming multi-select choices', async () => {
    browseFolderMock
      .mockResolvedValueOnce({
        current_folder: rootFolder,
        folders: [reportsFolder],
        files: [],
        has_more: false,
      })
      .mockResolvedValueOnce({
        current_folder: reportsFolder,
        folders: [nestedFolder],
        files: [],
        has_more: false,
      })
    const onSelect = vi.fn()
    const user = userEvent.setup()

    const { container } = render(
      <FolderPicker
        connectionId="conn-2"
        onSelect={onSelect}
        onClose={vi.fn()}
        allowMultiple
      />
    )

    expect(await screen.findByText('Reports')).toBeInTheDocument()

    const reportsButtons = screen.getAllByRole('button', { name: /Reports/ })
    await user.click(reportsButtons[0])

    await waitFor(() => {
      expect(browseFolderMock).toHaveBeenNthCalledWith(2, 'conn-2', { folder_id: 'reports' })
    })

    expect(await screen.findByText('Quarterly')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reports' })).toBeInTheDocument()

    const selectQuarterlyButton = container.querySelector('button[class*="w-5"][class*="h-5"]')
    expect(selectQuarterlyButton).not.toBeNull()
    await user.click(selectQuarterlyButton)

    expect(screen.getByText('1 folder(s) selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add Selected (1)' }))

    expect(onSelect).toHaveBeenCalledWith(nestedFolder)
  })

  it('shows empty and error states', async () => {
    browseFolderMock.mockRejectedValueOnce(new Error('Access denied'))
    const { rerender } = render(
      <FolderPicker connectionId="conn-3" onSelect={vi.fn()} onClose={vi.fn()} />
    )

    expect(await screen.findByText('Access denied')).toBeInTheDocument()

    browseFolderMock.mockResolvedValueOnce({
      current_folder: nestedFolder,
      folders: [],
      files: [],
      has_more: false,
    })

    rerender(<FolderPicker connectionId="conn-3" onSelect={vi.fn()} onClose={vi.fn()} />)

    expect(await screen.findByText('This folder is empty')).toBeInTheDocument()
  })
})
