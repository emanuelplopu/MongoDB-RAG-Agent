import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { TenantProvider } from './contexts/TenantContext'
import { ThemeProvider } from './contexts/ThemeContext'
import App from './App'
import './i18n' // Initialize i18n
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <TenantProvider>
      <ThemeProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ThemeProvider>
    </TenantProvider>
  </React.StrictMode>,
)
