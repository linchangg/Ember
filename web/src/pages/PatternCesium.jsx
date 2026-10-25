import { useEffect, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import PrototypeChrome from '../components/PrototypeChrome'
import {
  ESRI_ELEVATION_TOKEN,
  ESRI_ELEVATION_URL,
  INCIDENT,
} from '../lib/incident'

export default function PatternCesium() {
  const containerRef = useRef(null)
  const viewerRef = useRef(null)
  const [status, setStatus] = useState('Loading Cesium + Esri elevation…')
  const [t, setT] = useState(0.45)

  useEffect(() => {
    let cancelled = false
    let viewer

    async function boot() {
      if (!containerRef.current) return

      viewer = new Cesium.Viewer(containerRef.current, {
        animation: false,
        timeline: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        terrainProvider: undefined,
      })
      viewerRef.current = viewer

      // Satellite basemap (URL template avoids ArcGIS auth surprises)
      viewer.imageryLayers.removeAll()
      viewer.imageryLayers.addImageryProvider(
        new Cesium.UrlTemplateImageryProvider({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          maximumLevel: 19,
          credit: 'Esri World Imagery',
        }),
      )

      // Real global elevation (Esri WorldElevation3D)
      try {
        const terrain = await Cesium.ArcGISTiledElevationTerrainProvider.fromUrl(
          ESRI_ELEVATION_URL,
          { token: ESRI_ELEVATION_TOKEN },
        )
        if (!cancelled) {
          viewer.terrainProvider = terrain
          setStatus('Esri WorldElevation3D · World Imagery')
        }
      } catch (err) {
        console.warn('Esri terrain failed, using ellipsoid', err)
        if (!cancelled) setStatus('Terrain fallback (ellipsoid) — imagery only')
      }

      // The terrain request may finish after navigation or StrictMode cleanup.
      if (cancelled || viewer.isDestroyed()) return

      viewer.scene.globe.depthTestAgainstTerrain = true
      viewer.scene.globe.enableLighting = false
      viewer.scene.fog.enabled = true
      viewer.scene.skyAtmosphere.show = true

      // Synthetic fire entities
      const burn = viewer.entities.add({
        name: 'Burned',
        position: Cesium.Cartesian3.fromDegrees(INCIDENT.lon, INCIDENT.lat),
        ellipse: {
          semiMajorAxis: INCIDENT.burnRadiusM,
          semiMinorAxis: INCIDENT.burnRadiusM * 0.72,
          material: Cesium.Color.fromCssColorString('#7A3428').withAlpha(0.75),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString('#F06A1A'),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          classificationType: Cesium.ClassificationType.TERRAIN,
        },
      })

      const forecast = viewer.entities.add({
        name: 'Forecast',
        position: Cesium.Cartesian3.fromDegrees(INCIDENT.lon + 0.02, INCIDENT.lat + 0.015),
        ellipse: {
          semiMajorAxis: INCIDENT.forecastRadiiM[2],
          semiMinorAxis: INCIDENT.forecastRadiiM[2] * 0.8,
          material: Cesium.Color.fromCssColorString('#D93A2B').withAlpha(0.35),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString('#E8590C').withAlpha(0.9),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          classificationType: Cesium.ClassificationType.TERRAIN,
        },
      })

      for (const tw of INCIDENT.towns) {
        viewer.entities.add({
          name: tw.name,
          position: Cesium.Cartesian3.fromDegrees(tw.lon, tw.lat),
          point: {
            pixelSize: 10,
            color: Cesium.Color.fromCssColorString('#222222'),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 2,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
          label: {
            text: tw.name,
            font: '600 13px Inter, system-ui, sans-serif',
            fillColor: Cesium.Color.fromCssColorString('#222'),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            pixelOffset: new Cesium.Cartesian2(0, -14),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        })
      }

      viewer.burnEntity = burn
      viewer.forecastEntity = forecast

      await viewer.flyTo(burn, {
        offset: new Cesium.HeadingPitchRange(
          Cesium.Math.toRadians(40),
          Cesium.Math.toRadians(-35),
          28000,
        ),
        duration: 2.4,
      })
    }

    boot().catch((e) => {
      if (cancelled) return
      console.error(e)
      setStatus(String(e.message || e))
    })

    return () => {
      cancelled = true
      if (viewer && !viewer.isDestroyed()) viewer.destroy()
      viewerRef.current = null
    }
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer || viewer.isDestroyed() || !viewer.forecastEntity) return
    const r = Cesium.Math.lerp(
      INCIDENT.burnRadiusM,
      INCIDENT.forecastRadiiM[3],
      t,
    )
    viewer.forecastEntity.ellipse.semiMajorAxis = r
    viewer.forecastEntity.ellipse.semiMinorAxis = r * 0.8
    viewer.forecastEntity.ellipse.material = Cesium.Color.fromCssColorString('#D93A2B').withAlpha(
      0.2 + t * 0.35,
    )
  }, [t])

  return (
    <PrototypeChrome
      pattern="Pattern 1"
      title="Cesium only"
      blurb="Drag to orbit · scroll to zoom · scrub forecast. Real Esri elevation under the synthetic fire ellipses."
      footer={
        <>
          <label className="proto__scrub">
            Forecast horizon
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={t}
              onChange={(e) => setT(+e.target.value)}
            />
          </label>
          <span className="proto__status">{status}</span>
        </>
      }
    >
      <div ref={containerRef} className="proto__canvas cesium-root" />
    </PrototypeChrome>
  )
}
