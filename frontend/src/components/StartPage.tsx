import { Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import { useTenant } from '../contexts/TenantContext'
import { useLocalizedPath } from '../hooks/useLocalizedNavigate'
import LandingPage from '../pages/LandingPage'

/**
 * What the root URL renders, decided by `tenant.features.startPage`.
 *
 * - `landing`: the tenant's marketing landing page (RecallHub default).
 * - `login`: guests go straight to the login page, signed-in users straight to
 *   the dashboard. The desktop app and network clients both open the root URL,
 *   so this is the single place that defines the start screen for both.
 */
export default function StartPage() {
  const { tenant } = useTenant()
  const { isAuthenticated, isLoading } = useAuth()
  const getLocalizedPath = useLocalizedPath()
  const { t } = useTranslation()

  if (tenant.features.startPage !== 'login') {
    return <LandingPage />
  }

  if (isLoading) {
    return (
      <div
        className="flex items-center justify-center min-h-screen bg-background dark:bg-gray-900"
        role="status"
        aria-live="polite"
        data-testid="start-page-loading"
      >
        <div className="text-center">
          <div className="animate-spin h-10 w-10 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-secondary dark:text-gray-400">{t('common.loading')}</p>
        </div>
      </div>
    )
  }

  return <Navigate to={getLocalizedPath(isAuthenticated ? '/dashboard' : '/login')} replace />
}
