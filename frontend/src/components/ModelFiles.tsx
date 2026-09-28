import { useCallback, useEffect, useRef, useState } from 'react';
import { isMap, isSeq, parse as parseYaml, parseDocument } from 'yaml';
import { ApiError, api } from '../api';
import { useToast } from './Toast';
import { AddMemberDialog, type NewMember } from './AddMemberDialog';
import { ConfirmDialog } from './ConfirmDialog';
import { openSearchPanel } from '@codemirror/search';
import type { EditorView } from '@codemirror/view';
import { CodeView } from './CodeView';
import { Icon } from './Icon';

interface Props {
  /** Published path -> YAML body, as the generator rendered it. */
  files: Record<string, string>;
  /** Enables editing. Without it the panel is a viewer, as before. */
  projectId?: string;
}

/** The rendered YAML, readable in the app rather than only on disk.
 *
 * These files are the product: everything upstream exists to produce them, so
 * they get a viewer rather than a list of names. The body is shown verbatim,
 * because what Cube reads and what the user reviews have to be the same text.
 */
export function ModelFiles({ files, projectId }: Props) {
  const names = Object.keys(files).sort();
  const [active, setActive] = useState(names[0] ?? '');
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  /* Edits live here until saved, keyed by path, so switching files mid-edit
   * does not throw the draft away. */
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [edited, setEdited] = useState<Record<string, string>>({});
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  /* Revert is irreversible and goes further back than people expect, so it
   * is confirmed rather than done on the click. */
  const [confirmRevert, setConfirmRevert] = useState(false);
  const [reverting, setReverting] = useState(false);
  const view = useRef<EditorView | null>(null);

  /* Stable identities: new closures on every render would defeat the memo on
   * CodeView and reconcile the editor for a change it produced itself. */
  const onReady = useCallback((created: EditorView) => {
    view.current = created;
  }, []);
  /* The editor owns the text while you type; React hears about it on a pause.
   * Per-keystroke state meant a render of this whole panel, a re-render of the
   * editor, and a value prop the editor then had to compare against its own
   * document -- all for a change it had just made itself. `live` always holds
   * the current text so saving never reads a stale draft. */
  const live = useRef<string | null>(null);
  const sync = useRef<number | undefined>(undefined);

  const onChange = useCallback(
    (next: string) => {
      live.current = next;
      if (sync.current) window.clearTimeout(sync.current);
      sync.current = window.setTimeout(() => {
        setDrafts((current) => ({ ...current, [active]: next }));
      }, 250);
    },
    [active],
  );

  /* Switching file, saving or reverting all replace what is being edited, so
   * the buffered text must not leak across. */
  const clearLive = useCallback(() => {
    if (sync.current) window.clearTimeout(sync.current);
    sync.current = undefined;
    live.current = null;
  }, []);

  useEffect(() => clearLive(), [active, clearLive]);
  useEffect(() => () => clearLive(), [clearLive]);

  /* Re-read whenever the model changes, not once on open: a revert, a
   * regeneration or a view save can all change what is edited on disk, and a
   * list read at mount time goes on reporting the old state. */
  const refreshEdits = useCallback(() => {
    if (!projectId) return;
    api.listEdits(projectId).then(setEdited).catch(() => setEdited({}));
  }, [projectId]);

  useEffect(() => {
    refreshEdits();
  }, [refreshEdits, files]);

  /* Coming back to the tab is the other way the list goes stale -- the file
   * may have been changed from another page or another window meanwhile. */
  useEffect(() => {
    const onFocus = () => refreshEdits();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refreshEdits]);

  /* A regenerated model can drop the file that was open -- fall back to the
   * first one rather than rendering a blank pane. */
  useEffect(() => {
    if (!files[active]) setActive(names[0] ?? '');
  }, [files, active, names]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  /* Esc closes the full-screen view, and the page behind it must not scroll
   * under the overlay while it is open. */
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    document.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [expanded]);

  if (names.length === 0) return null;

  /* A saved edit is what is on disk, so it wins over the generated text. */
  const stored = edited[active] ?? files[active] ?? '';
  const body = drafts[active] ?? stored;
  /** What is in the editor right now, ahead of the debounced draft. */
  const current = () => live.current ?? body;
  const lines = body.split('\n').length;
  const dirty = body !== stored;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  /* The validator reports a line; being able to land on it is the whole
   * difference between a message and a fix. */
  const goToLine = (line: number) => {
    const editor = view.current;
    if (!editor) return;
    const target = editor.state.doc.line(
      Math.max(1, Math.min(line, editor.state.doc.lines)),
    );
    editor.dispatch({ selection: { anchor: target.from }, scrollIntoView: true });
    editor.focus();
  };

  const save = async () => {
    if (!projectId) return;
    setSaving(true);
    setProblem(null);
    try {
      /* Parsed here first so an indentation mistake is reported without a
       * round trip; the server checks again, and checks more. */
      const text = current();
      parseYaml(text);
      const result = await api.saveModelFile(projectId, active, text);
      /* Changed back to the original by hand: the server drops the edit, so
       * the file must stop being marked. */
      setEdited((current) => {
        const next = { ...current };
        if (result.edited) next[active] = text;
        else delete next[active];
        return next;
      });
      setDrafts((current) => {
        const next = { ...current };
        delete next[active];
        return next;
      });
      setSaved(active);
      setEditing(false);
      toast.show({
        tone: 'success',
        title: 'Saved',
        body: `${active} is valid and written to the model folder.`,
      });
    } catch (err) {
      /* Two shapes reach here: the local YAML parse, which carries linePos,
       * and the server's own check, which carries a located problem. */
      const local = err as { linePos?: [{ line: number }]; message?: string };
      const api_ = err instanceof ApiError ? err.problem : undefined;
      const line = api_?.line ?? local.linePos?.[0]?.line ?? null;
      const kind = api_?.kind ?? (local.linePos ? 'indentation' : 'schema');
      const message =
        err instanceof ApiError ? err.message : local.message ?? 'Could not save.';

      setProblem(line ? `Line ${line}: ${message}` : message);
      toast.show({
        tone: 'error',
        title:
          kind === 'indentation'
            ? 'Fix the indentation before saving'
            : kind === 'syntax'
              ? 'This is not valid YAML'
              : 'This does not match the Cube model',
        body: api_?.hint ? `${message} ${api_.hint}` : message,
        action: line
          ? { label: `Go to line ${line}`, run: () => goToLine(line) }
          : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  /* Everything already named in this cube, across dimensions, measures and
   * segments, which share one namespace. */
  const takenNames = (): Set<string> => {
    const names = new Set<string>();
    try {
      const cube = parseYaml(current())?.cubes?.[0] ?? {};
      for (const kind of ['dimensions', 'measures', 'segments']) {
        for (const m of cube[kind] ?? []) if (m?.name) names.add(m.name);
      }
    } catch {
      /* An unparseable file is caught by the save; nothing to offer here. */
    }
    return names;
  };

  /* Inserts through the YAML document model rather than re-dumping the data,
   * so comments and hand formatting in the file survive the add. */
  const addMember = async (
    member: NewMember,
    acknowledged: boolean,
  ): Promise<{ errors: string[]; warnings: string[] }> => {
    if (!projectId) return { errors: ['Editing is not available here.'], warnings: [] };

    const doc = parseDocument(current());
    if (doc.errors.length) {
      return { errors: ['Fix the YAML in this file before adding to it.'], warnings: [] };
    }
    const cube = doc.getIn(['cubes', 0]);
    if (!isMap(cube)) return { errors: ['This file does not define a cube.'], warnings: [] };
    const table = String(cube.get('sql_table') ?? '');

    const check = await api.checkMember(projectId, {
      table,
      kind: member.kind,
      type: member.type,
      sql: member.sql,
    });
    if (check.errors.length) return check;
    if (check.warnings.length && !acknowledged) return check;

    /* Keys in the same order the generator writes them, so an added member
     * reads like its neighbours. */
    const entry: Record<string, unknown> = { name: member.name, title: member.title };
    if (member.kind === 'dimension') {
      entry.sql = member.sql;
      entry.type = member.type;
      if (member.primary_key) entry.primary_key = true;
      if (member.public === false) entry.public = false;
    } else if (member.kind === 'measure') {
      entry.type = member.type;
      if (member.sql) entry.sql = member.sql;
      if (member.filter) entry.filters = [{ sql: member.filter }];
    } else {
      entry.sql = member.sql;
    }
    entry.description = member.description;

    const key = `${member.kind}s`;
    const list = cube.get(key);
    if (isSeq(list)) list.add(doc.createNode(entry));
    else cube.set(key, doc.createNode([entry]));

    const content = doc.toString({ lineWidth: 88, minContentWidth: 0 });

    let result: { edited: boolean };
    try {
      result = await api.saveModelFile(projectId, active, content);
    } catch (err) {
      return {
        errors: [err instanceof ApiError ? err.message : 'Could not save the file.'],
        warnings: [],
      };
    }

    setEdited((current) => {
      const next = { ...current };
      if (result.edited) next[active] = content;
      else delete next[active];
      return next;
    });
    setAdding(false);
    toast.show({
      tone: 'success',
      title: `Added ${member.kind} ${member.name}`,
      body: `Validated and written to ${active}. Revert undoes it.`,
    });
    /* Land on what was just added, once the editor has the new text. */
    const line = content.split('\n').findIndex((l) => l.trim() === `- name: ${member.name}`);
    if (line >= 0) setTimeout(() => goToLine(line + 1), 50);
    return { errors: [], warnings: [] };
  };

  const revert = async () => {
    if (!projectId) return;
    setProblem(null);
    setReverting(true);
    try {
      const { content } = await api.revertModelFile(projectId, active);
      setEdited((current) => {
        const next = { ...current };
        delete next[active];
        return next;
      });
      setDrafts((current) => {
        const next = { ...current };
        delete next[active];
        return next;
      });
      void content;
      clearLive();
      setEditing(false);
      toast.show({
        tone: 'success',
        title: `${active.split('/').pop()} reverted`,
        body: 'The file is back to what generation produced.',
      });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : 'Could not revert.');
      toast.show({
        tone: 'error',
        title: 'Could not revert',
        body: err instanceof ApiError ? err.message : 'Reverting failed.',
      });
    } finally {
      setReverting(false);
      setConfirmRevert(false);
    }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([body], { type: 'text/yaml' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = active.split('/').pop() ?? 'cube.yml';
    link.click();
    URL.revokeObjectURL(url);
  };

  /* One panel, rendered inline and again inside the overlay. The inline copy
   * stays mounted while expanded so the page behind does not reflow. */
  const panel = (full: boolean) => (
    <section
      className={`card card--code${full ? ' card--full' : ''}`}
      /* Ctrl+F anywhere in the panel, not only inside the editor: the file
       * list and the toolbar are part of "here" as far as searching goes.
       * Scoped to the panel, so the browser keeps the shortcut elsewhere. */
      onKeyDown={(event) => {
        if (
          (event.metaKey || event.ctrlKey) &&
          event.key.toLowerCase() === 'f' &&
          !event.defaultPrevented
        ) {
          if (view.current) {
            event.preventDefault();
            openSearchPanel(view.current);
            view.current.focus();
          }
        }
      }}
    >
      <div className="card__head">
        <span className="card__label">
          <Icon name="file" size={13} />
          Model files
        </span>
        <span className="card__tools">
          <span className="pill pill--idle">{lines} lines</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            title="Search this file (Ctrl+F)"
            onClick={() => {
              if (view.current) {
                openSearchPanel(view.current);
                view.current.focus();
              }
            }}
          >
            <Icon name="search" size={13} />
            Search
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={copy}>
            <Icon name={copied ? 'tick' : 'copy'} size={13} />
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={download}>
            <Icon name="download" size={13} />
            Download
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setExpanded(!full)}
          >
            <Icon name="expand" size={13} />
            {full ? 'Close' : 'Expand'}
          </button>
          {projectId && active.startsWith('cubes/') && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={dirty}
              title={
                dirty
                  ? 'Save or discard your edits first, so an add does not save them too'
                  : 'Add a dimension, measure or segment'
              }
              onClick={() => setAdding(true)}
            >
              <Icon name="plus" size={13} />
              Add
            </button>
          )}
          {projectId &&
            (editing ? (
              <>
                {edited[active] && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => setConfirmRevert(true)}
                  >
                    <Icon name="refresh" size={13} />
                    Revert
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn--solid btn--sm"
                  disabled={saving || !dirty}
                  onClick={() => void save()}
                >
                  <Icon name="tick" size={13} />
                  {saving ? 'Checking…' : 'Save'}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setEditing(true);
                  setProblem(null);
                }}
              >
                <Icon name="pencil" size={13} />
                Edit
              </button>
            ))}
        </span>
      </div>
      <div className="yaml">
        <div className="yaml__list" role="tablist" aria-label="Generated files">
          {names.map((name) => (
            <button
              type="button"
              key={name}
              role="tab"
              aria-selected={name === active}
              className={`yaml__tab${name === active ? ' is-active' : ''}`}
              onClick={() => setActive(name)}
            >
              <Icon name="file" size={13} />
              <span className="yaml__name">{name.split('/').pop()}</span>
              {(() => {
                const saved = edited[name] ?? files[name];
                /* A draft typed and then undone is identical to what is saved;
                   it has to differ to count as unsaved. */
                const unsaved = drafts[name] !== undefined && drafts[name] !== saved;
                if (!unsaved && !edited[name]) return null;
                return (
                  <span
                    className={`yaml__dot${unsaved ? '' : ' yaml__dot--saved'}`}
                    title={unsaved ? 'Unsaved changes' : 'Edited — differs from the generated file'}
                  />
                );
              })()}
            </button>
          ))}
        </div>
        <div className="yaml__body">
          {/* Only the visible panel mounts an editor. While expanded the inline
              one would otherwise sit behind the overlay, invisible, and still
              be reconciled on every keystroke. */}
          {full === expanded ? (
            <CodeView
              value={body}
              readOnly={!editing}
              onChange={onChange}
              onReady={onReady}
            />
          ) : (
            <div className="yaml__hidden">Open in the full-screen editor</div>
          )}
        </div>
      </div>
      <div className="card__foot">
        {problem ? (
          <span className="field__hint field__hint--error">{problem}</span>
        ) : (
          <span className="field__hint">
            {dirty
              ? 'Unsaved. Saving checks the YAML parses and the model is shaped correctly before writing.'
              : saved === active
                ? `Saved to docker/cube/model/${active}. Cube reloads the folder on change.`
                : editing
                  ? 'Editing. Nothing is written until you save.'
                  : `Written to docker/cube/model/${active}. Cube reloads the folder on change, so the playground picks this up without a restart.`}
          </span>
        )}
        {full && <span className="field__hint mono">Esc</span>}
      </div>
    </section>
  );

  return (
    <>
      {confirmRevert && (
        <ConfirmDialog
          destructive
          icon="refresh"
          title={`Revert ${active.split('/').pop()} to the generated version?`}
          body="This throws away every change made to this file, not only the last one. There is no per-edit history, so it cannot be undone."
          points={[
            'The file goes back to exactly what generation produced.',
            'Members you renamed go back to their generated names, and any view using them is updated to match.',
            'Other files are untouched.',
          ]}
          confirmLabel="Revert the file"
          busy={reverting}
          onCancel={() => setConfirmRevert(false)}
          onConfirm={() => void revert()}
        />
      )}

      {adding && (
        <AddMemberDialog
          cube={active.split('/').pop()?.replace('.yml', '') ?? ''}
          taken={takenNames()}
          onCancel={() => setAdding(false)}
          onDone={addMember}
        />
      )}
      {panel(false)}
      {expanded && (
        <div
          className="scrim scrim--wide"
          role="dialog"
          aria-modal="true"
          aria-label="Model files"
          onClick={(event) => {
            if (event.target === event.currentTarget) setExpanded(false);
          }}
        >
          {panel(true)}
        </div>
      )}
    </>
  );
}
