// Generates a SYNTHETIC Eaton Fire spread in the same schema real VIIRS detections will use.
// Nothing here is observed. It exists so the time slider has something to drive until real data
// (scripts/fetch_firms.mjs, or a teammate's pipeline) writes public/data/eaton_viirs.json.
//
// Schema:
//   { synthetic: bool, source: string,
//     detections: [[t_epoch_ms_utc, lon, lat, frp_mw], ...],
//     forecast:   [[issued_ms_utc, lon, lat, probability_0_to_1, eta_ms_utc], ...] }
// At slider time T the page shows the latest forecast issued at or before T, for cells whose eta is still ahead.
import { readFileSync, writeFileSync } from 'node:fs'

const perim = JSON.parse(readFileSync(new URL('../public/data/eaton_perimeter.geojson', import.meta.url)))
const polys = perim.features[0].geometry.coordinates

let seed = 20250107
const rnd = () => {
  seed = (seed + 0x6d2b79f5) | 0
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296
}

const inRing = (x, y, ring) => {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
const inside = (x, y) => polys.some((p) => p.reduce((acc, ring) => (inRing(x, y, ring) ? !acc : acc), false))

const IGN = { lon: -118.0947, lat: 34.1925, t: Date.parse('2025-01-08T02:18:00Z') }
const km = (dlon, dlat) => [dlon * 111.32 * Math.cos(34.19 * (Math.PI / 180)), dlat * 110.57]

// Santa Ana wind from the east: the fire runs west fast and creeps east/uphill slowly.
const arrivalHours = (lon, lat) => {
  const [dx, dy] = km(lon - IGN.lon, lat - IGN.lat)
  const d = Math.hypot(dx < 0 ? dx * 0.45 : dx * 2.2, dy > 0 ? dy * 1.5 : dy)
  const a = Math.pow(d / 0.5, 1.15) + rnd() * 4
  return a < 24 ? a : 24 + (a - 24) * 5
}

const PASS_UTC = [[9, 30], [10, 20], [21, 30], [22, 20]]
const LAT0 = 34.1
const LON0 = -118.25
const DLAT = 0.0035
const DLON = 0.0042
const cells = new Map() // "ix,iy" -> arrival ms for cells inside the final perimeter
const out = []
for (let iy = 0; LAT0 + iy * DLAT < 34.3; iy++) {
  for (let ix = 0; LON0 + ix * DLON < -117.95; ix++) {
    const lon = LON0 + ix * DLON
    const lat = LAT0 + iy * DLAT
    if (!inside(lon, lat)) continue
    const arrive = IGN.t + arrivalHours(lon, lat) * 3600000
    cells.set(`${ix},${iy}`, arrive)
    const burnH = rnd() < 0.06 ? 120 + rnd() * 280 : 10 + rnd() * 20
    for (let day = 8; day <= 31; day++) {
      for (const [h, m] of PASS_UTC) {
        const t = Date.UTC(2025, 0, day, h, m) + Math.floor(rnd() * 600000)
        if (t < arrive || t - arrive > burnH * 3600000 || rnd() > 0.7) continue
        out.push([t, +(lon + (rnd() - 0.5) * 0.003).toFixed(5), +(lat + (rnd() - 0.5) * 0.003).toFixed(5), +(3 + rnd() * 37).toFixed(1)])
      }
    }
  }
}
out.sort((a, b) => a[0] - b[0])

// Forecast: a model that is right on average but noisy, plus some low-probability cells that never burn.
const forecast = []
const gauss = () => (rnd() + rnd() + rnd() - 1.5) * 2
for (let issued = Date.parse('2025-01-08T02:00:00Z'); issued <= Date.parse('2025-01-14T02:00:00Z'); issued += 12 * 3600000) {
  for (let iy = 0; LAT0 + iy * DLAT < 34.3; iy++) {
    for (let ix = 0; LON0 + ix * DLON < -117.95; ix++) {
      let ref = cells.get(`${ix},${iy}`)
      let ring = false
      if (ref === undefined) {
        for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
          const a = cells.get(`${ix + dx},${iy + dy}`)
          if (a !== undefined && (ref === undefined || a < ref)) ref = a
        }
        ring = ref !== undefined
      }
      if (ref === undefined || ref <= issued || ref - issued > 48 * 3600000 || rnd() < 0.1) continue
      const leadH = (ref - issued) / 3600000
      const p = ring ? 0.04 + 0.22 * rnd() * Math.exp(-leadH / 30) : Math.min(0.95, Math.max(0.06, Math.exp(-leadH / 22) * (0.7 + 0.3 * rnd())))
      forecast.push([issued, +(LON0 + ix * DLON).toFixed(5), +(LAT0 + iy * DLAT).toFixed(5), +p.toFixed(2), Math.round(ref + gauss() * 6 * 3600000)])
    }
  }
}

writeFileSync(
  new URL('../public/data/eaton_spread_synthetic.json', import.meta.url),
  JSON.stringify({ synthetic: true, source: 'SYNTHETIC placeholder, not observed data', detections: out, forecast }),
)
console.log(`${out.length} synthetic detections, ${forecast.length} forecast cells written`)
