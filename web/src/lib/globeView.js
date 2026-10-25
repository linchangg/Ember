// Adapts Abby's validated data layer (src/data, a Snapshot of the current view) to what the globe draws.
// The snapshot holds one forecast at a time: the cycle in force at the viewed time, its grid and contours,
// and the observations, zones and clock. Everything else the globe needs (static extras, the previous
// forecast's contours) is fetched here through her asset-path guards.
import { assetRoot, assetUrl } from '../data/assetUrl.ts'

const QUANTILES = [0.1, 0.3, 0.5, 0.7, 0.9]
const base = () => assetRoot(import.meta.env.VITE_ASSET_BASE ?? '/', window.location.href)

const geoCache = new WeakMap()
function gridGeo(manifest) {
  if (geoCache.has(manifest)) return geoCache.get(manifest)
  const { nx, ny, corners } = manifest.grid
  const { tl, tr, bl, br } = corners
  const lerp = (u, v, k) => tl[k] * (1 - u) * (1 - v) + tr[k] * u * (1 - v) + bl[k] * (1 - u) * v + br[k] * u * v
  const cellLonLat = new Float32Array(nx * ny * 2)
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      cellLonLat[(j * nx + i) * 2] = lerp((i + 0.5) / nx, (j + 0.5) / ny, 0)
      cellLonLat[(j * nx + i) * 2 + 1] = lerp((i + 0.5) / nx, (j + 0.5) / ny, 1)
    }
  }
  // cell corners ((nx+1) x (ny+1)) for drawing the forecast as a true raster
  const cornerLonLat = new Float32Array((nx + 1) * (ny + 1) * 2)
  for (let j = 0; j <= ny; j++) {
    for (let i = 0; i <= nx; i++) {
      cornerLonLat[(j * (nx + 1) + i) * 2] = lerp(i / nx, j / ny, 0)
      cornerLonLat[(j * (nx + 1) + i) * 2 + 1] = lerp(i / nx, j / ny, 1)
    }
  }
  const k = Math.cos((tl[1] * Math.PI) / 180)
  const e1 = [(tr[0] - tl[0]) * k, tr[1] - tl[1]]
  const e2 = [(bl[0] - tl[0]) * k, bl[1] - tl[1]]
  const det = e1[0] * e2[1] - e1[1] * e2[0]
  const cellAt = (lon, lat) => {
    const d = [(lon - tl[0]) * k, lat - tl[1]]
    const u = (d[0] * e2[1] - d[1] * e2[0]) / det
    const v = (e1[0] * d[1] - e1[1] * d[0]) / det
    if (u < 0 || u >= 1 || v < 0 || v >= 1) return null
    return Math.floor(v * ny) * nx + Math.floor(u * nx)
  }
  const out = { cellLonLat, cornerLonLat, cellAt }
  geoCache.set(manifest, out)
  return out
}

const getJson = (rel) => fetch(assetUrl(base(), rel)).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${rel}: ${r.status}`))))

/** Static files the snapshot does not carry: assets, area X, timeline. Missing files are fine. */
export async function loadExtras(manifest) {
  const [assets, areaX, timeline] = await Promise.all([
    getJson(manifest.static.assets_url).catch(() => null),
    getJson(manifest.static.area_x_url).catch(() => null),
    getJson(manifest.static.timeline_url).catch(() => []),
  ])
  return { assets, areaX, timeline }
}

// previous forecasts' q50 contours, fetched on demand for the "compare" overlay
const isoCache = new Map()
function previousIsochrones(cycle) {
  const hit = isoCache.get(cycle.cycle_id)
  if (hit) return hit.features
  isoCache.set(cycle.cycle_id, { features: null })
  getJson(cycle.assets.isochrones)
    .then((g) => isoCache.set(cycle.cycle_id, { features: g.features ?? [] }))
    .catch(() => isoCache.set(cycle.cycle_id, { features: [] }))
  return null
}

/** The view the frame loop and panels read. Rebuilt whenever the snapshot or extras change. */
export function createGlobeView(snap, extras) {
  const m = snap.manifest
  const cyc = snap.cycle
  const K = cyc ? cyc.knowledge_sim_ms : null
  const feature = (id) => snap.mapAssets?.perimeter.features.find((f) => f.id === id)?.geometry ?? null
  const entry =
    cyc && snap.bundle
      ? {
          bundle: snap.bundle,
          arrival: snap.mapAssets?.arrival ?? null,
          burned: feature('burned'),
          front: feature('front'),
          isochrones: snap.mapAssets?.isochrones.features ?? [],
          zoneRisk: new Map(snap.zoneRisk.map((z) => [z.zone_id, z])),
        }
      : null
  const cyclesByK = new Map(snap.cycles.map((c) => [c.knowledge_sim_ms, c]))
  const geo = gridGeo(m)
  return {
    manifest: m,
    zones: { features: snap.zones },
    assets: extras?.assets ?? null,
    areaX: extras?.areaX ?? null,
    timeline: extras?.timeline ?? [],
    quantiles: QUANTILES,
    cellLonLat: geo.cellLonLat,
    cornerLonLat: geo.cornerLonLat,
    zoneRiskList: snap.zoneRisk,
    alerts: snap.alerts,
    briefings: snap.briefings,
    cellAt: geo.cellAt,
    simTime: snap.simTime,
    viewTime: snap.view.viewTime,
    scrubbing: snap.view.mode === 'scrub',
    status: snap.status,
    error: snap.error,
    clock: snap.clock,
    forecastStatus: snap.forecastStatus,
    /** the forecast in force at the viewed time (the data layer already chose it) */
    cycleAt: () => K,
    cycleOf: (k) => cyclesByK.get(k) ?? null,
    prevCycle(k) {
      const i = snap.cycles.findIndex((c) => c.knowledge_sim_ms === k)
      return i > 0 ? snap.cycles[i - 1].knowledge_sim_ms : null
    },
    /** the current forecast in full; an earlier one only as its contours */
    entry(k) {
      if (k == null) return null
      if (k === K) return entry
      const c = cyclesByK.get(k)
      const iso = c ? previousIsochrones(c) : null
      return iso ? { isochrones: iso, bundle: { cycle: c } } : null
    },
    observationsIn: (t0, t1) => snap.observations.filter((o) => o.observed_sim_ms > t0 && o.observed_sim_ms <= t1),
  }
}
