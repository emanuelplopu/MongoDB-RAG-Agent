import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DeveloperDocsPage from './DeveloperDocsPage'

const fetchMock = vi.fn()

const openApiSpec = {
  openapi: '3.1.0',
  paths: {
    '/auth/login': {
      post: {
        tags: ['Auth'],
        summary: 'Log a user in',
      },
    },
    '/documents': {
      get: {
        tags: ['Documents'],
        summary: 'List documents',
      },
    },
    '/documents/{id}': {
      delete: {
        tags: ['Documents'],
        description: 'Delete a document',
      },
    },
    '/health': {
      get: {
        summary: 'Health check',
      },
    },
  },
}

describe('DeveloperDocsPage', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads the OpenAPI spec, groups endpoints by tag, and filters by tag/search', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => openApiSpec,
    })
    const user = userEvent.setup()

    render(<DeveloperDocsPage />)

    expect(await screen.findByText('Developer API Documentation')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/openapi.json')
    expect(screen.getByText('4 endpoints')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Auth (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Documents (2)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Other (1)' })).toBeInTheDocument()

    expect(screen.getByText('/auth/login')).toBeInTheDocument()
    expect(screen.getByText('Log a user in')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Try it' })).toHaveAttribute('href', '/docs#/Auth/post__auth_login')

    await user.click(screen.getByRole('button', { name: 'Documents (2)' }))
    expect(await screen.findByText('/documents')).toBeInTheDocument()
    expect(screen.getByText('/documents/{id}')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Search endpoints...'), 'delete')
    expect(screen.getByText('/documents/{id}')).toBeInTheDocument()
    expect(screen.queryByText('/documents')).not.toBeInTheDocument()
    expect(screen.getByText('Delete a document')).toBeInTheDocument()
  })

  it('shows a retryable error for invalid specifications and recovers on retry', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ paths: {} }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => openApiSpec,
      })
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()

    render(<DeveloperDocsPage />)

    expect(await screen.findByText('Failed to load API specification')).toBeInTheDocument()
    expect(screen.getByText('Invalid OpenAPI specification')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('/auth/login')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    consoleErrorSpy.mockRestore()
  })

  it('surfaces HTTP fetch failures and keeps the quick-start content visible', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
    })
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<DeveloperDocsPage />)

    expect(await screen.findByText('Failed to load API specification')).toBeInTheDocument()
    expect(screen.getByText('HTTP 503: Service Unavailable')).toBeInTheDocument()
    expect(screen.getByText('Quick Start')).toBeInTheDocument()
    expect(screen.getByText('Rate Limits')).toBeInTheDocument()
    expect(screen.getByText('Client Libraries')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })
})
