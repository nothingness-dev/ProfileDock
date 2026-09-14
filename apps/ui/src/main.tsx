import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { GearSix } from '@phosphor-icons/react/dist/csr/GearSix';
import { Plus } from '@phosphor-icons/react/dist/csr/Plus';
import { MagnifyingGlass } from '@phosphor-icons/react/dist/csr/MagnifyingGlass';
import { X } from '@phosphor-icons/react/dist/csr/X';
import { ProfileCard } from './ProfileCard';
import { SessionDock } from './SessionDock';
import { closeAllPreviewProfiles, createPreviewProfiles, filterProfiles, runningProfiles, setPreviewStatus } from './preview-model';
import './tokens.css';
import './styles.css';
import './dashboard.css';

function App() {
  const [query, setQuery] = useState('');
  const [profiles, setProfiles] = useState(createPreviewProfiles);
  const [emptyPreview, setEmptyPreview] = useState(false);
  const collection = emptyPreview ? [] : profiles;
  const visibleProfiles = filterProfiles(collection, query);
  const running = runningProfiles(collection);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      if (document.querySelector('dialog[open]')) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  return (
    <>
      <a className="skip-link" href="#dashboard">Skip to dashboard</a>
      <header className="topbar">
        <div className="topbar-inner">
          <span className="wordmark">ProfileDock</span>
          <nav aria-label="Main navigation">
            <a className="nav-item active" href="#dashboard" aria-current="page">Profiles</a>
            <button className="nav-item" disabled>Backups <span className="soon">Soon</span></button>
            <button className="nav-item" disabled>Diagnostics <span className="soon">Soon</span></button>
          </nav>
          <button className="settings" disabled aria-label="Settings — unavailable in this preview">
            <GearSix size={22} aria-hidden="true" />
            <span className="soon">Soon</span>
          </button>
        </div>
      </header>

      <main id="dashboard" className="dashboard" tabIndex={-1}>
        <section aria-labelledby="dashboard-title">
          <div className="heading-row">
            <div>
              <h1 id="dashboard-title">A space for <span>every you.</span></h1>
              <p className="subtitle">Pick a profile. Get into your flow.</p>
            </div>
            <div className="create-group">
              <button className="create-button" disabled aria-describedby="create-note">
                <Plus size={22} aria-hidden="true" /> Create profile
              </button>
              <p id="create-note" className="helper">Creation is unavailable in this preview.</p>
            </div>
          </div>

          <div className="search-group" role="search" aria-label="Profile search preview">
            <label className="sr-only" htmlFor="profile-search">Find your space</label>
            <div className="search-field">
              <MagnifyingGlass size={23} aria-hidden="true" />
              <input
                ref={searchRef}
                id="profile-search"
                type="search"
                placeholder="Find your space..."
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-describedby="profile-count"
                aria-keyshortcuts="Control+k Meta+k"
                autoComplete="off"
                spellCheck={false}
              />
              {query && (
                <button className="clear-button" aria-label="Clear search" onClick={() => {
                  setQuery('');
                  searchRef.current?.focus();
                }}>
                  <X size={18} aria-hidden="true" />
                </button>
              )}
              <kbd aria-hidden="true">Ctrl / ⌘ K</kbd>
            </div>
          </div>
          <div className="preview-toolbar">
            <p id="preview-note"><strong>UI preview</strong> · Illustrative profiles. Actions change memory only; reload resets them.</p>
            <label className="empty-preview-toggle"><input type="checkbox" checked={emptyPreview} onChange={(event) => {
              setEmptyPreview(event.target.checked);
              setQuery('');
            }} /> Empty collection preview</label>
          </div>
          <div className="collection-summary">
            <p className="helper">Create profile, settings and tabs are unavailable.</p>
            <p id="profile-count" role="status">{visibleProfiles.length} of {collection.length} profiles <span aria-hidden="true"> / </span> {running.length} running</p>
          </div>
        </section>

        <section className="profile-grid" aria-label="Preview profiles" aria-describedby="preview-note">
          {visibleProfiles.map((profile) => <ProfileCard key={profile.id} profile={profile} onStatusChange={(id, status) => {
            setProfiles((current) => setPreviewStatus(current, id, status));
          }} />)}
        </section>
        {collection.length === 0 ? <section className="preview-placeholder" aria-labelledby="empty-title">
          <h2 id="empty-title">No profiles in this example collection.</h2>
          <p>This is an intentionally empty UI preview, not your saved collection.<br />Turn off Empty collection preview to return to the examples.</p>
        </section> : visibleProfiles.length === 0 && <section className="preview-placeholder" aria-labelledby="no-results-title">
          <h2 id="no-results-title">No profiles match your search.</h2>
          <p>Try a different profile name or clear your search.</p>
          <button className="secondary-button" onClick={() => { setQuery(''); searchRef.current?.focus(); }}>Clear search</button>
        </section>}
        <footer className="dashboard-footer">
          <p>Local UI preview · Nothing is saved</p>
          <SessionDock running={running} onCloseAll={() => setProfiles(closeAllPreviewProfiles)} />
        </footer>
      </main>
    </>
  );
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
