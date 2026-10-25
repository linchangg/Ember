import { useEffect, useRef, useState } from 'react'

/** Lightweight FPS readout so patterns can be compared for smoothness. */
export default function FpsMeter() {
  const [fps, setFps] = useState('—')
  const frames = useRef(0)
  const last = useRef(performance.now())

  useEffect(() => {
    let id
    let alive = true
    const tick = (now) => {
      if (!alive) return
      frames.current++
      const elapsed = now - last.current
      if (elapsed >= 750) {
        setFps(String(Math.round((frames.current * 1000) / elapsed)))
        frames.current = 0
        last.current = now
      }
      id = requestAnimationFrame(tick)
    }
    id = requestAnimationFrame(tick)
    return () => {
      alive = false
      cancelAnimationFrame(id)
    }
  }, [])

  return <div className="fps-meter">{fps} fps</div>
}
