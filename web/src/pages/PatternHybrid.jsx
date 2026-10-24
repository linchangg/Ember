import { useEffect, useRef, useState } from 'react'
import maplibregl from '../lib/maplibre'
import PrototypeChrome from '../components/PrototypeChrome'
import TerrainMap from '../components/TerrainMap'
import TimeSlider from '../components/TimeSlider'
import Sidebar from '../components/Sidebar'
import { ESRI_IMAGERY, ESRI_TOPO, INCIDENT, TERRARIUM_DEM } from '../lib/incident'

const HANDOFF_ZOOM = 10.5

export default function PatternHybrid() {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const [mode, setMode] = useState('world') // 'world' | 'incident'
  const [zoom, setZoom] = useState(2)
  const [t, setT] = useState(150)
  const [status, setStatus] = useState('World overview — zoom toward Sierra Vista or enter incident')

  useEffect(() => {
    if (mode !== 'world' || !containerRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          imagery: ESRI_IMAGERY,
          topo: ESRI_TOPO,
          terrain: TERRARIUM_DEM,
        },
        layers: [
          { id: 'imagery', type: 'raster', source: 'imagery' },
          {
            id: 'topo',
            type: 'raster',
            source: 'topo',
            paint: { 'raster-opacity': 0.35 },
          },
        ],
        terrain: { source: 'terrain', exaggeration: 1.4 },
        sky: {
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
        if (typeof map.setProjection === 'function') {
          map.setProjection({ type: 'globe' })
        }
      } catch {
        /* ignore */
      }

      // Incident halo on the real globe
      map.addSource('incident', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              properties: { name: INCIDENT.name },
              geometry: {
                type: 'Point',
                coordinates: [INCIDENT.lon, INCIDENT.lat],
              },
            },
          ],
        },
      })
      map.addLayer({
        id: 'incident-glow',
        type: 'circle',
        source: 'incident',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 6, 10, 28],
          'circle-color': '#E8590C',
          'circle-opacity': 0.55,
          'circle-blur': 0.6,
        },
      })
      map.addLayer({
        id: 'incident-core',
        type: 'circle',
        source: 'incident',
        paint: {
          'circle-radius': 5,
          'circle-color': '#B42318',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#fff',
        },
      })

      const popup = new maplibregl.Popup({ offset: 16, closeButton: false }).setHTML(
        `<strong>${INCIDENT.name}</strong><br/><button type="button" id="enter-incident" style="margin-top:6px;cursor:pointer;border:none;background:#C2410C;color:#fff;padding:6px 10px;border-radius:6px;font:600 12px Inter,system-ui,sans-serif">Enter incident view</button>`,
      )

      map.on('click', 'incident-core', (e) => {
        popup.setLngLat(e.lngLat).addTo(map)
        requestAnimationFrame(() => {
          document.getElementById('enter-incident')?.addEventListener(
            'click',
            () => setMode('incident'),
            { once: true },
          )
        })
      })
      map.on('mouseenter', 'incident-core', () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', 'incident-core', () => {
        map.getCanvas().style.cursor = ''
      })

      setStatus('AWS Terrarium · Esri imagery/topo — zoom in or click the fire')
    })

    const onZoom = () => {
      const z = map.getZoom()
      setZoom(z)
      if (z >= HANDOFF_ZOOM) {
        setStatus('Handoff threshold reached — switching to incident scene…')
        setMode('incident')
      }
    }
    map.on('zoom', onZoom)
    map.on('zoomend', onZoom)

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [mode])

  if (mode === 'incident') {
    return (
      <PrototypeChrome
        pattern="Pattern 3"
        title="Hybrid · incident scene"
        blurb="Handed off from the real-topo globe into the existing Sierra Vista command canvas."
        footer={
          <>
            <button type="button" className="proto__btn" onClick={() => setMode('world')}>
              ← Back to world globe
            </button>
            <span className="proto__status">Synthetic incident terrain (mockup scene)</span>
          </>
        }
      >
        <div className="hybrid-incident">
          <div className="hybrid-incident__main">
            <TerrainMap t={t} />
            <TimeSlider t={t} onChange={setT} />
          </div>
          <Sidebar t={t} />
        </div>
      </PrototypeChrome>
    )
  }

  return (
    <PrototypeChrome
      pattern="Pattern 3"
      title="Hybrid · world globe"
      blurb={`Real Earth overview. Zoom past ${HANDOFF_ZOOM} or click the fire marker to enter the regional command scene.`}
      footer={
        <>
          <button type="button" className="proto__btn" onClick={() => setMode('incident')}>
            Enter Sierra Vista incident →
          </button>
          <span className="proto__status">
            {status} · zoom {zoom.toFixed(1)}
          </span>
        </>
      }
    >
      <div ref={containerRef} className="proto__canvas" />
    </PrototypeChrome>
  )
}
