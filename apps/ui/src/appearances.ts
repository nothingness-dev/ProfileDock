import emberCover from './assets/appearances/ember-cover.png';
import emberIcon from './assets/appearances/ember-icon.png';
import prismCover from './assets/appearances/prism-cover.png';
import prismIcon from './assets/appearances/prism-icon.png';
import orbitCover from './assets/appearances/orbit-cover.png';
import orbitIcon from './assets/appearances/orbit-icon.png';
import vertexCover from './assets/appearances/vertex-cover.png';
import vertexIcon from './assets/appearances/vertex-icon.png';

// Presentation presets only. These IDs never derive from profile names, and
// this registry neither creates profiles nor changes Python/storage contracts.
export const appearanceIds = ['ember', 'prism', 'orbit', 'vertex'] as const;
export type AppearanceId = (typeof appearanceIds)[number];

export interface Appearance {
  readonly label: string;
  readonly cover: string;
  readonly icon: string;
  readonly accent: `#${string}`;
}

export const appearances = {
  ember: { label: 'Ember', cover: emberCover, icon: emberIcon, accent: '#f59a86' },
  prism: { label: 'Prism', cover: prismCover, icon: prismIcon, accent: '#527aff' },
  orbit: { label: 'Orbit', cover: orbitCover, icon: orbitIcon, accent: '#bc85f7' },
  vertex: { label: 'Vertex', cover: vertexCover, icon: vertexIcon, accent: '#b5d64d' },
} as const satisfies Readonly<Record<AppearanceId, Appearance>>;
