import { useState } from 'react'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import {
  ChatBubbleLeftRightIcon,
  MagnifyingGlassIcon,
  DocumentTextIcon,
  UserCircleIcon,
  Cog6ToothIcon,
  Bars3Icon,
  XMarkIcon,
  CircleStackIcon,
} from '@heroicons/react/24/outline'
import ThemeToggle from './ThemeToggle'

const navigation = [
  { name: 'Chat', href: '/chat', icon: ChatBubbleLeftRightIcon },
  { name: 'Search', href: '/search', icon: MagnifyingGlassIcon },
  { name: 'Documents', href: '/documents', icon: DocumentTextIcon },
  { name: 'Profiles', href: '/profiles', icon: UserCircleIcon },
  { name: 'System', href: '/system', icon: Cog6ToothIcon },
]

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  return (
    <div className="min-h-screen bg-background dark:bg-gray-900 transition-colors duration-200">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Mobile sidebar */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-72 transform bg-surface dark:bg-gray-800 shadow-elevation-3 transition-transform duration-300 ease-in-out lg:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-16 items-center justify-between px-6">
          <div className="flex items-center gap-3">
            <CircleStackIcon className="h-8 w-8 text-primary" />
            <span className="text-xl font-semibold text-primary-900 dark:text-primary-200">MongoDB RAG</span>
          </div>
          <button
            onClick={() => setSidebarOpen(false)}
            className="rounded-full p-2 hover:bg-surface-variant dark:hover:bg-gray-700"
          >
            <XMarkIcon className="h-6 w-6 text-secondary dark:text-gray-400" />
          </button>
        </div>
        <nav className="mt-4 px-3">
          {navigation.map((item) => (
            <NavLink
              key={item.name}
              to={item.href}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-2xl px-4 py-3 mb-1 text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                    : 'text-secondary dark:text-gray-400 hover:bg-surface-variant dark:hover:bg-gray-700'
                }`
              }
            >
              <item.icon className="h-5 w-5" />
              {item.name}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Desktop sidebar */}
      <div className="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-72 lg:flex-col">
        <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-surface dark:bg-gray-800 px-6 pb-4 shadow-elevation-1">
          <div className="flex h-16 items-center gap-3">
            <CircleStackIcon className="h-8 w-8 text-primary" />
            <span className="text-xl font-semibold text-primary-900 dark:text-primary-200">MongoDB RAG</span>
          </div>
          <nav className="flex flex-1 flex-col">
            <ul className="flex flex-1 flex-col gap-y-1">
              {navigation.map((item) => (
                <li key={item.name}>
                  <NavLink
                    to={item.href}
                    className={({ isActive }) =>
                      `group flex gap-x-3 rounded-2xl px-4 py-3 text-sm font-medium leading-6 transition-all duration-200 ${
                        isActive
                          ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                          : 'text-secondary dark:text-gray-400 hover:bg-surface-variant dark:hover:bg-gray-700 hover:text-primary-700 dark:hover:text-primary-300'
                      }`
                    }
                  >
                    <item.icon className="h-5 w-5 shrink-0" />
                    {item.name}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="mt-auto border-t border-surface-variant dark:border-gray-700 pt-4">
            <p className="text-xs text-secondary dark:text-gray-500">
              MongoDB RAG Agent v1.0
            </p>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="lg:pl-72">
        {/* Top bar */}
        <div className="sticky top-0 z-30 flex h-16 items-center gap-x-4 bg-surface/95 dark:bg-gray-800/95 px-4 shadow-elevation-1 backdrop-blur sm:gap-x-6 sm:px-6 lg:px-8">
          <button
            type="button"
            className="-m-2.5 p-2.5 text-secondary dark:text-gray-400 lg:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <Bars3Icon className="h-6 w-6" />
          </button>

          <div className="flex flex-1 items-center justify-between">
            <h1 className="text-lg font-semibold text-primary-900 dark:text-primary-200">
              {navigation.find((n) => location.pathname.startsWith(n.href))?.name || 'Chat'}
            </h1>
            <ThemeToggle />
          </div>
        </div>

        {/* Page content */}
        <main className="py-6 px-4 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

// Named export for testing
export { Layout }
