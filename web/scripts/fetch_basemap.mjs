// Pulls open US government map data for the Eaton area into public/data/basemap_eaton.json.
//   Roads, rail            US Census TIGERweb (public domain)
//   Places, landforms      USGS GNIS (public domain)
//   Schools, hospitals...  USGS National Map Structures (public domain)
//   Streams, water bodies  USGS NHD (public domain)
// Re-run: npm run fetch:basemap
import { writeFileSync } from 'node:fs'

const BBOX = [-118.29, 34.06, -117.95, 34.34]
const TIGER = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer'
const STRUCT = 'https://carto.nationalmap.gov/arcgis/rest/services/structures/MapServer'
const GNIS = 'https://carto.nationalmap.gov/arcgis/rest/services/geonames/MapServer'
const NHD = 'https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer'

async function query(base, layer, outFields = '*') {
  const out = []
  for (let offset = 0; ; offset += 2000) {
    const p = new URLSearchParams({
      where: '1=1', geometry: BBOX.join(','), geometryType: 'esriGeometryEnvelope', inSR: '4326',
      spatialRel: 'esriSpatialRelIntersects', outFields, returnGeometry: 'true', outSR: '4326', f: 'geojson',
      resultOffset: String(offset), resultRecordCount: '2000',
    })
    const r = await fetch(`${base}/${layer}/query?${p}`, { signal: AbortSignal.timeout(90000) })
    if (!r.ok) throw new Error(`${base}/${layer}: ${r.status}`)
    const d = await r.json()
    if (d.error) throw new Error(`${base}/${layer}: ${JSON.stringify(d.error)}`)
    out.push(...d.features)
    console.log(`  layer ${layer}: ${out.length} features`)
    if (d.features.length < 2000 && !d.exceededTransferLimit) break
  }
  return out
}

const r5 = (n) => Math.round(n * 1e5) / 1e5
const flat = (line) => line.flatMap(([x, y]) => [r5(x), r5(y)])
const lines = (g) => (g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [])
const rings = (g) => (g.type === 'Polygon' ? [g.coordinates[0]] : g.type === 'MultiPolygon' ? g.coordinates.map((p) => p[0]) : [])
const pt = (g) => (g.type === 'Point' ? g.coordinates : g.type === 'MultiPoint' ? g.coordinates[0] : null)

// TIGER MTFCC: S1100 primary, S1200 secondary, S1400 local, S1500 4WD track, S1640 service, S1710/S1720 trails
const roadClass = (m) => (m === 'S1100' ? 'major' : m === 'S1200' ? 'collector' : m === 'S1400' ? 'local' : m === 'S1500' || m === 'S1710' || m === 'S1720' ? 'trail' : 'service')

const roads = { major: [], collector: [], local: [], service: [], trail: [] }
for (const layer of [2, 6, 8]) {
  for (const f of await query(TIGER, layer, 'NAME,MTFCC')) {
    const cls = roadClass(f.properties.MTFCC)
    for (const l of lines(f.geometry)) roads[cls].push([f.properties.NAME ?? '', flat(l)])
  }
  console.log('TIGER layer', layer, Object.fromEntries(Object.entries(roads).map(([k, v]) => [k, v.length])))
}
const rail = []
for (const f of await query(TIGER, 9, 'MTFCC')) for (const l of lines(f.geometry)) rail.push(flat(l))

const STRUCT_TYPES = { 14: 'hospital', 16: 'fire_station', 18: 'police', 21: 'college', 23: 'school', 26: 'trailhead', 4: 'city_hall', 3: 'post_office', 11: 'landmark', 12: 'monument' }
const pois = []
for (const [layer, type] of Object.entries(STRUCT_TYPES)) {
  for (const f of await query(STRUCT, layer, 'name')) {
    const c = pt(f.geometry)
    if (c && f.properties.name) pois.push([f.properties.name, type, r5(c[0]), r5(c[1])])
  }
}

const places = []
const seen = new Set()
// Incorporated places and CDPs: Census TIGERweb centroids (GNIS "civil" places also hold old land grants)
const PLACES = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer'
for (const [layer, kind] of [[25, 'city'], [26, 'cdp']]) {
  for (const f of await query(PLACES, layer, 'BASENAME,CENTLAT,CENTLON')) {
    const { BASENAME: name, CENTLAT: lat, CENTLON: lon } = f.properties
    if (name && lat && lon) {
      places.push([name, kind, r5(+lon), r5(+lat)])
      seen.add(name.toLowerCase())
    }
  }
}
for (const [layer, kind] of [[3, 'populated'], [5, 'landform']]) {
  for (const f of await query(GNIS, layer, 'gaz_name,gaz_featureclass')) {
    const c = pt(f.geometry)
    const name = f.properties.gaz_name
    if (!c || !name || (kind === 'populated' && seen.has(name.toLowerCase()))) continue
    places.push([name, kind === 'landform' ? f.properties.gaz_featureclass.toLowerCase() : kind, r5(c[0]), r5(c[1])])
  }
}

const water = { lines: [], bodies: [] }
for (const f of await query(NHD, 6, 'ftype')) for (const l of lines(f.geometry)) water.lines.push(flat(l))
for (const f of await query(NHD, 12, 'ftype')) for (const ring of rings(f.geometry)) water.bodies.push(flat(ring))

// Layer 1 (civil places) also holds old land grants; keep a civil place only if GNIS also lists it as populated
const populated = new Set(places.filter((p) => p[1] === 'populated' || p[1] === 'cdp').map((p) => p[0].toLowerCase()))
for (let i = places.length - 1; i >= 0; i--) if (places[i][1] === 'city' && !populated.has(places[i][0].toLowerCase())) places.splice(i, 1)

const out = {
  source: 'US Census TIGERweb, USGS GNIS, USGS National Map Structures, USGS NHD (all US public domain)',
  bbox: BBOX, roads, rail, pois, places, water,
}
writeFileSync(new URL('../public/data/basemap_eaton.json', import.meta.url), JSON.stringify(out))
console.log({ roads: Object.fromEntries(Object.entries(roads).map(([k, v]) => [k, v.length])), rail: rail.length, pois: pois.length, places: places.length, water: [water.lines.length, water.bodies.length] })
