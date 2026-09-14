import { useRef } from 'react';
import { appearances } from './appearances';
import type { PreviewProfile } from './preview-model';

interface SessionDockProps {
  running: readonly PreviewProfile[];
  onCloseAll: () => void;
}

export function SessionDock({ running, onCloseAll }: SessionDockProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dockRef = useRef<HTMLElement>(null);

  function dismiss() {
    dialogRef.current?.close();
  }

  return <>
    <aside ref={dockRef} className="session-dock" tabIndex={-1} aria-label="Running preview profiles">
      <span className="dock-status"><span className={`status-dot ${running.length ? '' : 'inactive-dot'}`} aria-hidden="true" />{running.length} running</span>
      <ul className="dock-profiles">
        {running.map((profile) => <li key={profile.id}>
          <img src={appearances[profile.appearanceId].icon} alt="" width="48" height="48" />
          <span>{profile.name}</span>
        </li>)}
      </ul>
      {running.length === 0 && <span className="dock-empty">No running preview profiles</span>}
      <button ref={triggerRef} className="dock-close-all" disabled={running.length === 0} onClick={() => {
        dialogRef.current?.showModal();
        cancelRef.current?.focus();
      }}>Close all</button>
    </aside>
    <dialog ref={dialogRef} className="close-dialog" aria-labelledby="close-all-title" aria-describedby="close-all-description"
      onClose={() => {
        if (triggerRef.current?.disabled) dockRef.current?.focus();
        else triggerRef.current?.focus();
      }}>
      <h2 id="close-all-title">Close all preview profiles?</h2>
      <p id="close-all-description">This marks {running.length} running example {running.length === 1 ? 'profile' : 'profiles'} as Closed in memory. No real browsers will be closed.</p>
      <div className="dialog-actions">
        <button ref={cancelRef} className="secondary-button" autoFocus onClick={dismiss}>Cancel</button>
        <button className="profile-action launch-action" onClick={() => {
          dismiss();
          onCloseAll();
        }}>Close all in preview</button>
      </div>
    </dialog>
  </>;
}
