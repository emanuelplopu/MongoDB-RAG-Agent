import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './contexts/AuthContext'
import { ChatSidebarProvider } from './contexts/ChatSidebarContext'
import Layout from './components/Layout'
import ChatPageNew from './pages/ChatPageNew'
import HomePage from './pages/HomePage'
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
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'

function App() {
  return (
    <AuthProvider>
      <ChatSidebarProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<Layout />}>
            <Route index element={<HomePage />} />
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
            <Route path="cloud-sources" element={<CloudSourcesPage />} />
            <Route path="cloud-sources/connections" element={<CloudSourceConnectionsPage />} />
            <Route path="cloud-sources/connections/:connectionId" element={<CloudSourceConnectionsPage />} />
            <Route path="cloud-sources/connect/:providerType" element={<CloudSourceConnectPage />} />
            <Route path="email-cloud-config" element={<EmailCloudConfigPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </ChatSidebarProvider>
    </AuthProvider>
  )
}

export default App
