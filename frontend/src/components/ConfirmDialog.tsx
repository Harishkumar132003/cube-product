import { useEffect, useRef } from 'react';
import { Icon, type IconName } from './Icon';

interface Props {
  title: string;
  /** One sentence saying what happens. Not a restatement of the title. */
  body: string;
  /** The consequences worth naming, one clause each. */
  points?: string[];
  confirmLabel: string;
  icon?: IconName;
  /** Destructive actions get the red button; everything else the solid one. */
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A blocking confirm for an action that cannot be undone.
 *
 * Cancel takes focus, not the confirm button: this opens in front of someone
 * mid-task, and Enter should not destroy their work.
 */
export function ConfirmDialog({
  title,
  body,
  points = [],
  confirmLabel,
  icon = 'alert',
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const cancel = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [onCancel]);

  return (
    <div
      className="scrim"
      role="alertdialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div className="modal modal--confirm">
        <div className="confirm">
          <span className={`confirm__mark${destructive ? ' confirm__mark--danger' : ''}`}>
            <Icon name={icon} size={18} />
          </span>
          <div className="confirm__text">
            <h2 className="confirm__title">{title}</h2>
            <p className="confirm__body">{body}</p>
            {points.length > 0 && (
              <ul className="confirm__points">
                {points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="modal__foot confirm__foot">
          <button
            ref={cancel}
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className={`btn btn--sm ${destructive ? 'btn--danger' : 'btn--solid'}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
