import { useEffect, useRef, useState } from 'react'
import maplibregl from '../lib/maplibre'
import * as THREE from 'three'
import PrototypeChrome from '../components/PrototypeChrome'
import { ESRI_IMAGERY, INCIDENT, TERRARIUM_DEM } from '../lib/incident'

/**
 * MapLibre + terrain, with a Three.js custom layer for fire viz.
 */
function createFireLayer(map) {
  const fireLon = INCIDENT.lon
  const fireLat = INCIDENT.lat

  let camera
  let scene
  let renderer
  let fireGroup
  let anim = 0

  return {
    id: 'ember-three-fire',
    type: 'custom',
    renderingMode: '3d',

    onAdd(_map, gl) {
      camera = new THREE.Camera()
      scene = new THREE.Scene()

      const light = new THREE.DirectionalLight(0xffffff, 1.1)
      light.position.set(0, -70, 100).normalize()
      scene.add(light)
      scene.add(new THREE.AmbientLight(0xffffff, 0.35))

      fireGroup = new THREE.Group()

      const burnGeo = new THREE.CircleGeometry(1, 48)
      const burnMat = new THREE.MeshBasicMaterial({
        color: 0x7a3428,
        transparent: true,
        opacity: 0.8,
        side: THREE.DoubleSide,
        depthTest: false,
        depthWrite: false,
      })
      const burn = new THREE.Mesh(burnGeo, burnMat)
      burn.scale.set(INCIDENT.burnRadiusM, INCIDENT.burnRadiusM * 0.72, 1)
      burn.rotation.x = -Math.PI / 2
      fireGroup.add(burn)

      const ringGeo = new THREE.RingGeometry(0.92, 1.05, 64)
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0xff6a1a,
        transparent: true,
        opacity: 0.95,
        side: THREE.DoubleSide,
        depthTest: false,
        depthWrite: false,
      })
      const ring = new THREE.Mesh(ringGeo, ringMat)
      ring.scale.set(INCIDENT.burnRadiusM, INCIDENT.burnRadiusM * 0.72, 1)
      ring.rotation.x = -Math.PI / 2
      ring.position.y = 80
      fireGroup.add(ring)

      const forecastGeo = new THREE.CircleGeometry(1, 48)
      const forecastMat = new THREE.MeshBasicMaterial({
        color: 0xd93a2b,
        transparent: true,
        opacity: 0.32,
        side: THREE.DoubleSide,
        depthTest: false,
        depthWrite: false,
      })
      const forecast = new THREE.Mesh(forecastGeo, forecastMat)
      forecast.name = 'forecast'
      forecast.scale.set(INCIDENT.forecastRadiiM[2], INCIDENT.forecastRadiiM[2] * 0.8, 1)
      forecast.rotation.x = -Math.PI / 2
      forecast.position.set(1800, 40, -1200)
      fireGroup.add(forecast)

      const count = 180
      const positions = new Float32Array(count * 3)
      for (let i = 0; i < count; i++) {
        const a = Math.random() * Math.PI * 2
        const r = Math.random() * INCIDENT.burnRadiusM
        positions[i * 3] = Math.cos(a) * r
        positions[i * 3 + 1] = 30 + Math.random() * 220
        positions[i * 3 + 2] = Math.sin(a) * r * 0.72
      }
      const pGeo = new THREE.BufferGeometry()
      pGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
      const pMat = new THREE.PointsMaterial({
        color: 0xff8c2a,
        size: 55,
        transparent: true,
        opacity: 0.9,
        depthTest: false,
        depthWrite: false,
        sizeAttenuation: true,
      })
      const points = new THREE.Points(pGeo, pMat)
      points.name = 'embers'
      fireGroup.add(points)

      scene.add(fireGroup)

      renderer = new THREE.WebGLRenderer({
        canvas: map.getCanvas(),
        context: gl,
        antialias: true,
      })
      renderer.autoClear = false
    },

    render(_gl, args) {
      if (!renderer || !fireGroup) return

      const model = maplibregl.MercatorCoordinate.fromLngLat([fireLon, fireLat], 0)
      const scale = model.meterInMercatorCoordinateUnits()

      const rotationX = new THREE.Matrix4().makeRotationAxis(
        new THREE.Vector3(1, 0, 0),
        Math.PI / 2,
      )
      const rotationZ = new THREE.Matrix4().makeRotationAxis(
        new THREE.Vector3(0, 0, 1),
        -Math.PI,
      )

      const l = new THREE.Matrix4()
        .makeTranslation(model.x, model.y, model.z)
        .scale(new THREE.Vector3(scale, -scale, scale))
        .multiply(rotationX)
        .multiply(rotationZ)

      anim += 0.02
      const embers = fireGroup.getObjectByName('embers')
      if (embers) {
        const pos = embers.geometry.attributes.position
        for (let i = 0; i < pos.count; i++) {
          let y = pos.getY(i) + 1.8 + Math.sin(anim + i) * 0.4
          if (y > 280) y = 30
          pos.setY(i, y)
        }
        pos.needsUpdate = true
      }
      const forecast = fireGroup.getObjectByName('forecast')
      if (forecast) {
        const pulse = 1 + Math.sin(anim * 0.7) * 0.03
        forecast.scale.set(
          INCIDENT.forecastRadiiM[2] * pulse,
          INCIDENT.forecastRadiiM[2] * 0.8 * pulse,
          1,
        )
        forecast.material.opacity = 0.28 + Math.sin(anim * 0.7) * 0.06
      }

      const main =
        args?.defaultProjectionData?.mainMatrix ||
        args?.modelViewProjectionMatrix ||
        args
      camera.projectionMatrix = new THREE.Matrix4().fromArray(main).multiply(l)

      renderer.resetState()
      renderer.render(scene, camera)
      map.triggerRepaint()
    },
  }
}

