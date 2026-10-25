/** Shared mock incident placed on real Sierra Nevada topography. */

export const INCIDENT = {
  id: 'sierra-vista',
  name: 'Sierra Vista Fire',
  /** Approximate ignition / fire centroid (lon, lat) */
  lon: -118.92,
  lat: 37.18,
  /** Rough burned footprint radius in meters (synthetic) */
  burnRadiusM: 4200,
  forecastRadiiM: [4200, 6500, 9200, 12000],
  towns: [
    { name: 'Pine Hollow', lon: -118.85, lat: 37.22 },
    { name: 'Cedar Flat', lon: -118.78, lat: 37.12 },
    { name: 'Owl Creek', lon: -119.02, lat: 37.25 },
  ],
}

/** AWS Terrarium DEM — real global elevation, no API key. */
export const TERRARIUM_DEM = {
  type: 'raster-dem',
  tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
  encoding: 'terrarium',
  tileSize: 256,
  maxzoom: 15,
  attribution:
    'Elevation © <a href="https://github.com/tilezen/joerd">Mapzen / AWS Terrain Tiles</a>',
}

/** Esri World Imagery (satellite). */
export const ESRI_IMAGERY = {
  type: 'raster',
  tiles: [
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  ],
  tileSize: 256,
  attribution:
    'Tiles © <a href="https://www.esri.com/">Esri</a> — Source: Esri, Maxar, Earthstar Geographics',
  maxzoom: 19,
}

/** Esri World Topo (readable labels / contours when zoomed). */
export const ESRI_TOPO = {
  type: 'raster',
  tiles: [
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
  ],
  tileSize: 256,
  attribution: 'Tiles © Esri',
  maxzoom: 19,
}

export const ESRI_ELEVATION_URL =
  'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer'

/** Public demo token shown in Cesium/Esri docs for WorldElevation3D. */
export const ESRI_ELEVATION_TOKEN =
  'KED1aF_I4UzXOHy3BnhwyBHU4l5oY6rO6walkmHoYqGp4XyIWUd5YZUC1ZrLAzvV40pR6gBXQayh0eFA8m6vPg..'
