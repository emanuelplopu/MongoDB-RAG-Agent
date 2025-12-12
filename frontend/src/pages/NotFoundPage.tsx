import { Link } from 'react-router-dom'
import { HomeIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'

export default function NotFoundPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background dark:bg-gray-900 px-4">
      <div className="text-center">
        <div className="mx-auto mb-6 w-24 h-24 rounded-full bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
          <ExclamationTriangleIcon className="h-12 w-12 text-red-500" />
        </div>
        <h1 className="text-6xl font-bold text-primary-900 dark:text-gray-100 mb-4">
          404
        </h1>
        <h2 className="text-2xl font-semibold text-primary-700 dark:text-gray-300 mb-4">
          Page Not Found
        </h2>
        <p className="text-secondary dark:text-gray-400 mb-8 max-w-md mx-auto">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <Link
          to="/"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700 transition-colors font-medium"
        >
          <HomeIcon className="h-5 w-5" />
          Go Home
        </Link>
      </div>
    </div>
  )
}
