import { memo, useEffect, useMemo, useState } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { yaml as yamlLang, yamlLanguage } from '@codemirror/lang-yaml';
import { json as jsonLang } from '@codemirror/lang-json';
import { autocompletion } from '@codemirror/autocomplete';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import type { Extension } from '@codemirror/state';
import type { JSONSchema7 } from 'json-schema';

interface Props {
  value: string;
  /** Read-only today. Editing is a flag flip, not a rewrite. */
  readOnly?: boolean;
  /** Model files are YAML; the evidence bundle is JSON. */
  language?: 'yaml' | 'json';
  /** Handed the view once mounted, so a toolbar can drive the editor. */
  onReady?: (view: EditorView) => void;
  onChange?: (next: string) => void;
}

/** The schema is fetched once per page load and shared by every editor.
 *  It only changes when the backend does. */
let schemaPromise: Promise<JSONSchema7 | null> | null = null;

function loadSchema(): Promise<JSONSchema7 | null> {
  schemaPromise ??= fetch('/api/schema/model-file')
    .then((r) => (r.ok ? (r.json() as Promise<JSONSchema7>) : null))
    .catch(() => null);
  return schemaPromise;
}

/** Schema validation and completion, loaded only when they can be used.
 *
 * `codemirror-json-schema` costs ~160KB gzipped, largely because its entry
 * points re-export a hover tooltip that renders markdown through Shiki, and
 * the package does not declare `sideEffects: false` so none of it tree-shakes.
 * None of it is worth anything while the file is read-only -- the YAML was
 * rendered from validated pydantic models minutes earlier -- so it stays out
 * of the main bundle and arrives only once someone is actually editing.
 */
async function schemaExtensions(): Promise<Extension[]> {
  const [core, yamlSchemaMod, lint, schema] = await Promise.all([
    import('codemirror-json-schema'),
    import('codemirror-json-schema/yaml'),
    import('@codemirror/lint'),
    loadSchema(),
  ]);
  if (!schema) return [];
  return [
    lint.linter(yamlSchemaMod.yamlSchemaLinter(), { needsRefresh: core.handleRefresh }),
    yamlLanguage.data.of({ autocomplete: yamlSchemaMod.yamlCompletion() }),
    // The schema reaches the linter and completion through editor state.
    core.stateExtensions(schema),
  ];
}

/* Colours come from the design tokens rather than a packaged CodeMirror theme,
 * so the editor reads as part of this app instead of importing a second visual
 * language. Declared dark so CodeMirror's own defaults -- the cursor, the
 * search panel, the autocomplete popup -- pick their dark variants. */
const theme = EditorView.theme(
  {
    '&': {
      fontSize: '12.5px',
      backgroundColor: 'var(--code-bg)',
      color: 'var(--code-ink)',
    },
    '&.cm-focused': { outline: 'none' },
    '.cm-content': { caretColor: 'var(--violet-soft)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--violet-soft)' },
    '.cm-scroller': {
      fontFamily: 'var(--mono)',
      lineHeight: '1.65',
      padding: 'var(--s3) 0',
    },
    '.cm-gutters': {
      backgroundColor: 'var(--code-bg)',
      border: 'none',
      color: 'var(--code-ink-faint)',
      paddingRight: 'var(--s2)',
    },
    '.cm-activeLine': { backgroundColor: 'var(--code-hover)' },
    '.cm-activeLineGutter': {
      backgroundColor: 'transparent',
      color: 'var(--code-ink-muted)',
    },
    '.cm-foldPlaceholder': {
      backgroundColor: 'var(--code-chrome)',
      border: '1px solid var(--code-rule)',
      color: 'var(--code-ink-muted)',
      borderRadius: '4px',
      padding: '0 6px',
    },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection': {
      backgroundColor: '#39306b',
    },
    '.cm-panels': {
      backgroundColor: 'var(--code-chrome)',
      color: 'var(--code-ink)',
      border: 'none',
    },
    '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--code-rule)' },
    '.cm-panel.cm-search': {
      padding: '8px 10px',
      fontFamily: 'var(--sans)',
      fontSize: '12px',
      display: 'flex',
      flexWrap: 'wrap',
      alignItems: 'center',
      gap: '6px',
    },
    '.cm-panel.cm-search label': {
      display: 'inline-flex',
      alignItems: 'center',
      gap: '4px',
      color: 'var(--code-ink-muted)',
    },
    '.cm-panel input, .cm-panel button': {
      backgroundColor: 'var(--code-bg)',
      color: 'var(--code-ink)',
      border: '1px solid var(--code-rule)',
      borderRadius: '5px',
      padding: '4px 8px',
      fontFamily: 'var(--sans)',
      fontSize: '12px',
    },
    '.cm-panel input[type=checkbox]': { padding: '0', accentColor: 'var(--violet)' },
    '.cm-panel button:hover': {
      backgroundColor: 'var(--code-hover)',
      borderColor: 'var(--code-ink-faint)',
      cursor: 'pointer',
    },
    '.cm-panel input:focus-visible': {
      outline: '2px solid var(--violet)',
      outlineOffset: '1px',
    },
    // The close button is an absolutely positioned bare [x].
    '.cm-panel.cm-search [name=close]': {
      backgroundColor: 'transparent',
      border: 'none',
      color: 'var(--code-ink-muted)',
      fontSize: '15px',
    },
    '.cm-searchMatch': { backgroundColor: '#4a3f18' },
    '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: '#6d5c1f' },
    '.cm-tooltip': {
      backgroundColor: 'var(--code-chrome)',
      border: '1px solid var(--code-rule)',
      color: 'var(--code-ink)',
    },
    '.cm-lintRange-error': {
      backgroundImage: 'none',
      borderBottom: '2px dotted var(--code-invalid)',
    },
    '.cm-lintRange-warning': {
      backgroundImage: 'none',
      borderBottom: '2px dotted var(--code-atom)',
    },
  },
  { dark: true },
);