export default function PatternMapLibreThree() {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const [status, setStatus] = useState('Loading MapLibre + Terrarium DEM…')

  useEffect(() => {
    if (!containerRef.current) return
    let cancelled = false
    let activationTimer

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          imagery: ESRI_IMAGERY,
          terrainSource: TERRARIUM_DEM,
        },
        layers: [
          {
            id: 'imagery',
            type: 'raster',
            source: 'imagery',
          },
        ],
      },
      center: [INCIDENT.lon, INCIDENT.lat],
      zoom: 2.2,
      pitch: 0,
      bearing: 0,
      maxPitch: 85,
      attributionControl: true,
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right')

    map.on('load', () => {
      if (cancelled) return

      try {
        map.addSource('terrainSource', TERRARIUM_DEM)
      } catch {
        /* already in style */
      }

      try {
        map.setTerrain({ source: 'terrainSource', exaggeration: 1.6 })
      } catch (e) {
        console.warn('terrain', e)
      }

      try {
        if (typeof map.setProjection === 'function') {
          map.setProjection({ type: 'globe' })
        }
      } catch {
        /* ignore */
      }

      for (const tw of INCIDENT.towns) {
        const el = document.createElement('div')
        el.className = 'town-marker'
        el.textContent = tw.name
        new maplibregl.Marker({ element: el }).setLngLat([tw.lon, tw.lat]).addTo(map)
      }

      // Visible GeoJSON fallback so fire is obvious even if Three fails
      map.addSource('fire-fallback', {
        type: 'geojson',
        data: {
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'Point',
            coordinates: [INCIDENT.lon, INCIDENT.lat],
          },
        },
      })
      map.addLayer({
        id: 'fire-fallback-glow',
        type: 'circle',
        source: 'fire-fallback',
        paint: {
          'circle-radius': 40,
          'circle-color': '#E8590C',
          'circle-opacity': 0.35,
          'circle-blur': 0.7,
        },
      })

      setStatus('Flying to Sierra Vista · AWS Terrarium DEM')

      map.flyTo({
        center: [INCIDENT.lon, INCIDENT.lat],
        zoom: 12,
        pitch: 60,
        bearing: 28,
        duration: 2500,
        essential: true,
      })

      const activateThree = () => {
        if (cancelled) return
        try {
          map.setProjection({ type: 'mercator' })
        } catch {
          /* ignore */
        }
        if (!map.getLayer('ember-three-fire')) {
          try {
            map.addLayer(createFireLayer(map))
            setStatus('AWS Terrarium DEM · Esri imagery · Three.js fire layer active')
          } catch (e) {
            console.error(e)
            setStatus('Three.js layer failed — showing circle fallback. ' + (e.message || e))
          }
        }
      }

      map.once('moveend', activateThree)
      // Safety if moveend already happened or fly was skipped
      activationTimer = setTimeout(activateThree, 3000)
    })

    map.on('error', (e) => {
      console.warn('maplibre error', e?.error || e)
    })

    return () => {
      cancelled = true
      clearTimeout(activationTimer)
      map.remove()
      mapRef.current = null
    }
  }, [])

  return (
    <PrototypeChrome
      pattern="Pattern 2"
      title="MapLibre + Three.js"
      blurb="Globe with real Terrarium elevation, then regional mercator + Three.js fire mesh/embers."
      footer={<span className="proto__status">{status}</span>}
    >
      <div ref={containerRef} className="proto__canvas" />
    </PrototypeChrome>
  )
}
