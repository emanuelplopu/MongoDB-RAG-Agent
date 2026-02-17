import { useTranslation } from 'react-i18next'
import {
  MagnifyingGlassIcon,
  DocumentTextIcon,
  ChatBubbleLeftRightIcon,
  FolderOpenIcon,
  CloudIcon,
  SparklesIcon,
  ArrowRightIcon,
  CheckCircleIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { LocalizedLink } from '../components/LocalizedLink'
import ThemeSwitcher from '../components/ThemeSwitcher'
import LanguageSwitcher from '../components/LanguageSwitcher'

// Feature card data with translation keys
const features = [
  {
    titleKey: 'landing.features.aiSearch.title',
    descriptionKey: 'landing.features.aiSearch.description',
    icon: MagnifyingGlassIcon,
    color: 'from-blue-500 to-cyan-500',
    bgLight: 'bg-blue-50 dark:bg-blue-900/20',
    iconColor: 'text-blue-600',
  },
  {
    titleKey: 'landing.features.multiFormat.title',
    descriptionKey: 'landing.features.multiFormat.description',
    icon: DocumentTextIcon,
    color: 'from-green-500 to-emerald-500',
    bgLight: 'bg-green-50 dark:bg-green-900/20',
    iconColor: 'text-green-600',
  },
  {
    titleKey: 'landing.features.conversational.title',
    descriptionKey: 'landing.features.conversational.description',
    icon: ChatBubbleLeftRightIcon,
    color: 'from-purple-500 to-violet-500',
    bgLight: 'bg-purple-50 dark:bg-purple-900/20',
    iconColor: 'text-purple-600',
  },
  {
    titleKey: 'landing.features.profiles.title',
    descriptionKey: 'landing.features.profiles.description',
    icon: FolderOpenIcon,
    color: 'from-orange-500 to-amber-500',
    bgLight: 'bg-orange-50 dark:bg-orange-900/20',
    iconColor: 'text-orange-600',
  },
  {
    titleKey: 'landing.features.cloud.title',
    descriptionKey: 'landing.features.cloud.description',
    icon: CloudIcon,
    color: 'from-indigo-500 to-blue-500',
    bgLight: 'bg-indigo-50 dark:bg-indigo-900/20',
    iconColor: 'text-indigo-600',
  },
]

// Benefits list with translation keys
const benefitKeys = [
  'landing.benefits.security',
  'landing.benefits.selfHosted',
  'landing.benefits.llmProviders',
  'landing.benefits.realtime',
  'landing.benefits.apiFirst',
]

export default function LandingPage() {
  const { isAuthenticated, user } = useAuth()
  const { t } = useTranslation()

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-surface-variant dark:from-gray-900 dark:to-gray-800">
      {/* Top Bar with Language and Theme Switchers */}
      <div className="absolute top-4 right-4 z-50 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>

      {/* Hero Section */}
      <div className="relative overflow-hidden">
        {/* Background decoration */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          <div className="absolute -top-40 -right-40 w-80 h-80 bg-primary/10 rounded-full blur-3xl" />
          <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-purple-500/10 rounded-full blur-3xl" />
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-16 pb-20">
          <div className="text-center">
            {/* Logo and Title */}
            <div className="flex justify-center mb-6">
              <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 shadow-xl">
                <svg className="h-12 w-12" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 10h6c2.76 0 5 2.24 5 5s-2.24 5-5 5h-4" stroke="white" strokeWidth="2.5" strokeLinecap="round" fill="none"/>
                  <path d="M14 18l-3 3-3-3" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
                  <path d="M11 21v2" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
                </svg>
              </div>
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-primary-900 dark:text-white mb-6">
              {t('landing.welcomeTo')}{' '}
              <span className="bg-gradient-to-r from-indigo-500 to-purple-600 bg-clip-text text-transparent">
                {t('common.appName')}
              </span>
            </h1>

            <p className="text-xl text-secondary dark:text-gray-400 max-w-3xl mx-auto mb-10">
              {t('landing.subtitle')}
            </p>

            {/* CTA Buttons */}
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              {isAuthenticated ? (
                <>
                  <LocalizedLink
                    to="/dashboard"
                    className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 text-white font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5"
                  >
                    <SparklesIcon className="h-6 w-6" />
                    {t('landing.goToDashboard')}
                    <ArrowRightIcon className="h-5 w-5" />
                  </LocalizedLink>
                  <LocalizedLink
                    to="/chat"
                    className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-surface dark:bg-gray-700 text-primary-900 dark:text-white font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5 border border-surface-variant dark:border-gray-600"
                  >
                    <ChatBubbleLeftRightIcon className="h-6 w-6" />
                    {t('landing.startChat')}
                  </LocalizedLink>
                </>
              ) : (
                <>
                  <LocalizedLink
                    to="/login"
                    className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 text-white font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5"
                  >
                    {t('landing.getStarted')}
                    <ArrowRightIcon className="h-5 w-5" />
                  </LocalizedLink>
                  <LocalizedLink
                    to="/login"
                    className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-surface dark:bg-gray-700 text-primary-900 dark:text-white font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5 border border-surface-variant dark:border-gray-600"
                  >
                    {t('nav.signIn')}
                  </LocalizedLink>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Features Section */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
        <div className="text-center mb-16">
          <h2 className="text-3xl sm:text-4xl font-bold text-primary-900 dark:text-white mb-4">
            {t('landing.featuresTitle')}
          </h2>
          <p className="text-lg text-secondary dark:text-gray-400 max-w-2xl mx-auto">
            {t('landing.featuresSubtitle')}
          </p>
        </div>

        <div className="grid gap-8 md:grid-cols-2 lg:grid-cols-3">
          {features.map((feature) => (
            <div
              key={feature.titleKey}
              className="group relative bg-surface dark:bg-gray-800 rounded-2xl p-6 shadow-elevation-1 hover:shadow-elevation-3 transition-all duration-300 hover:-translate-y-1"
            >
              {/* Gradient border effect on hover */}
              <div className={`absolute inset-0 rounded-2xl bg-gradient-to-r ${feature.color} opacity-0 group-hover:opacity-100 transition-opacity duration-300 -z-10 blur-sm`} />
              <div className="absolute inset-[1px] rounded-2xl bg-surface dark:bg-gray-800" />
              
              <div className="relative">
                <div className={`inline-flex items-center justify-center w-14 h-14 rounded-xl ${feature.bgLight} mb-4`}>
                  <feature.icon className={`h-7 w-7 ${feature.iconColor}`} />
                </div>

                <h3 className="text-xl font-semibold text-primary-900 dark:text-white mb-3">
                  {t(feature.titleKey)}
                </h3>

                <p className="text-secondary dark:text-gray-400 leading-relaxed">
                  {t(feature.descriptionKey)}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Benefits Section */}
      <div className="bg-surface dark:bg-gray-800/50 border-y border-surface-variant dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
          <div className="grid lg:grid-cols-2 gap-12 items-center">
            <div>
              <h2 className="text-3xl font-bold text-primary-900 dark:text-white mb-6">
                {t('landing.benefitsTitle')}
              </h2>
              <p className="text-lg text-secondary dark:text-gray-400 mb-8">
                {t('landing.benefitsSubtitle')}
              </p>

              <ul className="space-y-4">
                {benefitKeys.map((benefitKey) => (
                  <li key={benefitKey} className="flex items-start gap-3">
                    <CheckCircleIcon className="h-6 w-6 text-green-500 flex-shrink-0 mt-0.5" />
                    <span className="text-primary-900 dark:text-gray-200">{t(benefitKey)}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="relative">
              <div className="bg-gradient-to-br from-indigo-500/20 to-purple-600/20 rounded-2xl p-8">
                <div className="bg-surface dark:bg-gray-800 rounded-xl shadow-xl p-6 space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="w-3 h-3 rounded-full bg-red-500" />
                    <div className="w-3 h-3 rounded-full bg-yellow-500" />
                    <div className="w-3 h-3 rounded-full bg-green-500" />
                  </div>
                  <div className="space-y-3">
                    <div className="h-4 bg-surface-variant dark:bg-gray-700 rounded w-3/4" />
                    <div className="h-4 bg-surface-variant dark:bg-gray-700 rounded w-1/2" />
                    <div className="h-4 bg-primary/20 rounded w-5/6" />
                    <div className="h-4 bg-surface-variant dark:bg-gray-700 rounded w-2/3" />
                  </div>
                  <div className="flex items-center gap-2 text-sm text-secondary dark:text-gray-400 pt-2">
                    <SparklesIcon className="h-4 w-4 text-primary" />
                    {t('landing.aiResponse')}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* CTA Section */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
        <div className="relative bg-gradient-to-r from-indigo-500 to-purple-600 rounded-3xl p-8 sm:p-12 overflow-hidden">
          {/* Background decoration */}
          <div className="absolute inset-0 overflow-hidden pointer-events-none">
            <div className="absolute top-0 right-0 w-64 h-64 bg-white/10 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />
            <div className="absolute bottom-0 left-0 w-64 h-64 bg-white/10 rounded-full blur-3xl translate-y-1/2 -translate-x-1/2" />
          </div>

          <div className="relative text-center">
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              {t('landing.ctaTitle')}
            </h2>
            <p className="text-lg text-white/80 max-w-2xl mx-auto mb-8">
              {isAuthenticated 
                ? t('landing.ctaSubtitleAuth', { name: user?.name?.split(' ')[0] })
                : t('landing.ctaSubtitleGuest')}
            </p>

            {isAuthenticated ? (
              <LocalizedLink
                to="/dashboard"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-white text-primary-900 font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5"
              >
                <SparklesIcon className="h-6 w-6" />
                {t('landing.goToDashboard')}
                <ArrowRightIcon className="h-5 w-5" />
              </LocalizedLink>
            ) : (
              <LocalizedLink
                to="/login"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-white text-primary-900 font-semibold text-lg shadow-lg hover:shadow-xl transition-all hover:-translate-y-0.5"
              >
                {t('landing.getStartedFree')}
                <ArrowRightIcon className="h-5 w-5" />
              </LocalizedLink>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-surface-variant dark:border-gray-700">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex flex-col sm:flex-row justify-between items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                <svg className="h-5 w-5" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 10h6c2.76 0 5 2.24 5 5s-2.24 5-5 5h-4" stroke="white" strokeWidth="2.5" strokeLinecap="round" fill="none"/>
                  <path d="M14 18l-3 3-3-3" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
                  <path d="M11 21v2" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
                </svg>
              </div>
              <span className="font-semibold text-primary-900 dark:text-white">{t('common.appName')}</span>
            </div>

            <p className="text-sm text-secondary dark:text-gray-400">
              {t('landing.footerTagline')}
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