/* Tag-for-tag against the grammar in @lezer/yaml, not a generic guess. YAML
 * has no number or boolean token: every unquoted scalar, from `true` to a
 * whole paragraph of description, arrives as `Literal -> tags.content`. It
 * gets the brightest ink because descriptions are the most-read text here.
 * Anything left unstyled falls through to CodeMirror's default highlight
 * style, which is written for a light background and turns grey on grey. */
const highlight = HighlightStyle.define(
  [
    // Keys: `Key/Literal Key/QuotedLiteral`.
    { tag: tags.definition(tags.propertyName), color: 'var(--code-key)', fontWeight: '500' },
    /* JSON keys use the bare tag. The YAML rule above is more specific, so it
     * still wins for `definition(propertyName)`. */
    { tag: tags.propertyName, color: 'var(--code-key)', fontWeight: '500' },
    // Every unquoted YAML value, including multi-line descriptions.
    { tag: [tags.content, tags.attributeValue], color: 'var(--code-ink)' },
    // JSON has real numbers, booleans and null; YAML does not.
    { tag: tags.number, color: 'var(--code-number)' },
    { tag: [tags.bool, tags.null], color: 'var(--code-atom)' },
    // Only genuinely quoted values, such as the join SQL.
    { tag: tags.string, color: 'var(--code-string)' },
    { tag: tags.special(tags.string), color: 'var(--code-string)' },
    { tag: tags.lineComment, color: 'var(--code-comment)', fontStyle: 'italic' },
    {
      tag: [tags.separator, tags.punctuation, tags.squareBracket, tags.brace],
      color: 'var(--code-punct)',
    },
    { tag: [tags.labelName, tags.typeName, tags.keyword], color: 'var(--code-atom)' },
    { tag: tags.meta, color: 'var(--code-ink-faint)' },
    { tag: tags.invalid, color: 'var(--code-invalid)' },
  ],
  { themeType: 'dark' },
);

function CodeViewInner({
  value,
  readOnly = true,
  language = 'yaml',
  onReady,
  onChange,
}: Props) {
  const [schemaExt, setSchemaExt] = useState<Extension[]>([]);

  useEffect(() => {
    // The schema describes model files, so it means nothing for the bundle.
    if (readOnly || language !== 'yaml') return;
    let live = true;
    schemaExtensions().then((extensions) => {
      if (live) setSchemaExt(extensions);
    });
    return () => {
      live = false;
    };
  }, [readOnly, language]);

  /* Without the schema the file is still fully readable and coloured, just
   * not validated. The dark theme is NOT in here: it goes through the `theme`
   * prop below. */
  /* A fresh object here would reconfigure the whole editor on every keystroke:
   * `basicSetup` sits in useCodeMirror's dependency array, and that effect
   * dispatches StateEffect.reconfigure -- rebuilding line numbers, folding,
   * history, search, autocompletion and the schema linter each time. */
  const basicSetup = useMemo(
    () => ({
      lineNumbers: true,
      foldGutter: true,
      highlightActiveLine: !readOnly,
      highlightActiveLineGutter: !readOnly,
      // CodeMirror virtualises long documents, so the browser's own find can
      // miss lines that are not currently rendered. Its search stays on.
      searchKeymap: true,
      // Configured in `extensions` instead, to control when it fires.
      autocompletion: false,
    }),
    [readOnly],
  );

  const extensions = useMemo<Extension[]>(
    () => [
      language === 'json' ? jsonLang() : yamlLang(),
      syntaxHighlighting(highlight),
      /* Completion on demand rather than on every character. The schema source
       * re-parses the document and resolves the schema at the cursor, which is
       * too much to do between keystrokes. Ctrl+Space still opens it. */
      ...(readOnly ? [] : [autocompletion({ activateOnTyping: false })]),
      ...schemaExt,
    ],
    [language, readOnly, schemaExt],
  );

  return (
    <CodeMirror
      value={value}
      /* Read-only through EditorState, never through `editable`. The latter
       * sets contenteditable="false" on the content, which cannot take
       * keyboard focus -- so no keybinding fires, Ctrl+F falls through to the
       * browser, and the text is not properly selectable. `readOnly` blocks
       * changes while keeping the editor focusable and navigable. */
      readOnly={readOnly}
      /* Passed as `theme`, not as an extension. Left to its default this
       * component appends its own `defaultLightThemeOption` (a hard `#fff`
       * background) ahead of any extension we supply, and in CodeMirror the
       * earlier extension wins -- so a dark theme added through `extensions`
       * is silently overridden. */
      theme={theme}
      extensions={extensions}
      onCreateEditor={onReady}
      onChange={onChange}
      basicSetup={basicSetup}
    />
  );
}

/* Re-rendered on every keystroke otherwise: the parent holds the draft, so its
 * state changes with each character and the editor is reconciled again for a
 * change it produced itself. */
export const CodeView = memo(CodeViewInner);
