/** VS Code Dark+–style theme for Monaco (no extra npm packages). */
export const VSCODE_DARK_PLUS_THEME = "vscode-dark-plus";

let registered = false;

export function registerVscodeDarkPlusTheme(monacoApi) {
  if (registered) return;
  registered = true;

  monacoApi.editor.defineTheme(VSCODE_DARK_PLUS_THEME, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6A9955", fontStyle: "italic" },
      { token: "comment.doc", foreground: "6A9955", fontStyle: "italic" },
      { token: "string", foreground: "CE9178" },
      { token: "string.escape", foreground: "D7BA7D" },
      { token: "regexp", foreground: "D16969" },
      { token: "number", foreground: "B5CEA8" },
      { token: "number.hex", foreground: "B5CEA8" },
      { token: "keyword", foreground: "569CD6" },
      { token: "keyword.control", foreground: "C586C0" },
      { token: "keyword.flow", foreground: "C586C0" },
      { token: "type", foreground: "4EC9B0" },
      { token: "type.identifier", foreground: "4EC9B0" },
      { token: "class", foreground: "4EC9B0" },
      { token: "interface", foreground: "4EC9B0" },
      { token: "namespace", foreground: "4EC9B0" },
      { token: "function", foreground: "DCDCAA" },
      { token: "method", foreground: "DCDCAA" },
      { token: "variable", foreground: "9CDCFE" },
      { token: "variable.predefined", foreground: "4FC1FF" },
      { token: "constant", foreground: "4FC1FF" },
      { token: "tag", foreground: "569CD6" },
      { token: "attribute.name", foreground: "9CDCFE" },
      { token: "attribute.value", foreground: "CE9178" },
      { token: "delimiter", foreground: "D4D4D4" },
      { token: "operator", foreground: "D4D4D4" },
      { token: "metatag", foreground: "569CD6" },
      { token: "metatag.content.html", foreground: "CE9178" },
      { token: "invalid", foreground: "F44747" },
    ],
    colors: {
      "editor.background": "#1E1E1E",
      "editor.foreground": "#D4D4D4",
      "editor.lineHighlightBackground": "#2A2D2E",
      "editor.selectionBackground": "#264F78",
      "editor.inactiveSelectionBackground": "#3A3D41",
      "editorCursor.foreground": "#AEAFAD",
      "editorLineNumber.foreground": "#858585",
      "editorLineNumber.activeForeground": "#C6C6C6",
      "editorIndentGuide.background": "#404040",
      "editorIndentGuide.activeBackground": "#707070",
      "editorWhitespace.foreground": "#3B3A32",
      "editorWidget.background": "#252526",
      "editorWidget.border": "#454545",
      "minimap.background": "#1E1E1E",
      "scrollbarSlider.background": "#79797966",
      "scrollbarSlider.hoverBackground": "#646464B3",
      "scrollbarSlider.activeBackground": "#BFBFBF66",
    },
  });
}
