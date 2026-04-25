export function initEditor(): void {
  const monaco = window['monaco'] as typeof import('monaco-editor');

  monaco.editor.defineTheme('annotationPipelineTheme', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      {
        foreground: '#dd8108ff',
        token: 'type'
      },

      {
        foreground: '#85a2b9',
        token: 'string'
      },
      {
        foreground: '#85a2b9',
        token: 'number'
      },
      {
        foreground: '#2f404eff',
        token: 'keyword'
      },
      {
        foreground: '#75715e',
        token: 'comment'
      },
    ],
    colors: {
      'editor.foreground': '#2f404eff',
      'editor.background': '#FFFFFF',
      'editor.selectionForeground': '#915b15ff',
      'editor.selectionBackground': '#e7e6e4ff',
      'editor.inactiveSelectionBackground': '#ebeae8ff',
      'editor.lineHighlightBackground': '#f0efe9b0',
      'editorCursor.foreground': '#383838ff',
      'editorWhitespace.foreground': '#c9d2ddff',
      'editor.wordHighlightBackground': '#e9e6dfff',
      'scrollbar.shadow': '#c9d2ddff',
      'scrollbarSlider.background': '#dfdfdfa2',
      'scrollbarSlider.hoverBackground': '#b3bbc583',
      'scrollbarSlider.activeBackground': '#b3bbc583',
      'editorIndentGuide.background1': '#dbdbdbe0',
      'editorIndentGuide.activeBackground1': '#a4b6c7ff',
    }
  });

  monaco.editor.setTheme('annotationPipelineTheme');
}

export const editorConfig = {
  language: 'yaml',
  minimap: {
    enabled: false
  },
  lineNumbers: 'off',
  folding: false,
  stickyScroll: {
    enabled: false,
  },
  scrollBeyondLastLine: false,
  theme: 'annotationPipelineTheme',
  automaticLayout: true,
};