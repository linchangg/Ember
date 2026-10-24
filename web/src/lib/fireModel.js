/** Synthetic terrain + ensemble fire-spread model (mock scenario). */

const N = 60

function gaussian(x, y, cx, cy, s) {
  return Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / s)
}

function buildHeightmap() {
  const h = new Float32Array(N * N)
  let mn = 1e9
  let mx = -1e9
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const x = j / N
      const y = i / N
      const v =
        1.0 * gaussian(x, y, 0.72, 0.22, 0.012) +
        0.85 * gaussian(x, y, 0.22, 0.18, 0.016) +
        0.7 * gaussian(x, y, 0.82, 0.72, 0.014) +
        0.55 * gaussian(x, y, 0.5, 0.48, 0.02) +
        0.45 * gaussian(x, y, 0.15, 0.7, 0.018) +
        0.06 * Math.sin(x * 31 + y * 17) +
        0.05 * Math.cos(x * 23 - y * 29)
      h[i * N + j] = v
      mn = Math.min(mn, v)
      mx = Math.max(mx, v)
    }
  }
  for (let k = 0; k < N * N; k++) h[k] = (h[k] - mn) / (mx - mn)
  return h
}

function buildEnsemble(h) {
  const i0 = 47
  const j0 = 13
  const h0 = h[i0 * N + j0]
  const offs = [0, -0.35, 0.3, -0.15, 0.45, -0.5, 0.15, 0.35, -0.25, 0.05]
  const sp = [1, 0.8, 1.25, 1.1, 0.75, 1.4, 0.9, 1.15, 0.95, 1.3]

  return offs.map((o, m) => {
    const th = -Math.PI / 4 + o
    const wx = Math.cos(th)
    const wy = Math.sin(th)
    const a = new Float32Array(N * N)
    for (let i = 0; i < N; i++) {
      for (let j = 0; j < N; j++) {
        const dx = j - j0
        const dy = i - i0
        const u = dx * wx + dy * wy
        const v = -dx * wy + dy * wx
        const ue = u > 0 ? u / (1.8 * sp[m]) : -u * 1.6
        const eff = Math.sqrt(ue * ue + v * v * 0.9)
        a[i * N + j] = eff * 22 * Math.exp(-0.8 * (h[i * N + j] - h0)) - 360
      }
    }
    return a
  })
}

const height = buildHeightmap()
const members = buildEnsemble(height)
const analysis = members[0]

export const TOWNS = [
  { name: 'Pine Hollow', i: 22, j: 40 },
  { name: 'Cedar Flat', i: 36, j: 52 },
  { name: 'Owl Creek', i: 13, j: 26 },
]

export const fireModel = {
  N,
  height,
  members,
  analysis,
  towns: TOWNS,
}

export function probAt(k, T) {
  if (analysis[k] <= Math.min(T, 0)) return 1
  if (T <= 0) return 0
  let c = 0
  for (const m of members) if (m[k] <= T) c++
  return c / members.length
}

export function clock(m) {
  const x = ((14 * 60 + 5 + m) % 1440 + 1440) % 1440
  return (
    String(Math.floor(x / 60)).padStart(2, '0') +
    ':' +
    String(Math.round(x % 60)).padStart(2, '0')
  )
}

export function relativeLabel(m) {
  const a = Math.abs(m)
  const hh = Math.floor(a / 60)
  const mm = a % 60
  return (hh ? hh + 'h ' : '') + (mm ? mm + 'm' : '') || '0m'
}

export function burnedAcres(T) {
  let cells = 0
  const tObs = Math.min(T, 0)
  for (let a = 0; a < N * N; a++) if (analysis[a] <= tObs) cells++
  return (cells * 9.9).toLocaleString('en-US', { maximumFractionDigits: 0 })
}

export function townRisks(T) {
  return TOWNS.map((tw) => {
    const p = probAt(tw.i * N + tw.j, T)
    return {
      name: tw.name,
      pct: Math.round(p * 100) + '%',
      color:
        p >= 0.67
          ? '#B42318'
          : p >= 0.34
            ? '#E8590C'
            : p > 0
              ? '#F08C4A'
              : '#9A9A9A',
    }
  })
}
