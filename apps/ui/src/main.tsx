import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { GearSix } from '@phosphor-icons/react/dist/csr/GearSix';
import { Plus } from '@phosphor-icons/react/dist/csr/Plus';
import { MagnifyingGlass } from '@phosphor-icons/react/dist/csr/MagnifyingGlass';
import { X } from '@phosphor-icons/react/dist/csr/X';
import './tokens.css';
import './styles.css';

function App() {
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
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
                aria-describedby="search-note"
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
            <p id="search-note" className="helper">Search preview · Filtering arrives with profile cards.</p>
          </div>
        </section>

        <section className="preview-placeholder" aria-labelledby="preview-title">
          <span className="preview-label">Level 1 preview</span>
          <h2 id="preview-title">Your spaces will take shape here.</h2>
          <p>Profile cards are coming in the next level.<br />This preview does not display your profile collection.</p>
        </section>
      </main>
    </>
  );
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
