import { useTheme } from '../contexts/ThemeContext'
import { SunIcon, MoonIcon, ComputerDesktopIcon } from '@heroicons/react/24/outline'

type ThemeOption = 'light' | 'dark' | 'system'

const themeOptions: { value: ThemeOption; icon: typeof SunIcon; label: string }[] = [
  { value: 'light', icon: SunIcon, label: 'Light' },
  { value: 'dark', icon: MoonIcon, label: 'Dark' },
  { value: 'system', icon: ComputerDesktopIcon, label: 'System' },
]

export default function ThemeToggle() {
  const { theme, setTheme } = useTheme()

  return (
    <div className="flex items-center gap-1 rounded-full bg-surface-variant dark:bg-gray-700 p-1">
      {themeOptions.map((option) => {
        const Icon = option.icon
        const isActive = theme === option.value
        return (
          <button
            key={option.value}
            onClick={() => setTheme(option.value)}
            className={`flex items-center justify-center rounded-full p-2 transition-all duration-200 ${
              isActive
                ? 'bg-primary text-white shadow-sm'
                : 'text-secondary dark:text-gray-400 hover:bg-white/50 dark:hover:bg-gray-600'
            }`}
            title={option.label}
            aria-label={`Switch to ${option.label} mode`}
          >
            <Icon className="h-4 w-4" />
          </button>
        )
      })}
    </div>
  )
}
