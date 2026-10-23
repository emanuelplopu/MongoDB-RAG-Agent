// Flat ESLint config (ESLint 9+). The frontend never had a lint config before
// 2026-10-23, so the rule set below is calibrated to the existing code:
// errors gate the `lint` script, warnings are reported for follow-up.
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'

export default tseslint.config(
  { ignores: ['dist', '.coverage', 'node_modules', 'insert-handler.js'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // eslint-plugin-react-hooks 7 "recommended" includes the React Compiler
      // rules; every rule is an error. Where an effect legitimately sets state
      // (data loaders, animation state machines, prop-driven resets) the site
      // carries an inline `eslint-disable` with its reason, and
      // `--report-unused-disable-directives` in the lint script keeps those
      // directives honest.
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['error', { allowConstantExport: true }],
      // Unused `catch (err)` bindings, `_`-prefixed placeholders and rest-sibling
      // destructuring (`({ node, ...props })`) are established patterns here.
      '@typescript-eslint/no-unused-vars': ['error', {
        args: 'after-used',
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrors: 'none',
        ignoreRestSiblings: true,
      }],
      '@typescript-eslint/no-explicit-any': 'error',
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    // Test files and test helpers legitimately export non-components.
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
)
