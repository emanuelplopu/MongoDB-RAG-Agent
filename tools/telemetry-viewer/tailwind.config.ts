import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#1a1b26',
          light: '#24283b',
          lighter: '#2f3347',
        },
        accent: {
          blue: '#7aa2f7',
          purple: '#bb9af7',
          green: '#9ece6a',
          orange: '#ff9e64',
          red: '#f7768e',
          teal: '#73daca',
          yellow: '#e0af68',
        },
      },
    },
  },
  plugins: [],
} satisfies Config
