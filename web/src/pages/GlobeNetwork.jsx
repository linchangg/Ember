import { useEffect, useRef, useState } from 'react'
import { CITIES, LOCAL, STATES } from '../labels.js'

const EONET = 'https://eonet.gsfc.nasa.gov/api/v3/events?status=all&start=2025-01-05&end=2025-01-09&limit=500'
const FOCUS_MS = Date.parse('2025-01-07T12:00:00Z')
const WIN = [Date.parse('2025-01-05T00:00:00Z'), Date.parse('2025-01-10T00:00:00Z')]
const D2R = Math.PI / 180
const SIM_START = Date.parse('2025-01-07T00:00:00-08:00')
const SIM_END = Date.parse('2025-01-31T23:59:00-08:00')
const HOUR = 3600000
const fmtPT = (ms) => `${new Date(ms).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} PT`

const KINDS = {
  wildfires: { label: 'Wildfire', color: [255, 176, 58] },
  severeStorms: { label: 'Severe storm', color: [120, 190, 255] },
  volcanoes: { label: 'Volcano', color: [255, 90, 74] },
}
const OTHER = { label: 'Other', color: [150, 165, 215] }
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`

const vec = (lon, lat) => {
  const cl = Math.cos(lat * D2R)
  return [cl * Math.cos(lon * D2R), cl * Math.sin(lon * D2R), Math.sin(lat * D2R)]
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }) : 'Unknown')

/** Rule-based from EONET magnitude only; no magnitude means no rating. */
function riskOf(e) {
  const m = e.mag
  if (m == null) return 'Not rated'
  const cuts = e.id === 'severeStorms' ? [34, 64, 96] : e.id === 'wildfires' ? [1000, 10000, 100000] : null
  if (!cuts) return 'Not rated'
  return ['Low', 'Moderate', 'High', 'Extreme'][cuts.filter((c) => m >= c).length]
}
const rnd = (a, b) => a + Math.random() * (b - a)

/** Latest geometry of an EONET event -> [lon, lat]. */
function pickGeom(ev) {
  const gs = ev.geometry ?? []
  return gs.reduce((b, g) => (!b || Math.abs(Date.parse(g.date) - FOCUS_MS) < Math.abs(Date.parse(b.date) - FOCUS_MS) ? g : b), null)
}

function locate(ev) {
  const g = pickGeom(ev)
  if (!g) return null
  if (g.type === 'Point') return g.coordinates
  const ring = g.coordinates?.[0]
  if (!Array.isArray(ring) || !ring.length) return null
  return [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length]
}

function toEvents(events) {
  const out = []
  events.forEach((ev, i) => {
    if (!ev.geometry?.some((g) => Date.parse(g.date) >= WIN[0] && Date.parse(g.date) < WIN[1])) return
    const xy = locate(ev)
    if (!xy) return
    const kind = KINDS[ev.categories?.[0]?.id] ?? OTHER
    out.push({
      v: vec(xy[0], xy[1]),
      lon: xy[0],
      lat: xy[1],
      started: ev.geometry?.[0]?.date ?? null,
      mag: pickGeom(ev)?.magnitudeValue ?? null,
      link: ev.link ?? null,
      color: kind.color,
      kind: kind.label,
      id: ev.categories?.[0]?.id,
      title: ev.title,
      source: ev.sources?.[0]?.id ?? 'NASA EONET',
      phase: (i * 0.618) % 1,
    })
  })
  return out
}

/** Flatten country polygons into rings of unit vectors. */
function buildRings(geo) {
  const rings = []
  const push = (coords) => {
    const n = coords.length
    if (n < 4) return
    const xyz = new Float32Array(n * 3)
    const lonlat = new Float32Array(n * 2)
    coords.forEach(([lon, lat], i) => {
      const v = vec(lon, lat)
      xyz.set(v, i * 3)
      lonlat[i * 2] = lon
      lonlat[i * 2 + 1] = lat
    })
    rings.push({ n, xyz, lonlat })
  }
  for (const f of geo.features) {
    const g = f.geometry
    if (!g) continue
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : []
    polys.forEach((p) => p.forEach(push))
  }
  return rings
}

function sprite(color, size = 64) {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')
  const r = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  r.addColorStop(0, rgba([255, 255, 255], 0.95))
  r.addColorStop(0.18, rgba(color, 0.75))
  r.addColorStop(0.5, rgba(color, 0.18))
  r.addColorStop(1, rgba(color, 0))
  g.fillStyle = r
  g.fillRect(0, 0, size, size)
  return c
}

const TZ = 11 // near terrain tiles
const FAR_Z = 8 // coarse tiles for the far fade-in
const smooth = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

const tileCache = new Map()
const loadTile = (z, x, y) => {
  const key = `${z}/${x}/${y}`
  if (!tileCache.has(key)) {
    tileCache.set(key, new Promise((res) => {
      const im = new Image()
      im.crossOrigin = 'anonymous'
      im.onload = () => res(im)
      im.onerror = () => res(null)
      im.src = `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${z}/${x}/${y}.png`
    }))
  }
  return tileCache.get(key)
}

/** One z11 tile (~16 km) of elevation, smoothed using its neighbours so chunk seams line up. */
async function buildChunk(z, cx, cy) {
  const n = 2 ** z
  const cv = document.createElement('canvas')
  cv.width = cv.height = 768
  const g = cv.getContext('2d', { willReadFrequently: true })
  await Promise.all([0, 1, 2].flatMap((dx) => [0, 1, 2].map(async (dy) => {
    const im = await loadTile(z, ((cx - 1 + dx) % n + n) % n, Math.min(n - 1, Math.max(0, cy - 1 + dy)))
    if (im) g.drawImage(im, dx * 256, dy * 256)
  })))
  const px = g.getImageData(0, 0, 768, 768).data
  let el = new Float32Array(768 * 768)
  for (let i = 0; i < el.length; i++) el[i] = px[i * 4] * 256 + px[i * 4 + 1] + px[i * 4 + 2] / 256 - 32768
  for (let pass = 0; pass < 2; pass++) {
    const nx = new Float32Array(el)
    for (let j = 1; j < 767; j++) for (let i = 1; i < 767; i++) {
      let sum = 0
      for (let b = -1; b <= 1; b++) for (let a = -1; a <= 1; a++) sum += el[(j + b) * 768 + i + a]
      nx[j * 768 + i] = sum / 9
    }
    el = nx
  }
  const c = new Float32Array(257 * 257)
  for (let j = 0; j < 257; j++) for (let i = 0; i < 257; i++) c[j * 257 + i] = el[(256 + j) * 768 + 256 + i]
  const ch = { z, n, cx, cy, el: c, groups: [null, null, null, null], fine: false }
  ch.groups = contourChunk(ch, false)
  return ch
}

const STEP_M = 30.48 // 100 ft
const EDGE = [
  (i, j, h, lv) => [i + (lv - h[0]) / (h[1] - h[0]), j],
  (i, j, h, lv) => [i + 1, j + (lv - h[1]) / (h[2] - h[1])],
  (i, j, h, lv) => [i + (lv - h[3]) / (h[2] - h[3]), j + 1],
  (i, j, h, lv) => [i, j + (lv - h[0]) / (h[3] - h[0])],
]
const SEG = [[], [[3, 0]], [[0, 1]], [[3, 1]], [[1, 2]], [[3, 0], [1, 2]], [[0, 2]], [[3, 2]], [[2, 3]], [[0, 2]], [[0, 1], [2, 3]], [[1, 2]], [[3, 1]], [[0, 1]], [[3, 0]], []]

/** Contour a chunk. Layers: 0 = 2,000 ft, 1 = 1,000 ft, 2 = 500 ft, 3 = 100 ft (fine, built on demand). Far tiles only hold layers 0-1. */
function contourChunk(ch, fine) {
  const { el, cx, cy, n } = ch
  const out = [[], [], [], []]
  const h = [0, 0, 0, 0]
  const enc = (u, v, hh, arr) => {
    const X = cx + u / 256
    const Y = cy + v / 256
    const lo = (X / n) * 360 - 180
    const la = Math.atan(Math.sinh(Math.PI * (1 - (2 * Y) / n)))
    const c = Math.cos(la)
    arr.push(c * Math.cos(lo * D2R), c * Math.sin(lo * D2R), Math.sin(la), hh)
  }
  for (let j = 0; j < 256; j++) for (let i = 0; i < 256; i++) {
    h[0] = el[j * 257 + i]
    h[1] = el[j * 257 + i + 1]
    h[2] = el[(j + 1) * 257 + i + 1]
    h[3] = el[(j + 1) * 257 + i]
    const lo = Math.min(h[0], h[1], h[2], h[3])
    const hi = Math.max(h[0], h[1], h[2], h[3])
    for (let k = Math.ceil(lo / STEP_M); k <= Math.floor(hi / STEP_M); k++) {
      const coarse = k % 5 === 0
      if (fine === coarse || (ch.z < TZ && k % 10 !== 0)) continue
      const lv = k * STEP_M
      const idx = (h[0] > lv ? 1 : 0) | (h[1] > lv ? 2 : 0) | (h[2] > lv ? 4 : 0) | (h[3] > lv ? 8 : 0)
      const layer = !coarse ? 3 : k % 20 === 0 ? 0 : k % 10 === 0 ? 1 : 2
      for (const [e1, e2] of SEG[idx]) {
        const p = EDGE[e1](i, j, h, lv)
        const q = EDGE[e2](i, j, h, lv)
        enc(p[0], p[1], lv, out[layer])
        enc(q[0], q[1], lv, out[layer])
      }
    }
  }
  return out.map((a) => (a.length ? Float32Array.from(a) : null))
}

function newHeli(events) {
  for (let tries = 0; tries < 40; tries++) {
    const a = events[(Math.random() * events.length) | 0]
    const b = events[(Math.random() * events.length) | 0]
    const dot = a.v[0] * b.v[0] + a.v[1] * b.v[1] + a.v[2] * b.v[2]
    const ang = Math.acos(Math.min(1, dot))
    if (ang > 0.06 && ang < 0.9) return { a: a.v, b: b.v, ang, t: 0, speed: rnd(0.02, 0.04), hue: Math.random() }
  }
  return null
}

function slerp(a, b, ang, s) {
  const k0 = Math.sin((1 - s) * ang) / Math.sin(ang)
  const k1 = Math.sin(s * ang) / Math.sin(ang)
  return [a[0] * k0 + b[0] * k1, a[1] * k0 + b[1] * k1, a[2] * k0 + b[2] * k1]
}

function makeOrbit(i) {
  const inc = rnd(25, 85) * D2R
  const raan = (i / 7) * Math.PI * 2 + rnd(-0.3, 0.3)
  const u = [Math.cos(raan), Math.sin(raan), 0]
  const w = [-Math.sin(raan) * Math.cos(inc), Math.cos(raan) * Math.cos(inc), Math.sin(inc)]
  return { u, w, r: rnd(1.25, 1.65), speed: rnd(0.05, 0.12) * (i % 2 ? 1 : -1), th: rnd(0, 6.28), hue: i % 3 }
}
const orbitPos = (o, th) => [
  o.r * (Math.cos(th) * o.u[0] + Math.sin(th) * o.w[0]),
  o.r * (Math.cos(th) * o.u[1] + Math.sin(th) * o.w[1]),
  o.r * (Math.cos(th) * o.u[2] + Math.sin(th) * o.w[2]),
]

export default function GlobeNetwork() {
  const canvasRef = useRef(null)
  const wrapRef = useRef(null)
  const [events, setEvents] = useState(null)
  const [, setError] = useState(null)
  const [pick, setPick] = useState(null)
  const selRef = useRef(false)
  selRef.current = !!pick
  const fitRef = useRef(null)
  const spinRef = useRef(null)
  const [viirs, setViirs] = useState(null)
  const [synthetic, setSynthetic] = useState(false)
  const [forecast, setForecast] = useState(null)
  const fcRef = useRef(null)
  fcRef.current = forecast
  const [hover, setHover] = useState(null)
  const viirsRef = useRef(null)
  viirsRef.current = viirs
  const [simMs, setSimMs] = useState(Date.parse('2025-01-08T12:00:00-08:00'))
  const simRef = useRef(simMs)
  simRef.current = simMs
  const [playing, setPlaying] = useState(false)
  const showTime = !!pick && !!viirs && /eaton/i.test(pick.title)
  const timeRef = useRef(false)
  timeRef.current = showTime

  // Spread data: real VIIRS file wins; the synthetic placeholder is the fallback (see scripts/).
  useEffect(() => {
    const load = (url) => fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(url))))
    load('/data/eaton_viirs.json')
      .catch(() => load('/data/eaton_spread_synthetic.json'))
      .then((d) => {
        setSynthetic(!!d.synthetic)
        const issues = new Map()
        for (const [issued, lon, lat, p, eta] of d.forecast ?? []) {
          if (!issues.has(issued)) issues.set(issued, [])
          issues.get(issued).push({ lon, lat, p, eta })
        }
        setForecast(issues.size ? { times: [...issues.keys()].sort((a, b) => a - b), issues } : null)
        setViirs(d.detections.map(([t, lon, lat, frp]) => ({ t, lon, lat, frp })).sort((x, y) => x.t - y.t))
      })
      .catch(() => setViirs(null))
  }, [])

  useEffect(() => {
    if (!playing) return undefined
    const id = setInterval(() => setSimMs((m) => (m + HOUR >= SIM_END ? (setPlaying(false), SIM_END) : m + HOUR)), 90)
    return () => clearInterval(id)
  }, [playing])

  useEffect(() => {
    fetch(EONET)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`EONET ${r.status}`))))
      .then((d) => setEvents(toEvents(d.events)))
      .catch((e) => {
        setError(e.message)
        setEvents([])
      })
  }, [])

  useEffect(() => {
    if (events === null) return undefined
    const cv = canvasRef.current
    const ctx = cv.getContext('2d')
    let rings = []
    fetch('/data/countries.geojson')
      .then((r) => r.json())
      .then((g) => {
        countryLabels = g.features.map((f) => {
          const pr = f.properties
          return { name: pr.NAME_EN || pr.NAME, lon: pr.LABEL_X, lat: pr.LABEL_Y, min: 0.8 * 2 ** ((pr.MIN_LABEL ?? 3) - 2.5) }
        })
        rings = buildRings(g)
        proj = new Float32Array(rings.reduce((s, r) => s + r.n, 0) * 3)
        runners = Array.from({ length: 140 }, () => newRunner())
      })

    // view state
    const view = { lon: -98, lat: 28, zoom: 1, drag: null, spin: 0.064, target: null, cxo: 0, lastMove: 0, vel: 0 }
    let W = 0
    let H = 0
    let dpr = 1
    let proj = new Float32Array(0)
    let runners = []
    const chunks = new Map()
    let queue = []
    let active = 0
    let lastPlan = 0
    const mouse = { x: 0, y: 0, in: false }
    let hoverEv = null
    let countryLabels = []
    const tileKey = (lo, la, z = TZ) => [Math.floor(((lo + 180) / 360) * 2 ** z), Math.floor(((1 - Math.asinh(Math.tan(la * D2R)) / Math.PI) / 2) * 2 ** z)]
    const sampleAt = (lo, la) => {
      const X = ((lo + 180) / 360) * 2 ** TZ
      const Y = ((1 - Math.asinh(Math.tan(la * D2R)) / Math.PI) / 2) * 2 ** TZ
      const ch = chunks.get(`${TZ}_${Math.floor(X)}_${Math.floor(Y)}`)
      if (!ch || ch === 'loading') return 0
      const fi = (X - ch.cx) * 256
      const fj = (Y - ch.cy) * 256
      const i = Math.min(255, Math.floor(fi))
      const j = Math.min(255, Math.floor(fj))
      const u = fi - i
      const v = fj - j
      const e = ch.el
      return (e[j * 257 + i] * (1 - u) + e[j * 257 + i + 1] * u) * (1 - v) + (e[(j + 1) * 257 + i] * (1 - u) + e[(j + 1) * 257 + i + 1] * u) * v
    }
    const pump = () => {
      while (active < 4 && queue.length) {
        const [z, cx0, cy0] = queue.shift()
        const key = `${z}_${cx0}_${cy0}`
        if (chunks.has(key)) continue
        chunks.set(key, 'loading')
        active++
        buildChunk(z, cx0, cy0).then((ch) => chunks.set(key, ch)).catch(() => chunks.delete(key)).finally(() => { active--; pump() })
      }
    }
    // load whichever terrain chunks the current view needs (near tiles up close, coarse tiles from far away), nearest first
    const planChunks = () => {
      const ext = Math.hypot(Math.max(cx, W - cx), Math.max(cy, H - cy)) * (6371008 / R) * 1.05 + 12000
      const dlat = ext / 110574
      const dlon = ext / (111320 * Math.max(0.2, Math.cos(view.lat * D2R)))
      const keep = new Set()
      const next = []
      for (const [z, on] of [[FAR_Z, view.zoom > 25], [TZ, view.zoom > 200]]) {
        if (!on) continue
        const [xa, ya] = tileKey(view.lon - dlon, Math.min(84, view.lat + dlat), z)
        const [xb, yb] = tileKey(view.lon + dlon, Math.max(-84, view.lat - dlat), z)
        const [xc, yc] = tileKey(view.lon, view.lat, z)
        const want = []
        for (let x = xa; x <= xb; x++) for (let y = ya; y <= yb; y++) want.push([x, y, Math.hypot(x - xc, y - yc)])
        want.sort((p, q) => p[2] - q[2])
        for (const [x, y] of want.slice(0, 100)) {
          keep.add(`${z}_${x}_${y}`)
          if (!chunks.has(`${z}_${x}_${y}`)) next.push([z, x, y])
        }
      }
      queue = next
      if (chunks.size > 220) {
        for (const k of [...chunks.keys()]) if (!keep.has(k) && chunks.get(k) !== 'loading' && chunks.size > 180) chunks.delete(k)
      }
      pump()
    }
    let fire = null
    const fireCv = document.createElement('canvas')
    const maskCv = document.createElement('canvas')
    const lineCv = document.createElement('canvas')
    const fireKey = events.find((e) => /eaton/i.test(e.title))?.title
    fetch('/data/eaton_perimeter.geojson').then((r) => r.json()).then((g) => {
      fire = g.features[0].geometry.coordinates.flatMap((poly) => poly)
    }).catch(() => {})
    const small = [document.createElement('canvas'), document.createElement('canvas')]
    const SCALE = [6, 14]

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1)
      W = wrapRef.current.clientWidth
      H = wrapRef.current.clientHeight
      cv.width = W * dpr
      cv.height = H * dpr
      small.forEach((c, i) => {
        c.width = Math.ceil((W * dpr) / SCALE[i])
        c.height = Math.ceil((H * dpr) / SCALE[i])
      })
    }
    resize()
    window.addEventListener('resize', resize)

    const heatSprite = (core, mid) => {
      const c = document.createElement('canvas')
      c.width = c.height = 64
      const g = c.getContext('2d')
      const r = g.createRadialGradient(32, 32, 0, 32, 32, 32)
      r.addColorStop(0, rgba(core, 1))
      r.addColorStop(0.5, rgba(mid, 0.55))
      r.addColorStop(1, rgba(mid, 0))
      g.fillStyle = r
      g.fillRect(0, 0, 64, 64)
      return c
    }
    const heat = {
      burnt: heatSprite([128, 14, 20], [110, 12, 20]),
      hot: heatSprite([255, 80, 55], [230, 50, 40]),
      high: heatSprite([255, 140, 30], [245, 110, 20]),
      low: heatSprite([255, 205, 80], [250, 170, 60]),
    }
    const sprites = {}
    const spr = (c) => (sprites[c.join()] ??= sprite(c))
    const white = sprite([255, 255, 255])

    // ambient: stars + out-of-focus bokeh
    const stars = Array.from({ length: 260 }, () => ({ x: Math.random(), y: Math.random(), s: rnd(0.4, 1.4), tw: Math.random() * 6 }))
    const bokeh = Array.from({ length: 26 }, () => ({
      x: Math.random(), y: Math.random(), r: rnd(14, 70), vx: rnd(-0.004, 0.004), vy: rnd(-0.003, 0.003),
      c: [[40, 90, 200], [30, 70, 150], [56, 140, 220], [100, 80, 200]][(Math.random() * 4) | 0], a: rnd(0.03, 0.09),
    }))

    const helis = Array.from({ length: 16 }, () => newHeli(events)).filter(Boolean)
    helis.forEach((h) => (h.t = Math.random()))
    const orbits = Array.from({ length: 7 }, (_, i) => makeOrbit(i))
    const sats = events.filter((_, i) => i % 4 === 0)

    function newRunner() {
      let ri = (Math.random() * rings.length) | 0
      for (let k = 0; k < 6 && rings[ri].n < 25; k++) ri = (Math.random() * rings.length) | 0
      const pos = Math.random() * rings[ri].n
      return { ri, pos, start: pos, span: rnd(30, 60), speed: rnd(3, 7), hue: (Math.random() * 3) | 0 }
    }

    // projection: geo vector -> [sx, sy, depth]
    let cx = 0
    let cy = 0
    let R = 1
    let cl0 = 1
    let sl0 = 0
    let cp0 = 1
    let sp0 = 0
    const P = (x, y, z, out) => {
      const x1 = x * cl0 + y * sl0
      const y1 = -x * sl0 + y * cl0
      const x2 = x1 * cp0 + z * sp0
      const z2 = -x1 * sp0 + z * cp0
      out[0] = cx + R * y1
      out[1] = cy - R * z2
      out[2] = x2
      return out
    }
    const seen = (o) => (o[2] >= 0 || (o[0] - cx) ** 2 + (o[1] - cy) ** 2 > R * R ? 1 : 0.2)
    const tmp = [0, 0, 0]
    const tmp2 = [0, 0, 0]

    const HUES = [[90, 160, 255], [70, 130, 230], [110, 200, 255], [130, 120, 230]]
    const LEVELS = 6

    function frame(ms) {
      raf = requestAnimationFrame(frame)
      const t = ms / 1000
      if (view.target) {
        const tg = view.target
        const dl = ((tg.lon - view.lon + 540) % 360) - 180
        view.lon += dl * 0.08
        view.lat += (tg.lat - view.lat) * 0.08
        view.zoom *= (tg.zoom / view.zoom) ** 0.08
        if (Math.abs(dl) < 0.05 && Math.abs(tg.lat - view.lat) < 0.05 && Math.abs(Math.log(tg.zoom / view.zoom)) < 0.005) view.target = null
      } else if (!view.drag) view.lon += view.spin
      view.cxo += ((selRef.current ? 170 : 0) - view.cxo) * 0.1
      view.lon = ((view.lon + 540) % 360) - 180
      const rot = view.lon * D2R
      const tilt = view.lat * D2R
      cl0 = Math.cos(rot)
      sl0 = Math.sin(rot)
      cp0 = Math.cos(tilt)
      sp0 = Math.sin(tilt)
      cx = W * 0.5 - view.cxo
      cy = H * 0.5
      R = Math.min(W, H) * 0.34 * view.zoom

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'lighter'

      // bokeh + stars
      for (const b of bokeh) {
        b.x = (b.x + b.vx * 0.016 + 1) % 1
        b.y = (b.y + b.vy * 0.016 + 1) % 1
        ctx.globalAlpha = b.a * (0.7 + 0.3 * Math.sin(t * 0.6 + b.r))
        ctx.drawImage(spr(b.c), b.x * W - b.r, b.y * H - b.r, b.r * 2, b.r * 2)
      }
      ctx.globalAlpha = 1
      for (const s of stars) {
        ctx.fillStyle = `rgba(200,220,255,${0.2 + 0.35 * Math.sin(t * 1.3 + s.tw)})`
        ctx.fillRect(s.x * W, s.y * H, s.s, s.s)
      }

      // atmosphere + translucent body
      const halo = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 1.32)
      halo.addColorStop(0, 'rgba(40,110,230,0)')
      halo.addColorStop(0.44, 'rgba(40,110,230,0.04)')
      halo.addColorStop(0.5, 'rgba(60,130,240,0.38)')
      halo.addColorStop(0.7, 'rgba(70,90,200,0.10)')
      halo.addColorStop(1, 'rgba(30,80,200,0)')
      ctx.fillStyle = halo
      ctx.beginPath()
      ctx.arc(cx, cy, R * 1.32, 0, 7)
      ctx.fill()
      const body = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, R * 0.1, cx, cy, R)
      body.addColorStop(0, 'rgba(20,50,110,0.14)')
      body.addColorStop(0.75, 'rgba(12,30,80,0.12)')
      body.addColorStop(1, 'rgba(80,140,230,0.18)')
      ctx.fillStyle = body
      ctx.beginPath()
      ctx.arc(cx, cy, R, 0, 7)
      ctx.fill()
      ctx.lineWidth = 1
      ctx.strokeStyle = 'rgba(140,185,245,0.65)'
      ctx.beginPath()
      ctx.arc(cx, cy, R, 0, 7)
      ctx.stroke()

      // graticule
      ctx.lineWidth = 0.5
      for (let lon = -180; lon < 180; lon += 15) {
        ctx.beginPath()
        for (let lat = -90; lat <= 90; lat += 5) {
          const v = vec(lon, lat)
          P(v[0], v[1], v[2], tmp)
          const a = seenAlpha(tmp)
          ctx.strokeStyle = `rgba(90,140,230,${0.12 * a})`
          if (lat === -90) ctx.moveTo(tmp[0], tmp[1])
          else ctx.lineTo(tmp[0], tmp[1])
        }
        ctx.stroke()
      }
      for (let lat = -75; lat <= 75; lat += 15) {
        ctx.beginPath()
        for (let lon = -180; lon <= 180; lon += 5) {
          const v = vec(lon, lat)
          P(v[0], v[1], v[2], tmp)
          if (lon === -180) ctx.moveTo(tmp[0], tmp[1])
          else ctx.lineTo(tmp[0], tmp[1])
        }
        ctx.strokeStyle = 'rgba(90,140,230,0.09)'
        ctx.stroke()
      }
      function seenAlpha(o) {
        return seen(o) === 1 ? 1 : 0.4
      }

      // terrain contours: start far away with only the steepest (2,000 ft) lines, slowly add finer ones as you close in
      const lz = Math.log(view.zoom)
      const layerW = [
        smooth(Math.log(40), Math.log(400), lz),
        smooth(Math.log(120), Math.log(700), lz),
        smooth(Math.log(350), Math.log(1100), lz),
        smooth(Math.log(900), Math.log(2500), lz),
      ]
      const tf = layerW[0]
      const xf = smooth(Math.log(300), Math.log(650), lz) // far tiles hand over to near tiles
      const lift = (1.6 * R) / 6371000
      const drawn = []
      if (view.zoom > 25 && ms - lastPlan > 250) {
        lastPlan = ms
        planChunks()
      }
      if (tf > 0.01) {
        const STYLE = [
          { w: 1.2, c: [150, 215, 255], a: 0.8 },
          { w: 0.9, c: [120, 190, 250], a: 0.6 },
          { w: 0.8, c: [95, 170, 245], a: 0.5 },
          { w: 0.6, c: [70, 140, 225], a: 0.38 },
        ]
        const paths = [0, 1].map(() => [new Path2D(), new Path2D(), new Path2D(), new Path2D()])
        const mpp1 = 6371008 / R
        chunks.forEach((ch) => {
          if (!ch || ch === 'loading') return
          const far = ch.z < TZ
          const lodW = far ? 1 - xf : xf
          if (lodW < 0.01) return
          const crad = (40075016 / ch.n) * 0.9 / mpp1
          const clo = ((ch.cx + 0.5) / ch.n) * 360 - 180
          const cla = Math.atan(Math.sinh(Math.PI * (1 - (2 * (ch.cy + 0.5)) / ch.n))) / D2R
          const cv3 = vec(clo, cla)
          P(cv3[0], cv3[1], cv3[2], tmp)
          if (tmp[2] < 0 || tmp[0] < -crad || tmp[0] > W + crad || tmp[1] < -crad - 100 || tmp[1] > H + crad + 100) return
          if (!far && layerW[3] > 0.01 && !ch.fine) {
            ch.groups[3] = contourChunk(ch, true)[3]
            ch.fine = true
          }
          for (let ly = 0; ly < 4; ly++) {
            const arr = ch.groups[ly]
            if (!arr || layerW[ly] * lodW < 0.01) continue
            const pa = paths[far ? 0 : 1][ly]
            for (let i = 0; i < arr.length; i += 8) {
              P(arr[i], arr[i + 1], arr[i + 2], tmp)
              if (tmp[2] < 0) continue
              const x1 = tmp[0]
              const y1 = tmp[1] - arr[i + 3] * lift
              P(arr[i + 4], arr[i + 5], arr[i + 6], tmp)
              const y2 = tmp[1] - arr[i + 7] * lift
              if ((x1 < -40 && tmp[0] < -40) || (x1 > W + 40 && tmp[0] > W + 40) || (y1 < -40 && y2 < -40) || (y1 > H + 40 && y2 > H + 40)) continue
              pa.moveTo(x1, y1)
              pa.lineTo(tmp[0], y2)
            }
          }
        })
        paths.forEach((set, lod) => set.forEach((pa, ly) => {
          const f = layerW[ly] * (lod === 0 ? 1 - xf : xf)
          if (f < 0.01) return
          const st = STYLE[ly]
          ctx.lineWidth = st.w
          ctx.strokeStyle = rgba(st.c, st.a * f)
          ctx.stroke(pa)
          drawn.push({ pa, ly, f, w: st.w })
        }))
      }

      // fire perimeter: red fill, with the terrain lines inside it turned red
      const ff = smooth(Math.log(25), Math.log(120), lz)
      const vi = viirsRef.current
      if (vi && ff > 0.01) {
        // VIIRS detections up to the chosen time: overlapping detections add up, recent ones glow brighter
        if (fireCv.width !== W || fireCv.height !== H) {
          fireCv.width = W
          fireCv.height = H
          maskCv.width = W
          maskCv.height = H
          lineCv.width = W
          lineCv.height = H
        }
        const fg = fireCv.getContext('2d')
        fg.clearRect(0, 0, W, H)
        fg.globalCompositeOperation = 'lighter'
        const rad = Math.max(5, 650 / (6371008 / R))
        const T = simRef.current
        // predicted spread: latest forecast issued at or before T, cells whose ETA is still ahead
        const fc = fcRef.current
        if (fc) {
          let it = -1
          for (let i = 0; i < fc.times.length && fc.times[i] <= T; i++) it = i
          for (const c of it >= 0 ? fc.issues.get(fc.times[it]) : []) {
            if (c.eta <= T || c.p < 0.05) continue
            c.v ??= vec(c.lon, c.lat)
            if (c.h === undefined && chunks.get(`${TZ}_${tileKey(c.lon, c.lat).join('_')}`) instanceof Object) c.h = sampleAt(c.lon, c.lat)
            P(c.v[0], c.v[1], c.v[2], tmp)
            if (tmp[2] < 0) continue
            const y = tmp[1] - (c.h || 0) * lift
            if (tmp[0] < -rad || tmp[0] > W + rad || y < -rad || y > H + rad) continue
            const high = c.p >= 0.5
            fg.globalAlpha = (high ? 0.3 + 0.4 * c.p : 0.12 + 0.4 * c.p) * ff
            fg.drawImage(high ? heat.high : heat.low, tmp[0] - rad, y - rad, rad * 2, rad * 2)
          }
        }
        for (const d of vi) {
          if (d.t > T) break
          d.v ??= vec(d.lon, d.lat)
          if (d.h === undefined && chunks.get(`${TZ}_${tileKey(d.lon, d.lat).join('_')}`) instanceof Object) d.h = sampleAt(d.lon, d.lat)
          P(d.v[0], d.v[1], d.v[2], tmp)
          if (tmp[2] < 0) continue
          const y = tmp[1] - (d.h || 0) * lift
          if (tmp[0] < -rad || tmp[0] > W + rad || y < -rad || y > H + rad) continue
          fg.globalCompositeOperation = 'source-over'
          fg.globalAlpha = 0.4 * ff
          fg.drawImage(heat.burnt, tmp[0] - rad, y - rad, rad * 2, rad * 2)
          const age = T - d.t
          if (age < 12 * HOUR) {
            fg.globalCompositeOperation = 'lighter'
            fg.globalAlpha = 0.3 * Math.exp(-age / (5 * HOUR)) * ff
            fg.drawImage(heat.hot, tmp[0] - rad, y - rad, rad * 2, rad * 2)
          }
        }
        fg.globalCompositeOperation = 'lighter'
        fg.globalAlpha = 1
        const lg = lineCv.getContext('2d')
        lg.clearRect(0, 0, W, H)
        for (const d of drawn) {
          lg.lineWidth = d.w + 0.4
          lg.strokeStyle = `rgba(255,${120 - d.ly * 25},75,${Math.min(1, 0.9 * d.f + 0.1)})`
          lg.stroke(d.pa)
        }
        lg.globalCompositeOperation = 'destination-in'
        lg.drawImage(fireCv, 0, 0)
        lg.drawImage(fireCv, 0, 0)
        lg.globalCompositeOperation = 'source-over'
        ctx.drawImage(fireCv, 0, 0, W, H)
        ctx.drawImage(lineCv, 0, 0, W, H)
      } else if (fire && ff > 0.01) {
        const path = new Path2D()
        for (const ring of fire) {
          ring.forEach(([lo, la], i) => {
            const v = vec(lo, la)
            P(v[0], v[1], v[2], tmp)
            const y = tmp[1] - sampleAt(lo, la) * lift
            if (i === 0) path.moveTo(tmp[0], y)
            else path.lineTo(tmp[0], y)
          })
          path.closePath()
        }
        // feathered burn glow: soft edge, denser toward the middle of the footprint
        const mpp0 = 6371008 / R
        const bl = Math.max(1.5, Math.min(36, 500 / mpp0))
        if (fireCv.width !== W || fireCv.height !== H) {
          fireCv.width = W
          fireCv.height = H
          maskCv.width = W
          maskCv.height = H
        }
        const fg = fireCv.getContext('2d')
        const mg = maskCv.getContext('2d')
        fg.clearRect(0, 0, W, H)
        mg.clearRect(0, 0, W, H)
        fg.filter = 'none'
        for (const [k, a] of [[2.4, 0.2], [1.1, 0.28], [0.45, 0.3]]) {
          fg.filter = `blur(${bl * k}px)`
          fg.fillStyle = `rgba(215,45,35,${a * ff})`
          fg.fill(path, 'evenodd')
        }
        mg.filter = `blur(${bl}px)`
        mg.fillStyle = '#fff'
        mg.fill(path, 'evenodd')
        mg.filter = 'none'
        const lg = lineCv.getContext('2d')
        if (lineCv.width !== W || lineCv.height !== H) {
          lineCv.width = W
          lineCv.height = H
        }
        lg.clearRect(0, 0, W, H)
        for (const d of drawn) {
          lg.lineWidth = d.w + 0.4
          lg.strokeStyle = `rgba(255,${120 - d.ly * 25},75,${Math.min(1, 0.9 * d.f * ff + 0.1 * ff)})`
          lg.stroke(d.pa)
        }
        lg.globalCompositeOperation = 'destination-in'
        lg.drawImage(maskCv, 0, 0)
        lg.globalCompositeOperation = 'source-over'
        ctx.drawImage(fireCv, 0, 0, W, H)
        ctx.drawImage(lineCv, 0, 0, W, H)
      }

      // borders: project once, bucket front segments by travelling colour wave
      if (rings.length) {
        let off = 0
        const buckets = Array.from({ length: LEVELS }, () => new Path2D())
        const back = new Path2D()
        const dots = new Path2D()
        for (const r of rings) {
          const { xyz, lonlat, n } = r
          r.off = off
          for (let i = 0; i < n; i++) {
            P(xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2], tmp)
            const o = (off + i) * 3
            proj[o] = tmp[0]
            proj[o + 1] = tmp[1]
            proj[o + 2] = tmp[2]
          }
          for (let i = 0; i < n - 1; i++) {
            const a = (off + i) * 3
            const b = a + 3
            const da = proj[a + 2]
            const db = proj[b + 2]
            if (da >= 0 && db >= 0) {
              const w = 0.5 + 0.5 * Math.sin(lonlat[i * 2] * 0.045 + lonlat[i * 2 + 1] * 0.03 - t * 1.4)
              const L = Math.min(LEVELS - 1, (w * LEVELS) | 0)
              buckets[L].moveTo(proj[a], proj[a + 1])
              buckets[L].lineTo(proj[b], proj[b + 1])
              if (i % 3 === 0) dots.rect(proj[a] - 0.7, proj[a + 1] - 0.7, 1.4, 1.4)
            } else if (da < 0 && db < 0) {
              back.moveTo(proj[a], proj[a + 1])
              back.lineTo(proj[b], proj[b + 1])
            }
          }
          off += n
        }
        ctx.lineWidth = 0.55
        ctx.strokeStyle = 'rgba(90,130,210,0.18)'
        ctx.stroke(back)
        buckets.forEach((p, L) => {
          const w = L / (LEVELS - 1)
          ctx.strokeStyle = `hsla(${212 + w * 10},80%,${55 + w * 20}%,${0.45 + w * 0.45})`
          ctx.stroke(p)
        })
        ctx.fillStyle = 'rgba(200,225,255,0.7)'
        ctx.fill(dots)

        // slow light pulses that glide along borders, drawn at line width
        ctx.lineWidth = 0.9
        for (const ru of runners) {
          const r = rings[ru.ri]
          ru.pos += ru.speed * 0.016
          if (ru.pos - ru.start > ru.span || ru.pos >= r.n - 1) Object.assign(ru, newRunner())
          const head = Math.floor(ru.pos)
          const life = Math.sin(Math.PI * Math.min(1, Math.max(0, (ru.pos - ru.start) / ru.span)))
          const col = HUES[ru.hue]
          for (let k = 0; k < 8; k++) {
            const i = head - k
            if (i < 0 || i >= r.n - 1) continue
            const a = (r.off + i) * 3
            if (proj[a + 2] < 0 || proj[a + 5] < 0) continue
            ctx.strokeStyle = rgba(col, (1 - k / 8) * 0.9 * life)
            ctx.beginPath()
            ctx.moveTo(proj[a], proj[a + 1])
            ctx.lineTo(proj[a + 3], proj[a + 4])
            ctx.stroke()
          }
        }
      }

      // orbits + satellites + downlinks
      ctx.lineWidth = 0.6
      orbits.forEach((o, oi) => {
        const col = HUES[(o.hue + 2) % 4]
        let prev = null
        for (let k = 0; k <= 96; k++) {
          const th = (k / 96) * Math.PI * 2
          const p = orbitPos(o, th)
          P(p[0], p[1], p[2], tmp)
          if (prev) {
            ctx.strokeStyle = rgba(col, 0.28 * seen(tmp))
            ctx.beginPath()
            ctx.moveTo(prev[0], prev[1])
            ctx.lineTo(tmp[0], tmp[1])
            ctx.stroke()
          }
          prev = [tmp[0], tmp[1]]
        }
        o.th += o.speed * 0.016
        const p = orbitPos(o, o.th)
        P(p[0], p[1], p[2], tmp)
        const a = seen(tmp)
        ctx.globalAlpha = a
        ctx.drawImage(spr(col), tmp[0] - 7, tmp[1] - 7, 14, 14)
        ctx.globalAlpha = 1
        // downlink to the nearest event beneath it
        const d = Math.hypot(...p)
        let best = null
        let bd = 0.93
        for (const e of sats) {
          const dp = (e.v[0] * p[0] + e.v[1] * p[1] + e.v[2] * p[2]) / d
          if (dp > bd) {
            bd = dp
            best = e
          }
        }
        if (best) {
          P(best.v[0], best.v[1], best.v[2], tmp2)
          if (tmp2[2] > 0) {
            ctx.setLineDash([3, 4])
            ctx.lineDashOffset = -t * 12
            ctx.strokeStyle = rgba(col, 0.6 * a)
            ctx.beginPath()
            ctx.moveTo(tmp[0], tmp[1])
            ctx.lineTo(tmp2[0], tmp2[1])
            ctx.stroke()
            ctx.setLineDash([])
          }
        }
        if (oi === -1) return
      })

      // helicopter routes (simulated)
      helis.forEach((h, hi) => {
        h.t += h.speed * 0.016
        if (h.t > 1.15) {
          const n = newHeli(events)
          if (n) Object.assign(h, n)
          else h.t = 0
        }
        const col = HUES[(h.hue * 4) | 0]
        const lift = 0.04 + 0.05 * h.ang
        const at = (s) => {
          const p = slerp(h.a, h.b, h.ang, s)
          const k = 1 + lift * Math.sin(Math.PI * s)
          return P(p[0] * k, p[1] * k, p[2] * k, tmp)
        }
        const fade = Math.min(1, h.t * 6, (1.15 - h.t) * 6)
        ctx.lineWidth = 0.7
        let pv = null
        for (let k = 0; k <= 36; k++) {
          const s = k / 36
          const o = at(s)
          if (pv) {
            ctx.strokeStyle = rgba(col, 0.22 * fade * seen(o))
            ctx.beginPath()
            ctx.moveTo(pv[0], pv[1])
            ctx.lineTo(o[0], o[1])
            ctx.stroke()
          }
          pv = [o[0], o[1]]
        }
        const hs = Math.min(1, h.t)
        pv = null
        for (let k = 0; k <= 14; k++) {
          const s = Math.max(0, hs - 0.14 + (0.14 * k) / 14)
          const o = at(s)
          if (pv) {
            ctx.lineWidth = 0.9
            ctx.strokeStyle = rgba(col, (k / 14) * fade * seen(o))
            ctx.beginPath()
            ctx.moveTo(pv[0], pv[1])
            ctx.lineTo(o[0], o[1])
            ctx.stroke()
          }
          pv = [o[0], o[1]]
        }
        if (hi < 0) return
      })

      // events
      for (const e of events) {
        P(e.v[0], e.v[1], e.v[2], tmp)
        const a = seen(tmp)
        const p = (t * 0.45 + e.phase) % 1
        ctx.globalAlpha = a * 0.5
        ctx.drawImage(spr(e.color), tmp[0] - 6, tmp[1] - 6, 12, 12)
        ctx.globalAlpha = a
        if (a === 1) {
          ctx.strokeStyle = rgba(e.color, 0.7 * (1 - p))
          ctx.lineWidth = 0.8
          ctx.beginPath()
          ctx.arc(tmp[0], tmp[1], 3 + p * 12, 0, 7)
          ctx.stroke()
        }
        ctx.fillStyle = '#ffe9c4'
        ctx.fillRect(tmp[0] - 0.9, tmp[1] - 0.9, 1.8, 1.8)
      }
      ctx.globalAlpha = 1

      // bloom: blurred downsamples added back on top
      small.forEach((c, i) => {
        const g = c.getContext('2d')
        g.setTransform(1, 0, 0, 1, 0, 0)
        g.globalCompositeOperation = 'copy'
        g.drawImage(cv, 0, 0, c.width, c.height)
      })
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.globalCompositeOperation = 'lighter'
      ctx.globalAlpha = 0.14 * (1 - 0.6 * tf)
      ctx.drawImage(small[0], 0, 0, cv.width, cv.height)
      ctx.globalAlpha = 0.16 * (1 - 0.6 * tf)
      ctx.drawImage(small[1], 0, 0, cv.width, cv.height)
      ctx.globalAlpha = 1
      ctx.globalCompositeOperation = 'source-over'

      // labels: fewer when far, more when close
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const placed = []
      const widths = frame.widths ?? (frame.widths = new Map())
      const zoom = view.zoom
      const draw = (text, x, y, font, color, dot) => {
        ctx.font = font
        const wk = font + text
        let tw = widths.get(wk)
        if (tw === undefined) {
          tw = ctx.measureText(text).width
          widths.set(wk, tw)
        }
        const left = dot ? x + 6 : x - tw / 2
        const r = [left - 3, y - 8, left + tw + 3, y + 8]
        if (r[2] < 0 || r[0] > W || r[3] < 0 || r[1] > H) return
        if (placed.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) return
        placed.push(r)
        if (dot === 'peak') {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.moveTo(x, y - 4)
          ctx.lineTo(x + 3.5, y + 2.5)
          ctx.lineTo(x - 3.5, y + 2.5)
          ctx.fill()
        } else if (dot) {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(x, y, 2.2, 0, 7)
          ctx.fill()
        }
        ctx.textBaseline = 'middle'
        ctx.fillStyle = color
        ctx.fillText(text, left, y)
      }
      const show = (lo, la, minZ, maxZ, local) => {
        const a = smooth(minZ, minZ * 1.5, zoom) * (maxZ ? 1 - smooth(maxZ * 0.6, maxZ, zoom) : 1)
        if (a < 0.02) return null
        const v = vec(lo, la)
        P(v[0], v[1], v[2], tmp)
        const front = smooth(0.08, 0.4, tmp[2])
        if (front < 0.02) return null
        const y = local ? tmp[1] - sampleAt(lo, la) * lift : tmp[1]
        return [tmp[0], y, a * front]
      }
      ctx.letterSpacing = '0px'
      for (const [name, lo, la, kind, minZ] of LOCAL) {
        const q = show(lo, la, minZ, 0, true)
        if (!q) continue
        const big = kind === 'region'
        ctx.letterSpacing = big ? '3px' : '1px'
        draw(big ? name.toUpperCase() : name, q[0], q[1], big ? "8.5px Michroma, 'Eurostile', 'Orbitron', sans-serif" : "9.5px Michroma, 'Eurostile', 'Orbitron', sans-serif", big ? `rgba(110,160,235,${0.75 * q[2]})` : `rgba(165,210,255,${0.95 * q[2]})`, kind === 'peak' ? 'peak' : kind === 'town')
        ctx.letterSpacing = '0px'
      }
      ctx.letterSpacing = '1px'
      for (const [name, lo, la, minZ] of CITIES) {
        if (zoom > 25 && LOCAL.some((l) => l[0] === name)) continue
        const q = show(lo, la, minZ * 3, 0, false)
        if (q) draw(name, q[0], q[1], "9px Michroma, 'Eurostile', 'Orbitron', sans-serif", `rgba(150,200,255,${0.92 * q[2]})`, true)
      }
      for (const [name, lo, la] of STATES) {
        const q = show(lo, la, 9, 160, false)
        if (q) draw(name, q[0], q[1], "8px Michroma, 'Eurostile', 'Orbitron', sans-serif", `rgba(105,150,225,${0.7 * q[2]})`, false)
      }
      ctx.letterSpacing = '3px'
      for (const c of countryLabels) {
        const q = show(c.lon, c.lat, c.min * 3, 45, false)
        if (q) draw(c.name.toUpperCase(), q[0], q[1], "8px Michroma, 'Eurostile', 'Orbitron', sans-serif", `rgba(90,135,215,${0.75 * q[2]})`, false)
      }
      ctx.letterSpacing = '0px'
      ctx.textBaseline = 'alphabetic'

      // hover: nearest event under the pointer
      if (mouse.in && !view.drag) {
        let best = null
        let bd = 12
        for (const ev of events) {
          P(ev.v[0], ev.v[1], ev.v[2], tmp)
          if (tmp[2] < 0) continue
          const dist = Math.hypot(tmp[0] - mouse.x, tmp[1] - mouse.y)
          if (dist < bd) {
            bd = dist
            best = ev
          }
        }
        if (best !== hoverEv) {
          hoverEv = best
          cv.style.cursor = best ? 'pointer' : ''
          setHover(best ? { title: best.title, x: mouse.x, y: mouse.y } : null)
        }
      }

      // US-customary scale bar (true at the screen centre)
      const mpp = 6371008 / R
      const NICE = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
      const opts = [...NICE.map((n) => [n, 'ft', n * 0.3048]), ...NICE.map((n) => [n, 'mi', n * 1609.344])]
      let best = opts[0]
      for (const o of opts) if (o[2] / mpp <= 150 && o[2] > best[2]) best = o
      const len = best[2] / mpp
      const bx = 28
      const by = H - (timeRef.current ? 200 : 40)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.strokeStyle = 'rgba(190,215,250,0.85)'
      ctx.fillStyle = 'rgba(190,215,250,0.9)'
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(bx, by - 6)
      ctx.lineTo(bx, by)
      ctx.lineTo(bx + len, by)
      ctx.lineTo(bx + len, by - 6)
      ctx.stroke()
      ctx.font = "9.5px Michroma, 'Eurostile', 'Orbitron', sans-serif"
      ctx.fillText(`${best[0].toLocaleString()} ${best[1] === 'mi' ? (best[0] === 1 ? 'mile' : 'miles') : 'ft'}`, bx, by - 12)
      const ci = layerW[3] > 0.5 ? '100 ft' : layerW[2] > 0.5 ? '500 ft' : layerW[1] > 0.5 ? '1,000 ft' : layerW[0] > 0.5 ? '2,000 ft' : null
      if (ci) {
        ctx.fillStyle = 'rgba(140,170,215,0.8)'
        ctx.fillText(`Contour interval ${ci}`, bx, by + 18)
      }
    }
    document.fonts?.load('10px Michroma').then(() => { frame.widths = null })
    let raf = requestAnimationFrame(frame)

    fitRef.current = (ev) => {
      view.spin = 0
      let zoom = 700
      let lon = ev.lon
      let lat = ev.lat
      if (ev.title === fireKey && fire) {
        const pts = fire.flat()
        const xs = pts.map((p) => p[0])
        const ys = pts.map((p) => p[1])
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
        lon = (x0 + x1) / 2
        lat = (y0 + y1) / 2
        const dx = (x1 - x0) * 111320 * Math.cos(lat * D2R)
        const dy = (y1 - y0) * 110574
        const mpp = Math.max(dx / ((W - 340) * 0.8), dy / (H * 0.7))
        zoom = Math.max(20, Math.min(3000, 6371008 / mpp / (0.34 * Math.min(W, H))))
      }
      view.target = { lon, lat, zoom }
    }

    spinRef.current = (dir) => {
      view.target = null
      view.spin = dir * 0.064
    }

    const focus = new URLSearchParams(window.location.search).get('focus')?.toLowerCase()
    const first = focus ? events.find((e) => e.title.toLowerCase().includes(focus)) : null
    if (first) {
      view.spin = 0
      fitRef.current(first)
      setPick(first)
    }

    // interaction
    const onDown = (e) => {
      view.spin = 0
      view.target = null
      view.vel = 0
      view.drag = { x: e.clientX, y: e.clientY, lon: view.lon, lat: view.lat, moved: false }
      cv.setPointerCapture(e.pointerId)
    }
    const onMove = (e) => {
      const d = view.drag
      const rect = cv.getBoundingClientRect()
      mouse.x = e.clientX - rect.left
      mouse.y = e.clientY - rect.top
      mouse.in = true
      if (!d) {
        if (hoverEv) setHover({ title: hoverEv.title, x: mouse.x, y: mouse.y })
        return
      }
      const k = 0.25 / view.zoom
      if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true
      const prev = view.lon
      view.lon = d.lon - (e.clientX - d.x) * k
      view.vel = view.lon - prev
      view.lastMove = performance.now()
      view.lat = Math.max(-80, Math.min(80, d.lat + (e.clientY - d.y) * k))
    }
    const onUp = (e) => {
      const d = view.drag
      view.drag = null
      if (d?.moved && performance.now() - view.lastMove < 120 && Math.abs(view.vel) > 0.05) view.spin = Math.sign(view.vel) * 0.064
      if (d && !d.moved) {
        const rect = cv.getBoundingClientRect()
        const mx = e.clientX - rect.left
        const my = e.clientY - rect.top
        let best = null
        let bd = 14
        for (const ev of events) {
          P(ev.v[0], ev.v[1], ev.v[2], tmp)
          if (tmp[2] < 0) continue
          const dist = Math.hypot(tmp[0] - mx, tmp[1] - my)
          if (dist < bd) {
            bd = dist
            best = ev
          }
        }
        if (best) fitRef.current(best)
        setPick(best)
      }
    }
    // zoom about the cursor: keep the point under the pointer fixed on screen
    const onWheel = (e) => {
      e.preventDefault()
      view.target = null
      const rect = cv.getBoundingClientRect()
      const sx = e.clientX - rect.left
      const sy = e.clientY - rect.top
      const nz = Math.max(0.55, Math.min(3000, view.zoom * Math.exp(-e.deltaY * 0.0015)))
      const y1 = (sx - cx) / R
      const z2 = (cy - sy) / R
      const d2 = 1 - y1 * y1 - z2 * z2
      if (d2 <= 0) {
        view.zoom = nz
        return
      }
      // geo point under the cursor (inverse of the projection)
      const x2 = Math.sqrt(d2)
      const x1 = x2 * cp0 - z2 * sp0
      const gz = x2 * sp0 + z2 * cp0
      const gx = x1 * cl0 - y1 * sl0
      const gy = x1 * sl0 + y1 * cl0
      const lg = Math.atan2(gy, gx)
      const sg = gz
      const cg = Math.hypot(gx, gy)
      // choose lon/lat of the view so that point lands under the cursor at the new scale
      const nR = R * (nz / view.zoom)
      const ny1 = (sx - cx) / nR
      const nz2 = (cy - sy) / nR
      const ratio = ny1 / cg
      if (cg < 1e-6 || Math.abs(ratio) > 1) {
        view.zoom = nz
        return
      }
      const rot = lg - Math.asin(ratio)
      const nx1 = Math.sqrt(Math.max(0, cg * cg - ny1 * ny1))
      const r = Math.hypot(nx1, sg)
      const q = nz2 / r
      if (Math.abs(q) > 1) {
        view.zoom = nz
        return
      }
      const a = Math.atan2(nx1, sg)
      const cands = [Math.acos(q) - a, -Math.acos(q) - a].map((t) => ((t + Math.PI * 3) % (Math.PI * 2)) - Math.PI)
      const cur = view.lat * D2R
      const tilt = Math.abs(cands[0] - cur) < Math.abs(cands[1] - cur) ? cands[0] : cands[1]
      view.zoom = nz
      view.lon = ((rot / D2R + 540) % 360) - 180
      view.lat = Math.max(-80, Math.min(80, tilt / D2R))
    }
    const onLeave = () => {
      mouse.in = false
      hoverEv = null
      setHover(null)
    }
    cv.addEventListener('pointerleave', onLeave)
    cv.addEventListener('pointerdown', onDown)
    cv.addEventListener('pointermove', onMove)
    cv.addEventListener('pointerup', onUp)
    cv.addEventListener('wheel', onWheel, { passive: false })

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      cv.removeEventListener('pointerleave', onLeave)
      cv.removeEventListener('pointerdown', onDown)
      cv.removeEventListener('pointermove', onMove)
      cv.removeEventListener('pointerup', onUp)
      cv.removeEventListener('wheel', onWheel)
    }
  }, [events])

  return (
    <div className="gn" ref={wrapRef}>
      <div className="gn__bg" />
      <canvas ref={canvasRef} className="gn__map" />
      <header className="gn__bar">
        <span className="gn__title">EMBERLINE</span>
      </header>
      <div className="gn__spin">
        <button onClick={() => spinRef.current?.(-1)} aria-label="Spin counterclockwise" title="Spin counterclockwise">↺</button>
        <button onClick={() => spinRef.current?.(0)} aria-label="Stop spin" title="Stop">❚❚</button>
        <button onClick={() => spinRef.current?.(1)} aria-label="Spin clockwise" title="Spin clockwise">↻</button>
      </div>
      {hover && !pick ? <div className="gn__tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>{hover.title}</div> : null}
      {hover && pick && hover.title !== pick.title ? <div className="gn__tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>{hover.title}</div> : null}
      {showTime ? (
        <div className="gn__time">
          <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
          <div className="gn__time-main">
            {synthetic ? <div className="gn__badge">SIMULATED spread · placeholder, not observed data</div> : null}
            <div className="gn__legend2">
              <span><i style={{ background: '#a01a20' }} />Burned / detected</span>
              <span><i style={{ background: '#ff8c1e' }} />High likelihood prediction (50%–99%)</span>
              <span><i style={{ background: '#ffd05a' }} />Low likelihood prediction (5%–49%)</span>
            </div>
            <div className="gn__time-row"><b>{fmtPT(simMs)}</b><span>{viirs.filter((d) => d.t <= simMs).length} detections</span></div>
            <input type="range" min={SIM_START} max={SIM_END} step={HOUR} value={simMs} onChange={(e) => { setPlaying(false); setSimMs(+e.target.value) }} />
          </div>
        </div>
      ) : null}
      {pick ? (
        <aside className="gn__side">
          <button className="gn__close" onClick={() => setPick(null)} aria-label="Close">×</button>
          <h2>{pick.title}</h2>
          <button className="gn__fit" onClick={() => fitRef.current?.(pick)}>Fit to fire</button>
          <dl>
            <dt>Type</dt><dd>{pick.kind}</dd>
            <dt>Started</dt><dd>{fmtDate(pick.started)}</dd>
            <dt>Risk level</dt><dd>{riskOf(pick)}</dd>
            <dt>Links</dt>
            <dd>{pick.link ? <a href={pick.link} target="_blank" rel="noreferrer">{pick.source} event record</a> : '—'}</dd>
          </dl>
        </aside>
      ) : null}
    </div>
  )
}
