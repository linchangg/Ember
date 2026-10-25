import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from '../lib/maplibre.js'
import type { GeoJSONSource, ImageSource, Map as LibreMap } from 'maplibre-gl'
import type { FeatureCollection } from 'geojson'
import type { Cycle } from '../contracts.ts'
import type { MapAssets, ScenarioManifest } from '../data/source.ts'
import type { RasterMode } from './forecastRaster.ts'
import type { Coordinates } from './reprojection.ts'
import { vectorBounds } from './mapAdapter.ts'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
const LAYERS = [
  'forecast-fill',
  'burned-fill',
  'published-front',
  'q50-isochrones',
]
interface Frame {
  key: string
  rgba: Uint8ClampedArray
  width: number
  height: number
  coordinates: Coordinates
  reached: number
  burned: number
  error?: string
}
export default function ForecastMap({
  manifest,
  cycle,
  assets,
  viewTime,
  mode,
  available,
}: {
  manifest: ScenarioManifest
  cycle: Cycle | null
  assets: MapAssets | null
  viewTime: number
  mode: RasterMode
  available: boolean
}) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<LibreMap | null>(null),
    worker = useRef<Worker | null>(null)
  const generation = useRef(0),
    requestKey = useRef(''),
    fitted = useRef(false)
  const [ready, setReady] = useState(false),
    [frame, setFrame] = useState<Frame | null>(null)
  const [mapError, setMapError] = useState<string | null>(null),
    [rendered, setRendered] = useState('')
  // Two-minute encoded arrival bins determine the mask; never rebuild on every playback frame.
  const leadBin = cycle
    ? Math.floor((viewTime - cycle.knowledge_sim_ms) / 120000)
    : 0
  const key = useMemo(
    () =>
      JSON.stringify([
        cycle?.cycle_id,
        cycle?.model_version,
        cycle?.config_hash,
        leadBin,
        mode,
        available,
      ]),
    [cycle, leadBin, mode, available]
  )
  useEffect(() => {
    let alive = true
    fitted.current = false
    const rasterWorker = new Worker(
      new URL('./raster.worker.ts', import.meta.url),
      { type: 'module' }
    )
    worker.current = rasterWorker
    rasterWorker.onmessage = (event: MessageEvent<Frame & { id: number }>) => {
      if (!alive || event.data.id !== generation.current) return
      setFrame({ ...event.data, key: requestKey.current })
    }
    rasterWorker.onerror = () => {
      if (alive)
        setFrame({
          key: requestKey.current,
          error: 'Forecast image worker failed',
        } as Frame)
    }
    let instance: LibreMap | undefined
    try {
      instance = new maplibregl.Map({
        container: host.current!,
        center: [
          manifest.initial_view.longitude,
          manifest.initial_view.latitude,
        ],
        zoom: manifest.initial_view.zoom,
        pitch: 0,
        maxPitch: 60,
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            'published-perimeter': { type: 'geojson', data: EMPTY },
            'published-isochrones': { type: 'geojson', data: EMPTY },
            'grid-domain': {
              type: 'geojson',
              data: {
                type: 'Feature',
                properties: {},
                geometry: {
                  type: 'Polygon',
                  coordinates: [
                    [
                      manifest.grid.corners.tl,
                      manifest.grid.corners.tr,
                      manifest.grid.corners.br,
                      manifest.grid.corners.bl,
                      manifest.grid.corners.tl,
                    ],
                  ],
                },
              },
            },
          },
          layers: [
            {
              id: 'paper',
              type: 'background',
              paint: { 'background-color': '#fafaf8' },
            },
            {
              id: 'grid-edge',
              type: 'line',
              source: 'grid-domain',
              paint: { 'line-color': '#bfc0bb', 'line-width': 1 },
            },
            {
              id: 'burned-fill',
              type: 'fill',
              source: 'published-perimeter',
              filter: ['==', ['id'], 'burned'],
              paint: { 'fill-color': '#4b3732', 'fill-opacity': 0.3 },
            },
            {
              id: 'published-front',
              type: 'line',
              source: 'published-perimeter',
              filter: ['==', ['id'], 'front'],
              paint: { 'line-color': '#9a3412', 'line-width': 2 },
            },
            {
              id: 'q50-isochrones',
              type: 'line',
              source: 'published-isochrones',
              paint: {
                'line-color': '#9a3412',
                'line-width': 1,
                'line-dasharray': [3, 3],
                'line-opacity': 0.65,
              },
            },
          ],
        },
      })
      const current = instance
      map.current = current
      current.addControl(new maplibregl.NavigationControl(), 'top-right')
      current.addControl(new maplibregl.ScaleControl({ unit: 'metric' }))
      current.on('error', (event) => {
        if (alive)
          setMapError(event.error?.message ?? 'Forecast map unavailable')
      })
      current.on('load', () => {
        if (!alive) return
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = 1
        const corners = manifest.grid.corners
        current.addSource('forecast-image', {
          type: 'image',
          url: canvas.toDataURL(),
          coordinates: [corners.tl, corners.tr, corners.br, corners.bl],
        })
        current.addLayer(
          {
            id: 'forecast-fill',
            type: 'raster',
            source: 'forecast-image',
            paint: {
              'raster-opacity': 1,
              'raster-fade-duration': 0,
              'raster-resampling': 'nearest',
            },
          },
          'burned-fill'
        )
        const points = Object.values(corners)
        current.fitBounds(
          [
            [
              Math.min(...points.map((p) => p[0])),
              Math.min(...points.map((p) => p[1])),
            ],
            [
              Math.max(...points.map((p) => p[0])),
              Math.max(...points.map((p) => p[1])),
            ],
          ],
          { padding: 35, duration: 0 }
        )
        setReady(true)
      })
      const observer = new ResizeObserver(() => current.resize())
      observer.observe(host.current!)
      return () => {
        alive = false
        observer.disconnect()
        rasterWorker.terminate()
        worker.current = null
        map.current = null
        current.remove()
      }
    } catch (error) {
      queueMicrotask(() => {
        if (alive)
          setMapError(error instanceof Error ? error.message : String(error))
      })
      return () => {
        alive = false
        rasterWorker.terminate()
        worker.current = null
        instance?.remove()
        map.current = null
      }
    }
  }, [manifest])
  useEffect(() => {
    const id = ++generation.current
    requestKey.current = key
    if (!available || !cycle || !assets || !worker.current) return
    const bytes = assets.arrival.slice() // Transfer a copy: source/cache buffers must stay attached.
    worker.current.postMessage(
      {
        id,
        grid: manifest.grid,
        bytes,
        issueMs: cycle.knowledge_sim_ms,
        viewMs: cycle.knowledge_sim_ms + Math.max(0, leadBin) * 120000,
        horizonMin: cycle.horizon_min,
        mode,
      },
      [bytes.buffer]
    )
  }, [manifest, key, cycle, assets, leadBin, mode, available])
  useEffect(() => {
    const current = map.current
    if (!ready || !current) return
    let alive = true
    const valid =
      available && cycle && assets && frame?.key === key && !frame.error
    for (const id of LAYERS)
      current.setLayoutProperty(id, 'visibility', valid ? 'visible' : 'none')
    if (!valid) {
      ;(current.getSource('published-perimeter') as GeoJSONSource).setData(
        EMPTY
      )
      ;(current.getSource('published-isochrones') as GeoJSONSource).setData(
        EMPTY
      )
      return () => {
        alive = false
      }
    }
    ;(current.getSource('forecast-image') as ImageSource).updateImage({
      image: new ImageData(frame.rgba, frame.width, frame.height),
      coordinates: frame.coordinates,
    })
    ;(current.getSource('published-perimeter') as GeoJSONSource).setData(
      assets.perimeter as unknown as FeatureCollection
    )
    ;(current.getSource('published-isochrones') as GeoJSONSource).setData(
      assets.isochrones as unknown as FeatureCollection
    )
    current.setFilter('q50-isochrones', [
      'all',
      ['==', ['get', 'q'], 0.5],
      ['<=', ['get', 'lead_min'], leadBin * 2],
    ])
    if (!fitted.current) {
      const bounds =
        vectorBounds(assets.isochrones) ?? vectorBounds(assets.perimeter)
      if (bounds)
        current.fitBounds(bounds, { padding: 55, duration: 0, maxZoom: 14 })
      fitted.current = true
    }
    const complete = () => {
      if (alive) setRendered(key)
    }
    current.once('idle', complete)
    return () => {
      alive = false
      current.off('idle', complete)
    }
  }, [ready, available, cycle, assets, frame, key, leadBin])
  const failure = mapError ?? (frame?.key === key ? frame.error : null)
  const busy =
    available && !failure && (!ready || frame?.key !== key || rendered !== key)
  return (
    <div
      className="terrain-wrap forecast-map"
      data-testid="forecast-map"
      data-cycle-id={available ? cycle?.cycle_id : ''}
      data-rendered={
        available && rendered === key && !busy && !failure ? 'ready' : 'empty'
      }
      data-reached-cells={frame?.key === key ? frame.reached : 0}
      data-burned-cells={frame?.key === key ? frame.burned : 0}
    >
      <div
        ref={host}
        className="forecast-map__surface"
        role="region"
        aria-label="Georeferenced Rust forecast map with modeled perimeter and q50 isochrones"
      />
      <button
        className="forecast-map__fit"
        disabled={!available || !ready}
        onClick={() => {
          const bounds =
            assets &&
            (vectorBounds(assets.isochrones) ?? vectorBounds(assets.perimeter))
          if (bounds)
            map.current?.fitBounds(bounds, {
              padding: 55,
              duration: 0,
              maxZoom: 14,
            })
        }}
      >
        Fit forecast
      </button>
      <div className="forecast-map__caption">
        Flat map · north-first forecast grid · drag to pan
      </div>
      {failure && (
        <div className="forecast-map__notice" role="alert">
          Map unavailable: {failure}
        </div>
      )}
      {busy && (
        <div className="forecast-map__notice" role="status">
          Preparing forecast map…
        </div>
      )}
      {!available && !failure && (
        <div className="forecast-map__notice" role="status">
          Forecast layer unavailable at this time
        </div>
      )}
    </div>
  )
}
