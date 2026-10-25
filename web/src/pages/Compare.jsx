import { Link } from 'react-router-dom'

const PATTERNS = [
  {
    to: '/compare/cesium',
    pattern: 'Pattern 1',
    title: 'Cesium only',
    body: 'One continuous globe with Esri World Elevation + imagery. Synthetic fire as Cesium entities. Best test of geospatial smoothness end-to-end.',
  },
  {
    to: '/compare/maplibre-three',
    pattern: 'Pattern 2',
    title: 'MapLibre + Three.js',
    body: 'MapLibre globe with AWS Terrarium DEM. At the incident, a Three.js custom layer draws the fire mesh/particles on top of real terrain.',
  },
  {
    to: '/compare/hybrid',
    pattern: 'Pattern 3',
    title: 'Hybrid handoff',
    body: 'MapLibre world overview with real topo → zoom in or enter the incident to hand off into the existing Sierra Vista 3D command scene.',
  },
  {
    to: '/compare/network',
    pattern: 'Experiment',
    title: 'Global hazard network',
    body: 'Dark globe with softly pulsing country borders. Pulsing points are historical January 2025 events from NASA EONET (wildfires, storms, volcanoes).',
  },
]

export default function Compare() {
  return (
    <div className="compare">
      <header className="compare__header">
        <Link to="/" className="compare__brand">
          Emberline
        </Link>
        <h1>Globe pattern bake-off</h1>
        <p>
          Three approaches, same mock Sierra Vista Fire, real Earth elevation. Watch the FPS
          meter and how zoom / rotate feels — pick the one that feels right for the product.
        </p>
      </header>
      <div className="compare__grid">
        {PATTERNS.map((p) => (
          <Link key={p.to} to={p.to} className="compare__card">
            <div className="compare__card-pattern">{p.pattern}</div>
            <h2>{p.title}</h2>
            <p>{p.body}</p>
            <span className="compare__card-cta">Open prototype →</span>
          </Link>
        ))}
      </div>
      <p className="compare__note">
        Terrain sources: Esri WorldElevation3D / World Imagery · AWS Terrarium DEM. Fire
        footprints are synthetic for viz only.
      </p>
    </div>
  )
}
