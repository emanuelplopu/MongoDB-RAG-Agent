import { Routes, Route, Navigate, useParams, useLocation } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import { AuthProvider } from './contexts/AuthProvider'
import { useTenant } from './contexts/TenantContext'
import { ChatSidebarProvider } from './contexts/ChatSidebarProvider'
import { LanguageProvider } from './contexts/LanguageProvider'
import { ThemeProvider } from './contexts/ThemeProvider'
import { ToastProvider } from './contexts/ToastProvider'
import { UserPreferencesProvider } from './contexts/UserPreferencesProvider'
import { SettingsSyncProvider } from './contexts/SettingsSyncProvider'
import { supportedLanguages, SupportedLanguage } from './i18n'
import Layout from './components/Layout'
import SessionExpiredModal from './components/SessionExpiredModal'
import ChatPageNew from './pages/ChatPageNew'
import DashboardPage from './pages/DashboardPage'
import StartPage from './components/StartPage'
import RequireAuth from './components/RequireAuth'
import SearchPage from './pages/SearchPage'
import DocumentsPage from './pages/DocumentsPage'
import DocumentPreviewPage from './pages/DocumentPreviewPage'
import ProfilesPage from './pages/ProfilesPage'
import SystemPage from './pages/SystemPage'
import StatusPage from './pages/StatusPage'
import SearchIndexesPage from './pages/SearchIndexesPage'
import IngestionManagementPage from './pages/IngestionManagementPage'
import ConfigurationPage from './pages/ConfigurationPage'
import UserManagementPage from './pages/UserManagementPage'
import CloudSourcesPage from './pages/CloudSourcesPage'
import CloudSourceConnectionsPage from './pages/CloudSourceConnectionsPage'
import CloudSourceConnectPage from './pages/CloudSourceConnectPage'
import EmailCloudConfigPage from './pages/EmailCloudConfigPage'
import PromptManagementPage from './pages/PromptManagementPage'
import DeveloperDocsPage from './pages/DeveloperDocsPage'
import APIKeysPage from './pages/APIKeysPage'
import ArchivedChatsPage from './pages/ArchivedChatsPage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import StrategiesPage from './pages/StrategiesPage'
import StrategyABTestPage from './pages/StrategyABTestPage'
import FailedDocumentsPage from './pages/FailedDocumentsPage'
import IngestionAnalyticsPage from './pages/IngestionAnalyticsPage'
import JobHistoryPage from './pages/JobHistoryPage'
import BackupManagementPage from './pages/BackupManagementPage'
import EmbeddingBenchmarkPage from './pages/EmbeddingBenchmarkPage'
import LiveDebugPage from './pages/LiveDebugPage'
import TelemetryHistoryPage from './pages/TelemetryHistoryPage'
import FeatureGuard from './components/FeatureGuard'

// Component to handle language redirect
function LanguageRedirect() {
  const location = useLocation()
  // Get preferred language from localStorage or browser
  const storedLang = localStorage.getItem('i18nextLng')
  const browserLang = navigator.language.split('-')[0]
  const preferredLang = supportedLanguages.includes(storedLang as SupportedLanguage)
    ? storedLang
    : supportedLanguages.includes(browserLang as SupportedLanguage)
      ? browserLang
      : 'en'
  
  return <Navigate to={`/${preferredLang}${location.pathname}${location.search}${location.hash}`} replace />
}

// Wrapper to validate language parameter
function LanguageValidation({ children }: { children: React.ReactNode }) {
  const { lang } = useParams<{ lang: string }>()
  const location = useLocation()
  
  // If invalid language, redirect to default
  if (!lang || !supportedLanguages.includes(lang as SupportedLanguage)) {
    const storedLang = localStorage.getItem('i18nextLng')
    const browserLang = navigator.language.split('-')[0]
    const preferredLang = supportedLanguages.includes(storedLang as SupportedLanguage)
      ? storedLang
      : supportedLanguages.includes(browserLang as SupportedLanguage)
        ? browserLang
        : 'en'
    const pathWithoutLang = location.pathname.replace(/^\/[^/]+/, '')
    return <Navigate to={`/${preferredLang}${pathWithoutLang || '/'}${location.search}${location.hash}`} replace />
  }
  
  return <>{children}</>
}

// Wrapper that provides SettingsSyncProvider with auth state and coordinates preference application
function SyncedProviders({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, showSessionModal, closeSessionModal, continueAsGuest } = useAuth()
  const { tenant } = useTenant()
  // Tenants that require a login never offer "continue as guest": RequireAuth
  // already redirects an expired session to the login page, which explains why.
  const allowGuest = !tenant.features.requireAuth

  return (
    <SettingsSyncProvider isAuthenticated={isAuthenticated}>
      <ThemeProvider>
        <LanguageProvider>
          <UserPreferencesProvider>
            {children}
          </UserPreferencesProvider>
          <SessionExpiredModal
            isOpen={showSessionModal && allowGuest}
            onClose={closeSessionModal}
            onContinueAsGuest={continueAsGuest}
            allowGuest={allowGuest}
          />
        </LanguageProvider>
      </ThemeProvider>
    </SettingsSyncProvider>
  )
}

