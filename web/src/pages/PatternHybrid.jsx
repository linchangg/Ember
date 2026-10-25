import { useEffect, useRef, useState } from 'react'
import maplibregl from '../lib/maplibre'
import PrototypeChrome from '../components/PrototypeChrome'
import TimeSlider from '../components/TimeSlider'
import Sidebar from '../components/Sidebar'
import { INCIDENT, TERRARIUM_DEM } from '../lib/incident'

const INK = '#1f2328'
const RED = '#C8261B'

/** Fire radius (m) at slider time t: ignition at -360, forecast radii at 0/120/240/360. */
function radiusAt(t) {
  const knots = [
    [-360, 250],
    [0, INCIDENT.forecastRadiiM[0]],
    [120, INCIDENT.forecastRadiiM[1]],
    [240, INCIDENT.forecastRadiiM[2]],
    [360, INCIDENT.forecastRadiiM[3]],
  ]
  for (let i = 1; i < knots.length; i++) {
    const [t1, r1] = knots[i]
    if (t <= t1) {
      const [t0, r0] = knots[i - 1]
      return r0 + ((r1 - r0) * (t - t0)) / (t1 - t0)
    }
  }
  return knots[knots.length - 1][1]
}

/** Closed lon/lat ring approximating a circle of radius `r` meters. */
function circle(lon, lat, r, steps = 72) {
  const dLat = r / 111320
  const dLon = r / (111320 * Math.cos((lat * Math.PI) / 180))
  const ring = []
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI
    ring.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)])
  }
  return ring
}

const poly = (r, props = {}) => ({
  type: 'Feature',
  properties: props,
  geometry: { type: 'Polygon', coordinates: [circle(INCIDENT.lon, INCIDENT.lat, r)] },
})

const fireData = (t) => ({ type: 'FeatureCollection', features: [poly(radiusAt(t))] })
const forecastData = {
  type: 'FeatureCollection',
  features: INCIDENT.forecastRadiiM.slice(1).map((r) => poly(r)),
}

export default function PatternHybrid() {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const tRef = useRef(0)
  const [t, setT] = useState(0)
  const [zoom, setZoom] = useState(2.4)

  useEffect(() => {
    tRef.current = t
    mapRef.current?.getSource?.('fire')?.setData(fireData(t))
  }, [t])

  useEffect(() => {
    if (!containerRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          terrain: TERRARIUM_DEM,
          shade: TERRARIUM_DEM,
          fire: { type: 'geojson', data: fireData(tRef.current) },
          forecast: { type: 'geojson', data: forecastData },
        },
        layers: [
          { id: 'paper', type: 'background', paint: { 'background-color': '#fbfbfa' } },
          {
            id: 'shade',
            type: 'hillshade',
            source: 'shade',
            paint: {
              'hillshade-shadow-color': '#2f333a',
              'hillshade-highlight-color': '#ffffff',
              'hillshade-accent-color': '#6b7078',
              'hillshade-exaggeration': 1,
            },
          },
          {
            id: 'forecast-line',
            type: 'line',
            source: 'forecast',
            paint: { 'line-color': RED, 'line-width': 1, 'line-opacity': 0.45, 'line-dasharray': [2, 2] },
          },
          // Translucent so the gray hillshade shows through: the burn follows the terrain.
          { id: 'fire-fill', type: 'fill', source: 'fire', paint: { 'fill-color': RED, 'fill-opacity': 0.5 } },
          { id: 'fire-line', type: 'line', source: 'fire', paint: { 'line-color': RED, 'line-width': 2 } },
        ],
        terrain: { source: 'terrain', exaggeration: 1.4 },
        sky: {
          'sky-color': '#ffffff',
          'horizon-color': '#e9eaec',
          'fog-color': '#ffffff',
          'sky-horizon-blend': 0.6,
          'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 12, 0.2],
        },
      },
      center: [INCIDENT.lon, INCIDENT.lat],
      zoom: 2.4,
      pitch: 0,
      maxPitch: 80,
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')

    map.on('load', () => {
      try {
        map.setProjection?.({ type: 'globe' })
      } catch {
        /* ignore */
      }
      map.addSource('incident', {
        type: 'geojson',
        data: { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [INCIDENT.lon, INCIDENT.lat] } },
      })
      map.addLayer({
        id: 'incident-core',
        type: 'circle',
        source: 'incident',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 5, 9, 3],
          'circle-opacity': ['interpolate', ['linear'], ['zoom'], 8, 1, 11, 0],
          'circle-stroke-opacity': ['interpolate', ['linear'], ['zoom'], 8, 1, 11, 0],
          'circle-color': RED,
          'circle-stroke-width': 2,
          'circle-stroke-color': '#fff',
        },
      })
      map.on('click', 'incident-core', () => {
        map.flyTo({ center: [INCIDENT.lon, INCIDENT.lat], zoom: 11, pitch: 55, duration: 2500 })
      })
      map.on('mouseenter', 'incident-core', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'incident-core', () => (map.getCanvas().style.cursor = ''))
    })

    map.on('zoom', () => setZoom(map.getZoom()))

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  return (
    <PrototypeChrome
      pattern="Pattern 3"
      title="Hybrid · monochrome globe"
      blurb="One continuous globe: white terrain, gray relief shadows, red burn area that grows with the forecast. Click the red dot to fly in; zoom out any time."
      footer={
        <>
          <button
            type="button"
            className="proto__btn"
            onClick={() => mapRef.current?.flyTo({ center: [INCIDENT.lon, INCIDENT.lat], zoom: 11, pitch: 55, duration: 2500 })}
          >
            Fly to Sierra Vista →
          </button>
          <button
            type="button"
            className="proto__btn"
            onClick={() => mapRef.current?.flyTo({ zoom: 2.4, pitch: 0, bearing: 0, duration: 2000 })}
          >
            ← Whole globe
          </button>
          <span className="proto__status">
            Terrarium DEM · synthetic fire footprint · zoom {zoom.toFixed(1)}
          </span>
        </>
      }
    >
      <div className="globe-stage" style={{ color: INK }}>
        <div ref={containerRef} className="proto__canvas" />
        <div className="globe-overlay globe-overlay--sidebar">
          <Sidebar t={t} />
        </div>
        <div className="globe-overlay globe-overlay--slider">
          <TimeSlider t={t} onChange={setT} />
        </div>
      </div>
    </PrototypeChrome>
  )
}
