import { fireModel, probAt } from './fireModel'

/**
 * Render the rotatable 3D mountain terrain with burned area + forecast probability.
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ cw: number, ch: number, dpr: number, t: number, yaw: number, pitch: number }} opts
 */
export function drawTerrain(ctx, { cw: W, ch: H, dpr, t: T, yaw, pitch }) {
  const { N, height: h, analysis } = fireModel

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(0, 0, W, H)

  const cy = Math.cos(yaw)
  const sy = Math.sin(yaw)
  const cp = Math.cos(pitch)
  const sp = Math.sin(pitch)
  const HS = 22
  const D = 160
  const k = Math.min(W, H * 1.3) / 68
  const ox = W / 2
  const oy = H * 0.58

  const proj = (X, Y, Z) => {
    const x1 = X * cy - Y * sy
    const y1 = X * sy + Y * cy
    const depth = y1 * cp - Z * sp
    const f = D / (D + depth)
    return [ox + x1 * f * k, oy - (Z * cp + y1 * sp) * f * k, depth]
  }

  const P = new Array(N * N)
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      P[i * N + j] = proj(j - (N - 1) / 2, (N - 1) / 2 - i, h[i * N + j] * HS)
    }
  }

  const quads = []
  for (let i = 0; i < N - 1; i++) {
    for (let j = 0; j < N - 1; j++) {
      const a = i * N + j
      quads.push([a, (P[a][2] + P[a + 1][2] + P[a + N][2] + P[a + N + 1][2]) / 4])
    }
  }
  quads.sort((p, q) => q[1] - p[1])

  const tObs = Math.min(T, 0)
  for (const [a] of quads) {
    const dzx = (h[a + 1] - h[a]) * HS
    const dzy = (h[a + N] - h[a]) * HS
    const shade = Math.max(0.55, Math.min(1.08, 0.9 + 0.22 * (dzx + dzy)))
    const base = 238 - 18 * (1 - h[a])
    let col = [base * shade, base * shade, base * shade * 0.985]
    const an = analysis[a]
    let mix = null
    let al = 0
    if (an <= tObs) {
      if (tObs - an < 30) {
        mix = [255, 106, 26]
        al = 1
      } else {
        mix = [122, 52, 40]
        al = 0.85
      }
    } else if (T > 0) {
      const p = probAt(a, T)
      if (p > 0.05) {
        mix =
          p >= 0.67
            ? [217, 58, 43]
            : p >= 0.34
              ? [240, 122, 58]
              : [249, 185, 138]
        al = 0.35 + 0.5 * p
      }
    }
    if (mix) col = col.map((v, n) => v * (1 - al) + mix[n] * al * (al === 1 ? 1 : shade))
    const fs = 'rgb(' + (col[0] | 0) + ',' + (col[1] | 0) + ',' + (col[2] | 0) + ')'
    const p0 = P[a]
    const p1 = P[a + 1]
    const p2 = P[a + N + 1]
    const p3 = P[a + N]
    ctx.beginPath()
    ctx.moveTo(p0[0], p0[1])
    ctx.lineTo(p1[0], p1[1])
    ctx.lineTo(p2[0], p2[1])
    ctx.lineTo(p3[0], p3[1])
    ctx.closePath()
    ctx.fillStyle = fs
    ctx.fill()
    ctx.strokeStyle = fs
    ctx.lineWidth = 0.6
    ctx.stroke()
  }

  // Fire centroid for satellite beams
  let fx = 0
  let fy = 0
  let fn = 0
  for (let a = 0; a < N * N; a++) {
    const an = analysis[a]
    if (an <= tObs && an > tObs - 40) {
      fx += P[a][0]
      fy += P[a][1]
      fn++
    }
  }
  if (fn) {
    fx /= fn
    fy /= fn
  }

  ctx.font = '500 13px Inter, system-ui, sans-serif'
  const sats = [
    [W * 0.18, 44, 'GOES-18'],
    [W * 0.55, 30, 'VIIRS'],
    [W * 0.84, 52, 'FireSat'],
  ]
  for (const [sx, syy, name] of sats) {
    if (fn) {
      ctx.setLineDash([4, 5])
      ctx.strokeStyle = 'rgba(232,89,12,0.45)'
      ctx.lineWidth = 1.2
      ctx.beginPath()
      ctx.moveTo(sx, syy + 8)
      ctx.lineTo(fx, fy)
      ctx.stroke()
      ctx.setLineDash([])
    }
    ctx.strokeStyle = '#8A8A8A'
    ctx.lineWidth = 1.4
    ctx.fillStyle = '#FFFFFF'
    ctx.fillRect(sx - 6, syy - 6, 12, 12)
    ctx.strokeRect(sx - 6, syy - 6, 12, 12)
    ctx.strokeRect(sx - 22, syy - 3, 12, 6)
    ctx.strokeRect(sx + 10, syy - 3, 12, 6)
    ctx.beginPath()
    ctx.moveTo(sx - 10, syy)
    ctx.lineTo(sx - 6, syy)
    ctx.moveTo(sx + 6, syy)
    ctx.lineTo(sx + 10, syy)
    ctx.stroke()
    ctx.fillStyle = '#6B6B6B'
    ctx.fillText(name, sx + 28, syy + 5)
  }

  // Town markers
  for (const tw of fireModel.towns) {
    const a = tw.i * N + tw.j
    const p = P[a]
    const top = proj(tw.j - (N - 1) / 2, (N - 1) / 2 - tw.i, h[a] * HS + 5)
    ctx.strokeStyle = '#555555'
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(p[0], p[1])
    ctx.lineTo(top[0], top[1])
    ctx.stroke()
    ctx.fillStyle = '#333333'
    ctx.beginPath()
    ctx.arc(top[0], top[1], 4.5, 0, Math.PI * 2)
    ctx.fill()
    ctx.font = '600 13px Inter, system-ui, sans-serif'
    const tw2 = ctx.measureText(tw.name).width
    ctx.fillStyle = 'rgba(255,255,255,0.9)'
    ctx.fillRect(top[0] + 8, top[1] - 11, tw2 + 10, 20)
    ctx.fillStyle = '#333333'
    ctx.fillText(tw.name, top[0] + 13, top[1] + 4)
  }
}
