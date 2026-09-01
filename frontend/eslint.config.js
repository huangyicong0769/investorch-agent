import eslint from '@eslint/js'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  {
    ignores: ['dist', '../src/investorch/web/static'],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
)
