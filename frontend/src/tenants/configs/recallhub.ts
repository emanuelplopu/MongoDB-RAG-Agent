import { TenantConfig } from '../types'

export const recallhubConfig: TenantConfig = {
  id: 'recallhub',
  branding: {
    appName: 'RecallHub',
    tagline: 'Your intelligent knowledge base',
    logoUrl: '/tenants/recallhub/logo.svg',
    iconUrl: '/tenants/recallhub/favicon.svg',
    faviconUrl: '/tenants/recallhub/favicon.svg',
  },
  theme: {
    colors: {
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
        200: '#C9C0D4',
        300: '#ABA2B7',
        400: '#8D849A',
        500: '#71687D',
        600: '#625B71',
        700: '#4A4458',
        800: '#332D41',
        900: '#1D192B',
      },
      surface: {
        DEFAULT: '#FFFBFE',
        variant: '#E7E0EC',
      },
      background: {
        DEFAULT: '#FFFBFE',
        dark: '#1C1B1F',
      },
      gradient: {
        from: '#6366f1',
        to: '#8b5cf6',
      },
    },
    fonts: {
      sans: ['Inter', 'Roboto', 'system-ui', 'sans-serif'],
    },
    borderRadius: {
      base: '16px',
      button: '28px',
      input: '16px',
    },
    darkMode: {
      background: '#1C1B1F',
      surface: '#2B2930',
      text: '#E6E1E5',
    },
  },
  features: {
    showCloudSources: true,
    showEmailConfig: true,
    showProfiles: true,
    showStrategies: true,
    showEmbeddingBenchmark: true,
    showBackups: true,
    showApiDocs: true,
    showWebSearch: true,
    landingPageVariant: 'default',
  },
  content: {
    i18nOverrides: {},
  },
}
