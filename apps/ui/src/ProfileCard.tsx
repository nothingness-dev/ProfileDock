import { useEffect, useRef, useState } from 'react';
import { DotsThree } from '@phosphor-icons/react/dist/csr/DotsThree';
import { Play } from '@phosphor-icons/react/dist/csr/Play';
import { Square } from '@phosphor-icons/react/dist/csr/Square';
import { ArrowUpRight } from '@phosphor-icons/react/dist/csr/ArrowUpRight';
import { appearances } from './appearances';
import type { PreviewProfile } from './preview-model';

interface ProfileCardProps {
  profile: PreviewProfile;
  onStatusChange: (id: string, status: PreviewProfile['status']) => void;
}

export function ProfileCard({ profile, onStatusChange }: ProfileCardProps) {
  const appearance = appearances[profile.appearanceId];
  const running = profile.status === 'running';
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const menuId = `${profile.id}-actions`;
  const noteId = `${profile.id}-unavailable`;

  useEffect(() => {
    if (!menuOpen) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setMenuOpen(false);
    }
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [menuOpen]);

  return (
    <article className="profile-card" aria-labelledby={`${profile.id}-name`}>
      <div className="card-cover">
        <img src={appearance.cover} alt="" width="2172" height="724" />
        <span className={`status-badge ${running ? 'running' : ''}`}>
          <span className="status-dot" aria-hidden="true" />{running ? 'Running' : 'Closed'}
        </span>
        <div className="card-menu" ref={menuRef} onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false);
        }} onKeyDown={(event) => {
          if (event.key === 'Escape' && menuOpen) {
            event.preventDefault();
            setMenuOpen(false);
            menuButtonRef.current?.focus();
          }
        }}>
          <button ref={menuButtonRef} className="menu-trigger" aria-label={`Actions for ${profile.name}`}
            aria-expanded={menuOpen} aria-controls={menuId} onClick={() => setMenuOpen(!menuOpen)}>
            <DotsThree size={22} weight="bold" aria-hidden="true" />
          </button>
          {menuOpen && <div id={menuId} className="card-popover" role="group" aria-label={`${profile.name} future actions`}>
            <button disabled aria-describedby={noteId}>Profile settings · Soon</button>
            <button disabled aria-describedby={noteId}>Open tabs · Soon</button>
            <p id={noteId}>Settings and tabs are unavailable in this preview.</p>
          </div>}
        </div>
      </div>
      <div className="card-body">
        <img className="identity-icon" src={appearance.icon} alt="" width="1254" height="1254" />
        <div className="profile-copy">
          <h2 id={`${profile.id}-name`}>{profile.name}</h2>
          <p>{profile.engine}<span aria-hidden="true"> · </span>{profile.browser}</p>
          {running && profile.tabCount !== undefined ? <>
            <button className="tabs-preview" disabled aria-describedby={`${profile.id}-tabs-note`}>
              {profile.tabCount} open tabs <ArrowUpRight size={16} aria-hidden="true" />
            </button>
            <span id={`${profile.id}-tabs-note`} className="sr-only">Illustrative count. Tabs are unavailable in this preview.</span>
          </> : <p className="profile-metadata">{running ? 'Running in preview' : profile.lastOpened ? `Last opened ${profile.lastOpened}` : 'Not launched yet'}</p>}
        </div>
        <button className={`profile-action ${running ? 'close-action' : 'launch-action'}`}
          aria-label={`${running ? 'Close' : 'Launch'} ${profile.name} in preview`}
          onClick={() => onStatusChange(profile.id, running ? 'closed' : 'running')}>
          {running ? <Square size={19} weight="fill" aria-hidden="true" /> : <Play size={21} weight="fill" aria-hidden="true" />}
          {running ? 'Close' : 'Launch'}
        </button>
      </div>
    </article>
  );
}
