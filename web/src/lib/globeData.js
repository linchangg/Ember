/** Decorative flights require two valid events; an offline/empty feed is normal. */
export function newHeli(events, random = Math.random) {
  if (events.length < 2) return null
  for (let tries = 0; tries < 40; tries++) {
    const a = events[Math.floor(random() * events.length)]
    const b = events[Math.floor(random() * events.length)]
    const dot = a.v[0] * b.v[0] + a.v[1] * b.v[1] + a.v[2] * b.v[2]
    const ang = Math.acos(Math.max(-1, Math.min(1, dot)))
    if (ang > 0.06 && ang < 0.9) return { a: a.v, b: b.v, ang, t: 0, speed: 0.02 + random() * 0.02, hue: random() }
  }
  return null
}
