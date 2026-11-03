// ESLint flat config. It reproduces the legacy .eslintrc.json block by block.

// Patches the ESLint Linter so that every reported problem is a warning.
require('eslint-plugin-only-warn');

const js = require('@eslint/js');
const tseslint = require('@typescript-eslint/eslint-plugin');
const tsParser = require('@typescript-eslint/parser');
const angular = require('@angular-eslint/eslint-plugin');
const angularTemplate = require('@angular-eslint/eslint-plugin-template');
const angularTemplateParser = require('@angular-eslint/template-parser');
const stylistic = require('@stylistic/eslint-plugin');
const jest = require('eslint-plugin-jest');
const prettier = require('eslint-plugin-prettier');
const prettierConfig = require('eslint-config-prettier');

const tsFiles = ['**/*.ts'];

// @angular-eslint 22 removed the legacy "recommended" configs from its plugins.
// These are the rule sets of plugin:@angular-eslint/recommended and
// plugin:@angular-eslint/template/recommended in @angular-eslint 21.4.0.
// The 22 ts-recommended set adds prefer-on-push-component-change-detection, which
// this app does not adopt: its components keep ChangeDetectionStrategy.Eager.
const angularRecommendedRules = {
  '@angular-eslint/contextual-lifecycle': 'error',
  '@angular-eslint/no-empty-lifecycle-method': 'error',
  '@angular-eslint/no-input-rename': 'error',
  '@angular-eslint/no-inputs-metadata-property': 'error',
  '@angular-eslint/no-output-native': 'error',
  '@angular-eslint/no-output-on-prefix': 'error',
  '@angular-eslint/no-output-rename': 'error',
  '@angular-eslint/no-outputs-metadata-property': 'error',
  '@angular-eslint/prefer-inject': 'error',
  '@angular-eslint/prefer-standalone': 'error',
  '@angular-eslint/use-pipe-transform-interface': 'error',
  '@angular-eslint/use-lifecycle-interface': 'warn',
};
const angularTemplateRecommendedRules = {
  '@angular-eslint/template/banana-in-box': 'error',
  '@angular-eslint/template/eqeqeq': 'error',
  '@angular-eslint/template/no-negated-async': 'error',
  '@angular-eslint/template/prefer-control-flow': 'error',
};
const htmlFiles = ['**/*.html'];

/** Scope each block of a typescript-eslint flat config list to the .ts files. */
const forTs = (configs) => configs.map((config) => ({ ...config, files: tsFiles }));

