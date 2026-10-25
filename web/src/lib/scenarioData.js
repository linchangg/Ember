// Scenario files in the shapes spec/00 section 4 defines (what Cody's pipeline writes), fetched from ASSET_BASE.

const BASE = import.meta.env.VITE_ASSET_BASE ?? '/'
const url = (rel) => `${BASE}${rel}`
const json = (rel) => fetch(url(rel)).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${rel}: ${r.status}`))))
const QUANTILES = [0.1, 0.3, 0.5, 0.7, 0.9]

const lerp2 = (m, u, v) => {
  const { tl, tr, bl, br } = m.grid.corners
  return [0, 1].map((k) => tl[k] * (1 - u) * (1 - v) + tr[k] * u * (1 - v) + bl[k] * (1 - u) * v + br[k] * u * v)
}

export async function loadScenario(scn) {
  const manifest = await json(`static/${scn}/manifest.json`)
  const [zones, assets, timeline, areaX, index] = await Promise.all([
    json(manifest.static.zones_url).catch(() => null),
    json(manifest.static.assets_url).catch(() => null),
    json(manifest.static.timeline_url).catch(() => []),
    json(manifest.static.area_x_url).catch(() => null),
    json(`cache/${scn}/main/index.json`).catch(() => ({ cycles: [] })),
  ])
  const cycles = [...index.cycles].sort((a, b) => a.knowledge_sim_ms - b.knowledge_sim_ms)

  // cell-centre lon/lat for every grid cell (row 0 = north)
  const { nx, ny } = manifest.grid
  const cellLonLat = new Float32Array(nx * ny * 2)
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const [lo, la] = lerp2(manifest, (i + 0.5) / nx, (j + 0.5) / ny)
      cellLonLat[(j * nx + i) * 2] = lo
      cellLonLat[(j * nx + i) * 2 + 1] = la
    }
  }

  const loaded = new Map()
  const loading = new Set()
  const load = (k) => {
    if (loaded.has(k) || loading.has(k)) return
    loading.add(k)
    const entry = cycles.find((c) => c.knowledge_sim_ms === k)
    json(entry.bundle)
      .then(async (bundle) => {
        const a = bundle.cycle.assets ?? {}
        const [perimeter, isochrones, buf] = await Promise.all([
          a.perimeter ? json(a.perimeter).catch(() => null) : null,
          a.isochrones ? json(a.isochrones).catch(() => null) : null,
          a.arrival_q ? fetch(url(a.arrival_q)).then((r) => r.arrayBuffer()).catch(() => null) : null,
        ])
        let arrival = null
        if (buf) {
          if (buf.byteLength !== 5 * nx * ny) throw new Error(`arrival_q size ${buf.byteLength} != 5*${ny}*${nx}`)
          arrival = new Uint8Array(buf)
        }
        loaded.set(k, {
          bundle,
          arrival,
          burned: perimeter?.features.find((f) => f.id === 'burned')?.geometry ?? null,
          front: perimeter?.features.find((f) => f.id === 'front')?.geometry ?? null,
          isochrones: isochrones?.features ?? [],
          zoneRisk: new Map((bundle.zone_risk ?? []).map((z) => [z.zone_id, z])),
        })
      })
      .catch((e) => console.warn('bundle load failed', k, e))
      .finally(() => loading.delete(k))
  }

  return {
    manifest,
    zones,
    assets,
    timeline,
    areaX,
    cycles,
    cellLonLat,
    quantiles: QUANTILES,
    /** latest cycle with knowledge <= t, or null before the first one */
    cycleAt(t) {
      let k = null
      for (const c of cycles) if (c.knowledge_sim_ms <= t) k = c.knowledge_sim_ms
      return k
    },
    /** loaded entry for cycle k (kicks off loading, and its neighbour, if needed) */
    entry(k) {
      if (k == null) return null
      load(k)
      const i = cycles.findIndex((c) => c.knowledge_sim_ms === k)
      if (cycles[i + 1]) load(cycles[i + 1].knowledge_sim_ms)
      return loaded.get(k) ?? null
    },
  }
}
