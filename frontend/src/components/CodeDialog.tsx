import { useEffect, useRef, useState } from 'react';
import { openSearchPanel } from '@codemirror/search';
import type { EditorView } from '@codemirror/view';
import { CodeView } from './CodeView';
import { Icon } from './Icon';

interface Props {
  title: string;
  /** Used as the download filename. */
  filename: string;
  value: string;
  language?: 'yaml' | 'json';
  /** Shown under the editor: where this came from, or what it is for. */
  note?: string;
  onClose: () => void;
}

/** One document, read full screen.
 *
 * ModelFiles has its own overlay because it also carries a file list. This is
 * for the single-document case, such as the evidence bundle.
 */
export function CodeDialog({ title, filename, value, language = 'yaml', note, onClose }: Props) {
  const [copied, setCopied] = useState(false);
  const view = useRef<EditorView | null>(null);
  const lines = value.split('\n').length;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const download = () => {
    const url = URL.createObjectURL(
      new Blob([value], { type: language === 'json' ? 'application/json' : 'text/yaml' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className="scrim scrim--wide"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="card card--code card--full"
        onKeyDown={(event) => {
          if (
            (event.metaKey || event.ctrlKey) &&
            event.key.toLowerCase() === 'f' &&
            !event.defaultPrevented &&
            view.current
          ) {
            event.preventDefault();
            openSearchPanel(view.current);
            view.current.focus();
          }
        }}
      >
        <div className="card__head">
          <span className="card__label">
            <Icon name="file" size={13} />
            {title}
          </span>
          <span className="card__tools">
            <span className="pill pill--idle num">{lines.toLocaleString()} lines</span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              title="Search (Ctrl+F)"
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
            <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
              <Icon name="expand" size={13} />
              Close
            </button>
          </span>
        </div>

        <div className="yaml yaml--single">
          <div className="yaml__body">
            <CodeView
              value={value}
              language={language}
              onReady={(created) => (view.current = created)}
            />
          </div>
        </div>

        <div className="card__foot">
          {note && <span className="field__hint">{note}</span>}
          <span className="field__hint mono">Esc</span>
        </div>
      </section>
    </div>
  );
}
