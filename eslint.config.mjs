// @ts-check
import eslint from '@eslint/js';
import eslintConfigPrettier from 'eslint-config-prettier';
import eslintPluginPrettierRecommended from 'eslint-plugin-prettier/recommended';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: ['eslint.config.mjs'],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  // Prettier: 先让格式违规暴露为 error,再用 eslint-config-prettier 关掉所有与 Prettier 冲突的 ESLint 规则。
  // prettier 选项单来源在 .prettierrc——此处不再内联任何选项。
  eslintPluginPrettierRecommended,
  eslintConfigPrettier,
  // 类型感知项目服务,按文件向上找最近的 tsconfig:frontend/tsconfig.json -> frontend/src/**、frontend/vite.config.ts
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  // 前端:浏览器 ESM
  {
    files: ['frontend/src/**/*.{ts,tsx}', 'frontend/vite.config.ts'],
    languageOptions: {
      globals: { ...globals.browser },
      sourceType: 'module',
    },
  },
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-floating-promises': 'warn',
      '@typescript-eslint/no-unsafe-argument': 'warn',
    },
  },
);
