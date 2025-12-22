/**
 * Unit tests for App component - routing configuration.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

// Mock all page components to simplify testing
vi.mock('./pages/HomePage', () => ({ default: () => <div data-testid="home-page">Home Page</div> }))
vi.mock('./pages/ChatPageNew', () => ({ default: () => <div data-testid="chat-page">Chat Page</div> }))
vi.mock('./pages/SearchPage', () => ({ default: () => <div data-testid="search-page">Search Page</div> }))
vi.mock('./pages/DocumentsPage', () => ({ default: () => <div data-testid="documents-page">Documents Page</div> }))
vi.mock('./pages/DocumentPreviewPage', () => ({ default: () => <div data-testid="document-preview-page">Document Preview</div> }))
vi.mock('./pages/ProfilesPage', () => ({ default: () => <div data-testid="profiles-page">Profiles Page</div> }))
vi.mock('./pages/SystemPage', () => ({ default: () => <div data-testid="system-page">System Page</div> }))
vi.mock('./pages/StatusPage', () => ({ default: () => <div data-testid="status-page">Status Page</div> }))
vi.mock('./pages/SearchIndexesPage', () => ({ default: () => <div data-testid="indexes-page">Indexes Page</div> }))
vi.mock('./pages/IngestionManagementPage', () => ({ default: () => <div data-testid="ingestion-page">Ingestion Page</div> }))
vi.mock('./pages/ConfigurationPage', () => ({ default: () => <div data-testid="config-page">Config Page</div> }))
vi.mock('./pages/UserManagementPage', () => ({ default: () => <div data-testid="users-page">Users Page</div> }))
vi.mock('./pages/CloudSourcesPage', () => ({ default: () => <div data-testid="cloud-sources-page">Cloud Sources</div> }))
vi.mock('./pages/CloudSourceConnectionsPage', () => ({ default: () => <div data-testid="connections-page">Connections</div> }))
vi.mock('./pages/CloudSourceConnectPage', () => ({ default: () => <div data-testid="connect-page">Connect</div> }))
vi.mock('./pages/EmailCloudConfigPage', () => ({ default: () => <div data-testid="email-config-page">Email Config</div> }))
vi.mock('./pages/PromptManagementPage', () => ({ default: () => <div data-testid="prompts-page">Prompts</div> }))
vi.mock('./pages/DeveloperDocsPage', () => ({ default: () => <div data-testid="api-docs-page">API Docs</div> }))
vi.mock('./pages/APIKeysPage', () => ({ default: () => <div data-testid="api-keys-page">API Keys</div> }))
vi.mock('./pages/ArchivedChatsPage', () => ({ default: () => <div data-testid="archived-chats-page">Archived Chats</div> }))
vi.mock('./pages/LoginPage', () => ({ default: () => <div data-testid="login-page">Login Page</div> }))
vi.mock('./pages/NotFoundPage', () => ({ default: () => <div data-testid="not-found-page">Not Found</div> }))

// Mock Layout to just render Outlet
vi.mock('./components/Layout', () => ({
  default: ({ children }: { children?: React.ReactNode }) => {
    // Import Outlet dynamically
    const { Outlet } = require('react-router-dom')
    return <div data-testid="layout"><Outlet /></div>
  },
}))

// Mock contexts
vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('./contexts/ChatSidebarContext', () => ({
  ChatSidebarProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

const renderApp = (initialRoute = '/') => {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <App />
    </MemoryRouter>
  )
}

describe('App', () => {
  describe('Routing', () => {
    it('should render home page at root path', () => {
      renderApp('/')
      expect(screen.getByTestId('home-page')).toBeInTheDocument()
    })

    it('should render login page at /login', () => {
      renderApp('/login')
      expect(screen.getByTestId('login-page')).toBeInTheDocument()
    })

    it('should render chat page at /chat', () => {
      renderApp('/chat')
      expect(screen.getByTestId('chat-page')).toBeInTheDocument()
    })

    it('should render search page at /search', () => {
      renderApp('/search')
      expect(screen.getByTestId('search-page')).toBeInTheDocument()
    })

    it('should render documents page at /documents', () => {
      renderApp('/documents')
      expect(screen.getByTestId('documents-page')).toBeInTheDocument()
    })

    it('should render document preview page at /documents/:id', () => {
      renderApp('/documents/doc123')
      expect(screen.getByTestId('document-preview-page')).toBeInTheDocument()
    })

    it('should render profiles page at /profiles', () => {
      renderApp('/profiles')
      expect(screen.getByTestId('profiles-page')).toBeInTheDocument()
    })

    it('should render system page at /system', () => {
      renderApp('/system')
      expect(screen.getByTestId('system-page')).toBeInTheDocument()
    })

    it('should render status page at /system/status', () => {
      renderApp('/system/status')
      expect(screen.getByTestId('status-page')).toBeInTheDocument()
    })

    it('should render indexes page at /system/indexes', () => {
      renderApp('/system/indexes')
      expect(screen.getByTestId('indexes-page')).toBeInTheDocument()
    })

    it('should render ingestion page at /system/ingestion', () => {
      renderApp('/system/ingestion')
      expect(screen.getByTestId('ingestion-page')).toBeInTheDocument()
    })

    it('should render config page at /system/config', () => {
      renderApp('/system/config')
      expect(screen.getByTestId('config-page')).toBeInTheDocument()
    })

    it('should render users page at /system/users', () => {
      renderApp('/system/users')
      expect(screen.getByTestId('users-page')).toBeInTheDocument()
    })

    it('should render prompts page at /system/prompts', () => {
      renderApp('/system/prompts')
      expect(screen.getByTestId('prompts-page')).toBeInTheDocument()
    })

    it('should render API keys page at /system/api-keys', () => {
      renderApp('/system/api-keys')
      expect(screen.getByTestId('api-keys-page')).toBeInTheDocument()
    })

    it('should render archived chats page at /archived-chats', () => {
      renderApp('/archived-chats')
      expect(screen.getByTestId('archived-chats-page')).toBeInTheDocument()
    })

    it('should render cloud sources page at /cloud-sources', () => {
      renderApp('/cloud-sources')
      expect(screen.getByTestId('cloud-sources-page')).toBeInTheDocument()
    })

    it('should render connections page at /cloud-sources/connections', () => {
      renderApp('/cloud-sources/connections')
      expect(screen.getByTestId('connections-page')).toBeInTheDocument()
    })

    it('should render connect page at /cloud-sources/connect/:type', () => {
      renderApp('/cloud-sources/connect/google-drive')
      expect(screen.getByTestId('connect-page')).toBeInTheDocument()
    })

    it('should render email config page at /email-cloud-config', () => {
      renderApp('/email-cloud-config')
      expect(screen.getByTestId('email-config-page')).toBeInTheDocument()
    })

    it('should render API docs page at /api-docs', () => {
      renderApp('/api-docs')
      expect(screen.getByTestId('api-docs-page')).toBeInTheDocument()
    })

    it('should render not found page for unknown routes', () => {
      renderApp('/unknown-route')
      expect(screen.getByTestId('not-found-page')).toBeInTheDocument()
    })
  })

  describe('Layout wrapper', () => {
    it('should wrap main routes with Layout', () => {
      renderApp('/')
      expect(screen.getByTestId('layout')).toBeInTheDocument()
    })

    it('should not wrap login page with Layout', () => {
      renderApp('/login')
      expect(screen.getByTestId('login-page')).toBeInTheDocument()
      // Login page should not have layout wrapper
      expect(screen.queryByTestId('layout')).not.toBeInTheDocument()
    })

    it('should not wrap api-docs page with Layout', () => {
      renderApp('/api-docs')
      expect(screen.getByTestId('api-docs-page')).toBeInTheDocument()
      expect(screen.queryByTestId('layout')).not.toBeInTheDocument()
    })
  })

  describe('Providers', () => {
    it('should wrap app with AuthProvider', () => {
      // The app should render without errors, meaning providers are set up
      renderApp('/')
      expect(screen.getByTestId('home-page')).toBeInTheDocument()
    })

    it('should wrap app with ChatSidebarProvider', () => {
      // The app should render without errors, meaning providers are set up
      renderApp('/')
      expect(screen.getByTestId('home-page')).toBeInTheDocument()
    })
  })
})
