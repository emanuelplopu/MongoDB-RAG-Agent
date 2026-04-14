/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@material-tailwind/react/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: 'var(--color-primary)',
          50: 'var(--color-primary-50)',
          100: 'var(--color-primary-100)',
          200: 'var(--color-primary-200)',
          300: 'var(--color-primary-300)',
          400: 'var(--color-primary-400)',
          500: 'var(--color-primary-500)',
          600: 'var(--color-primary-600)',
          700: 'var(--color-primary-700)',
          800: 'var(--color-primary-800)',
          900: 'var(--color-primary-900)',
        },
        secondary: {
          DEFAULT: 'var(--color-secondary)',
          50: 'var(--color-secondary-50)',
          100: 'var(--color-secondary-100)',
          200: 'var(--color-secondary-200)',
          300: 'var(--color-secondary-300)',
          400: 'var(--color-secondary-400)',
          500: 'var(--color-secondary-500)',
          600: 'var(--color-secondary-600)',
          700: 'var(--color-secondary-700)',
          800: 'var(--color-secondary-800)',
          900: 'var(--color-secondary-900)',
        },
        accent: {
          DEFAULT: 'var(--color-accent)',
          50: 'var(--color-accent-50)',
          500: 'var(--color-accent-500)',
          700: 'var(--color-accent-700)',
        },
        surface: {
          DEFAULT: 'var(--color-surface)',
          variant: 'var(--color-surface-variant)',
        },
        background: {
          DEFAULT: 'var(--color-background)',
          dark: 'var(--color-background-dark)',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        display: ['var(--font-display)'],
      },
      borderRadius: {
        'xl': 'var(--radius-base)',
        '2xl': '28px',
        '3xl': '32px',
      },
      boxShadow: {
        'elevation-1': '0 1px 3px 1px rgba(0, 0, 0, 0.15), 0 1px 2px rgba(0, 0, 0, 0.3)',
        'elevation-2': '0 2px 6px 2px rgba(0, 0, 0, 0.15), 0 1px 2px rgba(0, 0, 0, 0.3)',
        'elevation-3': '0 4px 8px 3px rgba(0, 0, 0, 0.15), 0 1px 3px rgba(0, 0, 0, 0.3)',
      },
    },
  },
  plugins: [],
}
