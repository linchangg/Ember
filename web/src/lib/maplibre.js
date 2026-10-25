import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'

try {
  maplibregl.setWorkerUrl(workerUrl)
} catch (e) {
  console.warn('maplibre setWorkerUrl failed', e)
}

export default maplibregl
export { maplibregl }