module.exports = [
  {
    ignores: ['projects/**', 'build/**', 'node_modules/**', 'coverage/**', 'dist/**'],
  },
  {
    // ESLint 9 reports unused eslint-disable comments by default. ESLint 8 did not.
    linterOptions: {
      reportUnusedDisableDirectives: 'off',
    },
    plugins: {
      '@stylistic': stylistic,
      jest,
    },
  },

  // *.ts — the blocks of the legacy "extends" list, in the same order.
  {
    files: tsFiles,
    // plugin:@angular-eslint/template/process-inline-templates
    processor: angularTemplate.processors['extract-inline-html'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2020,
      sourceType: 'module',
      parserOptions: {
        project: ['tsconfig.json'],
      },
    },
    plugins: {
      '@angular-eslint': angular,
    },
    // plugin:@angular-eslint/recommended
    rules: angularRecommendedRules,
  },
  {
    files: tsFiles,
    ...js.configs.recommended,
  },
  ...forTs(tseslint.configs['flat/recommended']),
  ...forTs(tseslint.configs['flat/recommended-type-checked']),
  {
    files: tsFiles,
    rules: {
      '@stylistic/no-trailing-spaces': 'warn',
      'require-atomic-updates': 'warn',
      'no-await-in-loop': 'warn',
      'no-constructor-return': 'warn',
      'no-duplicate-imports': 'warn',
      'no-self-compare': 'warn',
      'no-unreachable-loop': 'warn',
      'consistent-this': ['warn', 'this'],
      'no-alert': 'warn',
      'no-caller': 'warn',
      'no-delete-var': 'warn',
      'eqeqeq': ['warn', 'always'],
      '@stylistic/no-extra-semi': 'warn',
      '@stylistic/no-floating-decimal': 'warn',
      'no-implicit-coercion': 'warn',
      'no-implied-eval': 'warn',
      'no-lone-blocks': 'warn',
      'no-lonely-if': 'warn',
      'no-loop-func': 'warn',
      'no-multi-assign': 'warn',
      'no-multi-str': 'warn',
      'no-new': 'warn',
      'no-new-func': 'warn',
      'no-new-wrappers': 'warn',
      'no-octal': 'warn',
      'no-octal-escape': 'warn',
      'no-proto': 'warn',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-return-assign': 'warn',
      'no-return-await': 'warn',
      'no-script-url': 'warn',
      'no-shadow-restricted-names': 'warn',
      'no-throw-literal': 'warn',
      'no-undef-init': 'warn',
      'no-unneeded-ternary': 'warn',
      'no-useless-catch': 'warn',
      'no-useless-computed-key': 'warn',
      'no-useless-concat': 'warn',
      'no-useless-escape': 'warn',
      'no-useless-rename': 'warn',
      'no-useless-return': 'warn',
      'no-with': 'warn',
      'prefer-numeric-literals': 'warn',
      'prefer-object-spread': 'warn',
      'prefer-spread': 'warn',
      'quote-props': ['warn', 'consistent-as-needed'],
      '@stylistic/quotes': ['error', 'single'],
      'radix': 'warn',
      'array-bracket-spacing': ['warn', 'never'],
      'arrow-spacing': ['warn', { before: true, after: true }],
      'block-spacing': ['warn', 'always'],
      'brace-style': ['warn', '1tbs'],
      'comma-spacing': ['warn', { before: false, after: true }],
      'comma-style': ['warn', 'last'],
      'computed-property-spacing': ['warn', 'never'],
      'camelcase': ['warn'],
      'func-call-spacing': ['warn', 'never'],
      'key-spacing': ['warn', { beforeColon: false }],
      'keyword-spacing': ['warn', { before: true }],
      '@stylistic/no-multi-spaces': 'warn',
      '@stylistic/no-tabs': ['warn'],
      'curly': 'warn',
      'padded-blocks': ['warn', 'never'],
      'rest-spread-spacing': ['warn', 'never'],
      'semi-spacing': ['warn', { before: false, after: true }],
      'space-before-blocks': ['warn', 'always'],
      '@angular-eslint/component-selector': [
        'warn',
        {
          type: 'element',
          // "prefix": "gpf",
          style: 'kebab-case',
        },
      ],
      '@angular-eslint/directive-selector': [
        'warn',
        {
          type: 'attribute',
          // "prefix": "gpf",
          style: 'camelCase',
        },
      ],
      '@typescript-eslint/await-thenable': 'warn',
      '@typescript-eslint/explicit-member-accessibility': [
        'warn',
        {
          accessibility: 'explicit',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@stylistic/dot-notation': 'off',
      '@typescript-eslint/dot-notation': 'off',
      '@typescript-eslint/explicit-function-return-type': 'warn',
      '@stylistic/no-extra-parens': ['warn', 'all', { nestedBinaryExpressions: false }],
      '@typescript-eslint/no-floating-promises': 'off',
      '@typescript-eslint/member-ordering': 'off',
      '@stylistic/semi': 'warn',
      '@typescript-eslint/no-unused-vars': 'warn',
      'indent': ['warn', 2, { SwitchCase: 1 }],
      '@stylistic/max-len': [
        'warn',
        {
          code: 120,
        },
      ],
      'no-duplicate-case': 'warn',
      'no-invalid-this': 'warn',
      '@stylistic/no-multiple-empty-lines': 'warn',
      'no-shadow': 'warn',
      'no-template-curly-in-string': 'warn',
      'no-unused-expressions': 'warn',
      '@stylistic/space-before-function-paren': ['warn', 'never'],
      '@stylistic/space-in-parens': ['warn', 'never'],
      'valid-typeof': 'warn',
      'yoda': 'warn',
      'object-shorthand': ['warn', 'never'],
      '@typescript-eslint/unbound-method': 'off',
      '@angular-eslint/prefer-standalone': 'off',
    },
  },

  // *.html — the Angular template rules.
  {
    files: htmlFiles,
    languageOptions: {
      parser: angularTemplateParser,
    },
    plugins: {
      '@angular-eslint/template': angularTemplate,
    },
    // plugin:@angular-eslint/template/recommended
    rules: angularTemplateRecommendedRules,
  },

  // *.html — plugin:prettier/recommended of eslint-plugin-prettier 4, written out.
  {
    files: htmlFiles,
    plugins: {
      prettier,
    },
    rules: {
      ...prettierConfig.rules,
      ...prettier.configs.recommended.rules,
      'prettier/prettier': 'warn',
    },
  },

  // *.spec.ts — the Jest rules.
  {
    files: ['**/*.spec.ts'],
    ...jest.configs['flat/recommended'],
  },
  {
    files: ['**/*.spec.ts'],
    rules: {
      'jest/consistent-test-it': 1,
      'jest/expect-expect': 1,
      'jest/max-nested-describe': 2,
      'jest/no-conditional-expect': 1,
      'jest/no-conditional-in-test': 1,
      'jest/no-deprecated-functions': 1,
      'jest/no-done-callback': 1,
      'jest/no-duplicate-hooks': 1,
      'jest/no-export': 1,
      'jest/no-focused-tests': 1,
      'jest/no-identical-title': 1,
      'jest/no-interpolation-in-snapshots': 1,
      'jest/no-jasmine-globals': 1,
      'jest/no-large-snapshots': 1,
      'jest/no-mocks-import': 1,
      'jest/no-standalone-expect': 1,
      'jest/no-test-return-statement': 1,
      'jest/prefer-called-with': 1,
      'jest/prefer-comparison-matcher': 1,
      'jest/prefer-equality-matcher': 1,
      'jest/prefer-expect-resolves': 1,
      'jest/prefer-hooks-in-order': 1,
      'jest/prefer-hooks-on-top': 1,
      'jest/prefer-spy-on': 1,
      'jest/prefer-strict-equal': 1,
      'jest/prefer-to-be': 1,
      'jest/prefer-to-contain': 1,
      'jest/prefer-to-have-length': 1,
      'jest/prefer-todo': 1,
      'jest/require-to-throw-message': 1,
      'jest/require-top-level-describe': 1,
      'jest/valid-describe-callback': 1,
      'jest/valid-expect': 1,
      'jest/valid-expect-in-promise': 1,
      'jest/valid-title': 1,
      'jest/no-disabled-tests': 0,
    },
  },
];
