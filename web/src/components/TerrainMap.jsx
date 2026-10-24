import { useEffect, useRef } from 'react'
import { drawTerrain } from '../lib/drawTerrain'
import { clock, relativeLabel } from '../lib/fireModel'

export default function TerrainMap({ t }) {
  const wrapRef = useRef(null)
  const canvasRef = useRef(null)
  const viewRef = useRef({ yaw: -0.6, pitch: 0.78 })
  const dragRef = useRef(null)
  const sizeRef = useRef({ cw: 0, ch: 0, dpr: 1 })

  const paint = () => {
    const canvas = canvasRef.current
    const { cw, ch, dpr } = sizeRef.current
    if (!canvas || !cw) return
    const ctx = canvas.getContext('2d')
    const { yaw, pitch } = viewRef.current
    drawTerrain(ctx, { cw, ch, dpr, t, yaw, pitch })
  }

  const resize = () => {
    const wrap = wrapRef.current
    const canvas = canvasRef.current
    if (!wrap || !canvas || !wrap.clientWidth) return
    const cw = wrap.clientWidth
    const ch = wrap.clientHeight
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    sizeRef.current = { cw, ch, dpr }
    canvas.width = Math.round(cw * dpr)
    canvas.height = Math.round(ch * dpr)
    paint()
  }

  useEffect(() => {
    resize()
    const wrap = wrapRef.current
    if (!wrap || !window.ResizeObserver) return
    const ro = new ResizeObserver(() => resize())
    ro.observe(wrap)
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    paint()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const onDown = (e) => {
      dragRef.current = { x: e.clientX, y: e.clientY }
      canvas.setPointerCapture(e.pointerId)
      canvas.style.cursor = 'grabbing'
    }
    const onMove = (e) => {
      if (!dragRef.current) return
      viewRef.current.yaw += (e.clientX - dragRef.current.x) * 0.008
      viewRef.current.pitch = Math.max(
        0.35,
        Math.min(1.45, viewRef.current.pitch + (e.clientY - dragRef.current.y) * 0.005),
      )
      dragRef.current = { x: e.clientX, y: e.clientY }
      paint()
    }
    const onEnd = () => {
      dragRef.current = null
      canvas.style.cursor = 'grab'
    }

    canvas.addEventListener('pointerdown', onDown)
    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerup', onEnd)
    canvas.addEventListener('pointercancel', onEnd)
    return () => {
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerup', onEnd)
      canvas.removeEventListener('pointercancel', onEnd)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t])

  const modeLabel =
    t === 0
      ? 'Now'
      : t < 0
        ? 'Observed · ' + relativeLabel(t) + ' ago'
        : 'Forecast · ' + relativeLabel(t) + ' ahead'
  const modeColor = t > 0 ? '#E8590C' : '#6B6B6B'

  return (
    <div ref={wrapRef} className="terrain-wrap">
      <canvas
        ref={canvasRef}
        role="img"
        aria-label="Rotatable 3D mountain terrain showing the fire's burned area and forecast spread probability"
        className="terrain-canvas"
      />
      <div className="terrain-clock">
        <div className="terrain-clock__time">{clock(t)}</div>
        <div className="terrain-clock__mode" style={{ color: modeColor }}>
          {modeLabel}
        </div>
      </div>
      <div className="terrain-hint">Drag to rotate</div>
    </div>
  )
}
