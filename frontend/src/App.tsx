import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ChatPage from './pages/ChatPage'
import SearchPage from './pages/SearchPage'
import DocumentsPage from './pages/DocumentsPage'
import ProfilesPage from './pages/ProfilesPage'
import SystemPage from './pages/SystemPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<ChatPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="profiles" element={<ProfilesPage />} />
        <Route path="system" element={<SystemPage />} />
      </Route>
    </Routes>
  )
}

export default App
