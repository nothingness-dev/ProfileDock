import { StrictMode, type CSSProperties } from 'react';
import { createRoot } from 'react-dom/client';
import { appearances, appearanceIds } from '../appearances';
import '../tokens.css';
import './asset-preview.css';

function AssetPreview() {
  return (
    <main>
      <header>
        <p className="eyebrow">Development only · Level 2</p>
        <h1>Appearance assets</h1>
        <p>Four reusable appearance presets, not profiles. Review covers at a 4:1 crop and transparent sculptures at 96px and 64px.</p>
      </header>
      <div className="asset-grid">
        {appearanceIds.map((id) => {
          const appearance = appearances[id];
          return (
            <section key={id} aria-labelledby={`${id}-title`} style={{ '--accent': appearance.accent } as CSSProperties}>
              <h2 id={`${id}-title`}>{appearance.label} <code>{id}</code></h2>
              <img className="cover-sample" src={appearance.cover} alt={`${appearance.label} cover at card proportions`} />
              <div className="icon-samples">
                <figure>
                  <div className="swatch dark"><img width="96" height="96" src={appearance.icon} alt={`${appearance.label} sculpture on navy`} /></div>
                  <figcaption>Card · 96px</figcaption>
                </figure>
                <figure>
                  <div className="swatch checker"><img width="64" height="64" src={appearance.icon} alt={`${appearance.label} sculpture on transparency grid`} /></div>
                  <figcaption>Picker · 64px</figcaption>
                </figure>
                <figure>
                  <div className="swatch light"><img width="64" height="64" src={appearance.icon} alt={`${appearance.label} sculpture on light background`} /></div>
                  <figcaption>Alpha edge review</figcaption>
                </figure>
              </div>
              <details>
                <summary>Inspect full source files</summary>
                <p><a href={appearance.cover} target="_blank" rel="noreferrer">Full cover</a> · <a href={appearance.icon} target="_blank" rel="noreferrer">Full icon</a></p>
                <img className="full-cover" src={appearance.cover} alt={`${appearance.label} uncropped cover`} />
              </details>
            </section>
          );
        })}
      </div>
    </main>
  );
}

// This module is referenced only by asset-preview.html, never the production entry.
if (import.meta.env.DEV) {
  createRoot(document.getElementById('root')!).render(<StrictMode><AssetPreview /></StrictMode>);
}
