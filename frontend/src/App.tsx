import { Routes, Route, Navigate, useParams, useLocation } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext'
import { ChatSidebarProvider } from './contexts/ChatSidebarContext'
import { LanguageProvider } from './contexts/LanguageContext'
import { supportedLanguages, SupportedLanguage } from './i18n'
import Layout from './components/Layout'
import ChatPageNew from './pages/ChatPageNew'
import DashboardPage from './pages/DashboardPage'
import LandingPage from './pages/LandingPage'
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

// App routes wrapped with language provider
function AppRoutes() {
  return (
    <LanguageProvider>
      <AuthProvider>
        <ChatSidebarProvider>
          <Routes>
            {/* Root redirects to language-prefixed path */}
            <Route path="/" element={<LanguageRedirect />} />
            
            {/* Language-prefixed routes */}
            <Route path="/:lang/*" element={
              <LanguageValidation>
                <Routes>
                  <Route path="/" element={<LandingPage />} />
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/api-docs" element={<DeveloperDocsPage />} />
                  <Route path="/dashboard" element={<Layout />}>
                    <Route index element={<DashboardPage />} />
                  </Route>
                  <Route element={<Layout />}>
                    <Route path="chat" element={<ChatPageNew />} />
                    <Route path="search" element={<SearchPage />} />
                    <Route path="documents" element={<DocumentsPage />} />
                    <Route path="documents/:documentId" element={<DocumentPreviewPage />} />
                    <Route path="profiles" element={<ProfilesPage />} />
                    <Route path="system" element={<SystemPage />} />
                    <Route path="system/status" element={<StatusPage />} />
                    <Route path="system/indexes" element={<SearchIndexesPage />} />
                    <Route path="system/ingestion" element={<IngestionManagementPage />} />
                    <Route path="system/config" element={<ConfigurationPage />} />
                    <Route path="system/users" element={<UserManagementPage />} />
                    <Route path="system/prompts" element={<PromptManagementPage />} />
                    <Route path="system/api-keys" element={<APIKeysPage />} />
                    <Route path="archived-chats" element={<ArchivedChatsPage />} />
                    <Route path="cloud-sources" element={<CloudSourcesPage />} />
                    <Route path="cloud-sources/connections" element={<CloudSourceConnectionsPage />} />
                    <Route path="cloud-sources/connections/:connectionId" element={<CloudSourceConnectionsPage />} />
                    <Route path="cloud-sources/connect/:providerType" element={<CloudSourceConnectPage />} />
                    <Route path="email-cloud-config" element={<EmailCloudConfigPage />} />
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
      </AuthProvider>
    </LanguageProvider>
  )
}

function App() {
  return <AppRoutes />
}

export default App
