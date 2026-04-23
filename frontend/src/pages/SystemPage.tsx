import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ChartBarIcon,
  MagnifyingGlassCircleIcon,
  ArrowPathIcon,
  WrenchScrewdriverIcon,
  BeakerIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { useTranslation } from 'react-i18next'

// System page now serves as a hub to the 4 specialized pages
const systemPages = [
  { key: 'status', href: '/system/status', icon: ChartBarIcon, color: 'bg-blue-500' },
  { key: 'indexes', href: '/system/indexes', icon: MagnifyingGlassCircleIcon, color: 'bg-green-500' },
  { key: 'ingestion', href: '/system/ingestion', icon: ArrowPathIcon, color: 'bg-purple-500' },
  { key: 'config', href: '/system/config', icon: WrenchScrewdriverIcon, color: 'bg-orange-500' },
  { key: 'benchmark', href: '/system/benchmark', icon: BeakerIcon, color: 'bg-cyan-500' },
]

export default function SystemPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { user, isLoading: authLoading } = useAuth()
  
  // Admin-only access check
  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate('/dashboard')
    }
  }, [user, authLoading, navigate])
  
  // Show loading while checking auth
  if (authLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }
  
  // Redirect non-admins
  if (!user?.is_admin) {
    return null
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-display font-semibold text-primary-900 dark:text-gray-200">{t('systemPage.title')}</h2>
        <p className="text-sm text-primary-700 dark:text-gray-400">
          {t('systemPage.subtitle')}
        </p>
      </div>

      {/* Page Grid */}
      <div className="grid gap-6 md:grid-cols-2">
        {systemPages.map((page) => (
          <button
            key={page.key}
            onClick={() => navigate(page.href)}
            className="flex items-start gap-4 rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1 text-left transition-all hover:shadow-elevation-2 hover:scale-[1.02]"
          >
            <div className={`rounded-xl ${page.color} p-3 text-white`}>
              <page.icon className="h-6 w-6" />
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-medium text-primary-900 dark:text-gray-200">
                {t(`systemPage.pages.${page.key}.name`)}
              </h3>
              <p className="text-sm text-secondary dark:text-gray-400 mt-1">
                {t(`systemPage.pages.${page.key}.desc`)}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

// Named export for testing
export { SystemPage }
