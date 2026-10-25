// Downloads NASA FIRMS VIIRS detections around the Eaton Fire (Jan 7-31, 2025)
// into public/data/eaton_viirs.json. Needs a free MAP_KEY in FIRMS_MAP_KEY.
//   FIRMS_MAP_KEY=... npm run fetch:firms
import { readFileSync, writeFileSync } from 'node:fs'

const key = process.env.FIRMS_MAP_KEY
if (!key) {
  console.error('Set FIRMS_MAP_KEY (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)')
  process.exit(1)
}

const perim = JSON.parse(readFileSync(new URL('../public/data/eaton_perimeter.geojson', import.meta.url)))
const pts = perim.features[0].geometry.coordinates.flat(2)
const m = 0.03
const west = Math.min(...pts.map((p) => p[0])) - m
const east = Math.max(...pts.map((p) => p[0])) + m
const south = Math.min(...pts.map((p) => p[1])) - m
const north = Math.max(...pts.map((p) => p[1])) + m
const area = [west, south, east, north].map((n) => n.toFixed(4)).join(',')

const SOURCES = ['VIIRS_SNPP_SP', 'VIIRS_NOAA20_SP', 'VIIRS_NOAA21_SP']
const out = []
for (const src of SOURCES) {
  for (let day = 7; day <= 31; day += 5) {
    const date = `2025-01-${String(day).padStart(2, '0')}`
    const days = Math.min(5, 32 - day)
    const url = `https://firms.modaps.eosdis.nasa.gov/api/area/csv/${key}/${src}/${area}/${days}/${date}`
    const res = await fetch(url)
    const text = await res.text()
    if (!res.ok || !text.startsWith('latitude')) {
      console.warn(`${src} ${date}: skipped (${res.status} ${text.slice(0, 80).replace(key, '***')})`)
      continue
    }
    const [head, ...rows] = text.trim().split('\n')
    const cols = head.split(',')
    const at = (r, c) => r[cols.indexOf(c)]
    for (const row of rows) {
      const r = row.split(',')
      if (at(r, 'confidence') === 'l') continue
      const hhmm = at(r, 'acq_time').padStart(4, '0')
      const t = Date.parse(`${at(r, 'acq_date')}T${hhmm.slice(0, 2)}:${hhmm.slice(2)}:00Z`)
      out.push([t, +at(r, 'longitude'), +at(r, 'latitude'), +at(r, 'frp'), src])
    }
  }
}

const seen = new Set()
const detections = out
  .filter((d) => d[0] >= Date.parse('2025-01-07T08:00:00Z') && !seen.has(d.join()) && seen.add(d.join()))
  .sort((a, b) => a[0] - b[0])
  .map(([t, lon, lat, frp]) => [t, lon, lat, frp])
writeFileSync(
  new URL('../public/data/eaton_viirs.json', import.meta.url),
  JSON.stringify({ source: 'NASA FIRMS VIIRS (SNPP, NOAA-20, NOAA-21), standard processing; low-confidence removed', bbox: area, detections }),
)
console.log(`${detections.length} detections written`)
