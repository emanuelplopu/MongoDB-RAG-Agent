import { useTranslation } from 'react-i18next'
import { HomeIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'
import { LocalizedLink } from '../components/LocalizedLink'
import ThemeSwitcher from '../components/ThemeSwitcher'
import LanguageSwitcher from '../components/LanguageSwitcher'

export default function NotFoundPage() {
  const { t } = useTranslation()
  
  return (
    <div className="min-h-screen flex items-center justify-center bg-background dark:bg-gray-900 px-4">
      {/* Top Bar with Language and Theme Switchers */}
      <div className="absolute top-4 right-4 z-50 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>

      <div className="text-center">
        <div className="mx-auto mb-6 w-24 h-24 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
          <ExclamationTriangleIcon className="h-12 w-12 text-red-500" />
        </div>
        <h1 className="text-6xl font-bold text-primary-900 dark:text-gray-100 mb-4">
          404
        </h1>
        <h2 className="text-2xl font-semibold text-primary-700 dark:text-gray-300 mb-4">
          {t('errors.notFound.title')}
        </h2>
        <p className="text-secondary dark:text-gray-400 mb-8 max-w-md mx-auto">
          {t('errors.notFound.message')}
        </p>
        <LocalizedLink
          to="/"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700 transition-colors font-medium"
        >
          <HomeIcon className="h-5 w-5" />
          {t('errors.notFound.goHome')}
        </LocalizedLink>
      </div>
    </div>
  )
}
