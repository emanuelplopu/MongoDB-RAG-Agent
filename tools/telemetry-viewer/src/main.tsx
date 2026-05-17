import './i18n'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import { DataSourceProvider } from './api/DataSourceProvider'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <HashRouter>
        <DataSourceProvider>
          <App />
        </DataSourceProvider>
      </HashRouter>
    </ThemeProvider>
  </React.StrictMode>
)
