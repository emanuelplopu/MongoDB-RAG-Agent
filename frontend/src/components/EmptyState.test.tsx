import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, string>) =>
      params?.query ? `${key}:${params.query}` : key,
  }),
}))

import EmptyState, {
  LoadingPlaceholder,
  NoChats,
  NoCloudSources,
  NoDocuments,
  NoFolders,
  NoNotifications,
  NoSearchResults,
} from './EmptyState'

describe('EmptyState', () => {
  it('renders the default empty state with title and description', () => {
    const { container } = render(
      <EmptyState title="Nothing here yet" description="Start by creating some content." />
    )

    expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
    expect(screen.getByText('Start by creating some content.')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('renders custom icons, size variants, and both action buttons', () => {
    const onPrimary = vi.fn()
    const onSecondary = vi.fn()

    render(
      <EmptyState
        title="Large state"
        customIcon={<span>Custom Icon</span>}
        size="lg"
        className="extra-shell"
        action={{ label: 'Create', onClick: onPrimary }}
        secondaryAction={{ label: 'Dismiss', onClick: onSecondary }}
      />
    )

    expect(screen.getByText('Custom Icon')).toBeInTheDocument()
    expect(screen.getByText('Large state').className).toContain('text-xl')
    expect(screen.getByText('Large state').closest('div')?.className).toContain('extra-shell')

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(onPrimary).toHaveBeenCalledTimes(1)
    expect(onSecondary).toHaveBeenCalledTimes(1)
  })
})

describe('EmptyState presets', () => {
  it('renders NoDocuments and calls the upload action', () => {
    const onUpload = vi.fn()
    render(<NoDocuments onUpload={onUpload} />)

    fireEvent.click(screen.getByRole('button', { name: 'emptyStates.noDocuments.action' }))

    expect(screen.getByText('emptyStates.noDocuments.title')).toBeInTheDocument()
    expect(screen.getByText('emptyStates.noDocuments.description')).toBeInTheDocument()
    expect(onUpload).toHaveBeenCalledTimes(1)
  })

  it('renders NoSearchResults with and without a query', () => {
    const onClear = vi.fn()
    const { rerender } = render(<NoSearchResults query="contract" onClear={onClear} />)

    expect(screen.getByText('emptyStates.noResults.description:contract')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'emptyStates.noResults.action' }))
    expect(onClear).toHaveBeenCalledTimes(1)

    rerender(<NoSearchResults />)
    expect(screen.getByText('emptyStates.noResults.descriptionNoQuery')).toBeInTheDocument()
  })

  it('renders chat, folder, cloud, and notification preset variants', () => {
    const onNewChat = vi.fn()
    const onCreate = vi.fn()
    const onConnect = vi.fn()

    const { rerender } = render(<NoChats onNewChat={onNewChat} />)
    fireEvent.click(screen.getByRole('button', { name: 'emptyStates.noChats.action' }))
    expect(onNewChat).toHaveBeenCalledTimes(1)
    expect(screen.getByText('emptyStates.noChats.description')).toBeInTheDocument()

    rerender(<NoFolders onCreate={onCreate} />)
    fireEvent.click(screen.getByRole('button', { name: 'emptyStates.noFolders.action' }))
    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(screen.getByText('emptyStates.noFolders.title')).toBeInTheDocument()

    rerender(<NoCloudSources onConnect={onConnect} />)
    fireEvent.click(screen.getByRole('button', { name: 'emptyStates.noCloudSources.action' }))
    expect(onConnect).toHaveBeenCalledTimes(1)
    expect(screen.getByText('emptyStates.noCloudSources.description')).toBeInTheDocument()

    rerender(<NoNotifications />)
    expect(screen.getByText('emptyStates.noNotifications.title')).toBeInTheDocument()
    expect(screen.getByText('emptyStates.noNotifications.description')).toBeInTheDocument()
  })

  it('renders the loading placeholder with default and custom messages', () => {
    const { rerender } = render(<LoadingPlaceholder />)

    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(document.querySelector('.animate-spin')).not.toBeNull()

    rerender(<LoadingPlaceholder message="Syncing data" className="loading-shell" />)
    expect(screen.getByText('Syncing data')).toBeInTheDocument()
    expect(screen.getByText('Syncing data').closest('div')?.className).toContain('loading-shell')
  })
})
