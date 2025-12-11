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
        // Material Design 3 color scheme
        primary: {
          DEFAULT: '#6750A4',
          50: '#F6F2FF',
          100: '#E9DDFF',
          200: '#D0BCFF',
          300: '#B69DF8',
          400: '#9A82DB',
          500: '#7F67BE',
          600: '#6750A4',
          700: '#4F378B',
          800: '#381E72',
          900: '#21005D',
        },
        secondary: {
          DEFAULT: '#625B71',
          50: '#F9F5FF',
          100: '#E8DEF8',
        },
        surface: {
          DEFAULT: '#FFFBFE',
          variant: '#E7E0EC',
        },
        background: {
          DEFAULT: '#FFFBFE',
          dark: '#1C1B1F',
        },
      },
      fontFamily: {
        sans: ['Inter', 'Roboto', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        'xl': '16px',
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