// App routes wrapped with language provider
function AppRoutes() {
  return (
    <ToastProvider>
      <AuthProvider>
        <SyncedProviders>
          <ChatSidebarProvider>
            <Routes>
              {/* Root redirects to language-prefixed path */}
              <Route path="/" element={<LanguageRedirect />} />
            
            {/* Language-prefixed routes */}
            <Route path="/:lang/*" element={
              <LanguageValidation>
                <Routes>
                  <Route path="/" element={<StartPage />} />
                  <Route path="/login" element={<LoginPage />} />
                  {/* Everything below needs a session when the tenant sets features.requireAuth */}
                  <Route element={<RequireAuth />}>
                  <Route path="/api-docs" element={<DeveloperDocsPage />} />
                  <Route path="/dashboard" element={<Layout />}>
                    <Route index element={<DashboardPage />} />
                  </Route>
                  <Route element={<Layout />}>
                    <Route path="chat" element={<ChatPageNew />} />
                    <Route path="search" element={<SearchPage />} />
                    <Route path="documents" element={<DocumentsPage />} />
                    <Route path="documents/:documentId" element={<DocumentPreviewPage />} />
                    <Route path="profiles" element={<FeatureGuard feature="showProfiles"><ProfilesPage /></FeatureGuard>} />
                    <Route path="system" element={<SystemPage />} />
                    <Route path="system/status" element={<StatusPage />} />
                    <Route path="system/indexes" element={<SearchIndexesPage />} />
                    <Route path="system/ingestion" element={<IngestionManagementPage />} />
                    <Route path="system/ingestion/failed" element={<FailedDocumentsPage />} />
                    <Route path="system/ingestion/analytics" element={<IngestionAnalyticsPage />} />
                    <Route path="system/ingestion/history" element={<JobHistoryPage />} />
                    <Route path="system/config" element={<ConfigurationPage />} />
                    <Route path="system/users" element={<UserManagementPage />} />
                    <Route path="system/prompts" element={<PromptManagementPage />} />
                    <Route path="system/api-keys" element={<APIKeysPage />} />
                    <Route path="system/strategies" element={<FeatureGuard feature="showStrategies"><StrategiesPage /></FeatureGuard>} />
                    <Route path="system/strategies/ab-test" element={<FeatureGuard feature="showStrategies"><StrategyABTestPage /></FeatureGuard>} />
                    <Route path="system/backups" element={<FeatureGuard feature="showBackups"><BackupManagementPage /></FeatureGuard>} />
                    <Route path="system/benchmark" element={<FeatureGuard feature="showEmbeddingBenchmark"><EmbeddingBenchmarkPage /></FeatureGuard>} />
                    <Route path="system/debug" element={<LiveDebugPage />} />
                    <Route path="system/telemetry" element={<TelemetryHistoryPage />} />
                    <Route path="archived-chats" element={<ArchivedChatsPage />} />
                    <Route path="cloud-sources" element={<FeatureGuard feature="showCloudSources"><CloudSourcesPage /></FeatureGuard>} />
                    <Route path="cloud-sources/connections" element={<FeatureGuard feature="showCloudSources"><CloudSourceConnectionsPage /></FeatureGuard>} />
                    <Route path="cloud-sources/connections/:connectionId" element={<FeatureGuard feature="showCloudSources"><CloudSourceConnectionsPage /></FeatureGuard>} />
                    <Route path="cloud-sources/connect/:providerType" element={<FeatureGuard feature="showCloudSources"><CloudSourceConnectPage /></FeatureGuard>} />
                    <Route path="email-cloud-config" element={<FeatureGuard feature="showEmailConfig"><EmailCloudConfigPage /></FeatureGuard>} />
                  </Route>
                  </Route>
                  <Route path="*" element={<NotFoundPage />} />
                </Routes>
              </LanguageValidation>
            } />
            
            {/* Legacy routes redirect to language-prefixed */}
            <Route path="/login" element={<LanguageRedirect />} />
            <Route path="/dashboard" element={<LanguageRedirect />} />
            <Route path="/chat" element={<LanguageRedirect />} />
            <Route path="/search" element={<LanguageRedirect />} />
            <Route path="/documents/*" element={<LanguageRedirect />} />
            <Route path="/profiles" element={<LanguageRedirect />} />
            <Route path="/system/*" element={<LanguageRedirect />} />
            <Route path="/cloud-sources/*" element={<LanguageRedirect />} />
            <Route path="/email-cloud-config" element={<LanguageRedirect />} />
            <Route path="/archived-chats" element={<LanguageRedirect />} />
            <Route path="/api-docs" element={<LanguageRedirect />} />
            
            {/* Catch-all for unknown routes */}
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </ChatSidebarProvider>
        </SyncedProviders>
      </AuthProvider>
    </ToastProvider>
  )
}

function App() {
  return <AppRoutes />
}

export default App
