import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
const translations: Record<string, string> = {
  'devDocsPage.title': 'Developer API Documentation',
  'devDocsPage.subtitle': 'Explore and test the API',
  'devDocsPage.swaggerUi': 'Swagger UI',
  'devDocsPage.swaggerDesc': 'Interactive API explorer',
  'devDocsPage.redoc': 'ReDoc',
  'devDocsPage.redocDesc': 'Beautiful API reference',
  'devDocsPage.openApiSpec': 'OpenAPI Spec',
  'devDocsPage.openApiDesc': 'Download the specification',
  'devDocsPage.quickStart': 'Quick Start',
  'devDocsPage.baseUrl': 'Base URL',
  'devDocsPage.authMethods': 'Authentication',
  'devDocsPage.authIntro': 'Two authentication methods are supported',
  'devDocsPage.recommended': 'Recommended',
  'devDocsPage.method1Title': 'API Key',
  'devDocsPage.method1Desc': 'Use an API key for external apps',
  'devDocsPage.method2Title': 'JWT Token',
  'devDocsPage.method2Desc': 'Use JWT for browser sessions',
  'devDocsPage.commonOps': 'Common Operations',
  'devDocsPage.searchKb': 'Search Knowledge Base',
  'devDocsPage.chatAgent': 'Chat with Agent',
  'devDocsPage.startIngestion': 'Start Ingestion',
  'devDocsPage.systemStatus': 'System Status',
  'devDocsPage.apiReference': 'API Reference',
  'devDocsPage.searchEndpoints': 'Search endpoints...',
  'devDocsPage.loadingSpec': 'Loading specification...',
  'devDocsPage.loadFailed': 'Failed to load API specification',
  'devDocsPage.retry': 'Retry',
  'devDocsPage.tryIt': 'Try it',
  'devDocsPage.noEndpoints': 'No endpoints found',
  'devDocsPage.clientLibraries': 'Client Libraries',
  'devDocsPage.rateLimits': 'Rate Limits',
  'devDocsPage.rateSearch': 'Search: 60 requests/min',
  'devDocsPage.rateChat': 'Chat: 30 requests/min',
  'devDocsPage.rateIngestion': 'Ingestion: 10 requests/min',
  'devDocsPage.rateTimeout': 'Timeout: 30 seconds',
  'devDocsPage.bestPractices': 'Best Practices',
  'devDocsPage.practiceHybrid': 'Use hybrid search',
  'devDocsPage.practiceTokens': 'Monitor token usage',
  'devDocsPage.practiceRateLimit': 'Respect rate limits',
  'devDocsPage.practiceStreaming': 'Use streaming for chat',
}
const stableT = (key: string, params?: Record<string, unknown>) => {
  if (key === 'devDocsPage.endpointCount') return `${params?.count ?? 0} endpoints`
  return translations[key] ?? key
}
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: stableT, i18n: { language: 'en', changeLanguage: async () => {} } }),
  Trans: ({ children }: { children?: unknown }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}))
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
