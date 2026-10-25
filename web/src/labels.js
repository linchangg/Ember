// Placeholder gazetteer. Positions are approximate; replace with Natural Earth / GNIS / OSM data later.
// [name, lon, lat, minZoom] where minZoom is the globe zoom at which the label starts to fade in.

export const CITIES = [
  // tier 1: visible from the default view
  ['New York', -74.01, 40.71, 1.1], ['Los Angeles', -118.24, 34.05, 1.1], ['Mexico City', -99.13, 19.43, 1.1],
  ['São Paulo', -46.63, -23.55, 1.1], ['Buenos Aires', -58.38, -34.6, 1.1], ['London', -0.13, 51.51, 1.1],
  ['Paris', 2.35, 48.86, 1.3], ['Moscow', 37.62, 55.76, 1.1], ['Cairo', 31.24, 30.04, 1.1], ['Lagos', 3.38, 6.52, 1.2],
  ['Johannesburg', 28.05, -26.2, 1.2], ['Mumbai', 72.88, 19.08, 1.2], ['Delhi', 77.21, 28.61, 1.2], ['Beijing', 116.41, 39.9, 1.1],
  ['Shanghai', 121.47, 31.23, 1.3], ['Tokyo', 139.69, 35.68, 1.1], ['Jakarta', 106.85, -6.21, 1.3], ['Sydney', 151.21, -33.87, 1.1],
  // tier 2
  ['Chicago', -87.63, 41.88, 2.2], ['Houston', -95.37, 29.76, 2.4], ['Toronto', -79.38, 43.65, 2.4], ['Vancouver', -123.12, 49.28, 2.4],
  ['San Francisco', -122.42, 37.77, 2.4], ['Seattle', -122.33, 47.61, 2.8], ['Miami', -80.19, 25.76, 2.4], ['Washington', -77.04, 38.91, 3],
  ['Denver', -104.99, 39.74, 3], ['Phoenix', -112.07, 33.45, 3], ['San Diego', -117.16, 32.72, 3.2], ['Las Vegas', -115.14, 36.17, 3.2],
  ['Havana', -82.37, 23.11, 2.8], ['Bogotá', -74.07, 4.71, 2.2], ['Lima', -77.04, -12.05, 2.2], ['Rio de Janeiro', -43.17, -22.91, 2.4],
  ['Santiago', -70.67, -33.45, 2.4], ['Madrid', -3.7, 40.42, 2.4], ['Berlin', 13.4, 52.52, 2.4], ['Rome', 12.5, 41.9, 2.4],
  ['Istanbul', 28.98, 41.01, 2.4], ['Nairobi', 36.82, -1.29, 2.4], ['Kinshasa', 15.27, -4.44, 2.6], ['Cape Town', 18.42, -33.92, 2.6],
  ['Addis Ababa', 38.74, 9.03, 2.8], ['Tehran', 51.39, 35.69, 2.4], ['Riyadh', 46.68, 24.71, 2.4], ['Dubai', 55.27, 25.2, 2.8],
  ['Karachi', 67.01, 24.86, 2.6], ['Dhaka', 90.41, 23.81, 2.6], ['Bangkok', 100.5, 13.76, 2.4], ['Singapore', 103.82, 1.35, 3],
  ['Manila', 120.98, 14.6, 2.6], ['Hong Kong', 114.17, 22.32, 3], ['Seoul', 126.98, 37.57, 2.4], ['Melbourne', 144.96, -37.81, 2.6],
  ['Perth', 115.86, -31.95, 3], ['Auckland', 174.76, -36.85, 3], ['Anchorage', -149.9, 61.22, 3], ['Honolulu', -157.86, 21.31, 3],
  ['Sacramento', -121.49, 38.58, 4.5],
]

export const STATES = [
  ['Alabama', -86.8, 32.8], ['Alaska', -152.5, 64.2], ['Arizona', -111.7, 34.2], ['Arkansas', -92.4, 34.9], ['California', -119.5, 37.2],
  ['Colorado', -105.5, 39], ['Connecticut', -72.7, 41.6], ['Delaware', -75.5, 39], ['Florida', -82.4, 28.6], ['Georgia', -83.4, 32.7],
  ['Hawaii', -156.3, 20.8], ['Idaho', -114.6, 44.4], ['Illinois', -89.2, 40], ['Indiana', -86.3, 39.9], ['Iowa', -93.5, 42.1],
  ['Kansas', -98.4, 38.5], ['Kentucky', -85.3, 37.5], ['Louisiana', -92, 31.1], ['Maine', -69.2, 45.3], ['Maryland', -76.8, 39],
  ['Massachusetts', -71.8, 42.3], ['Michigan', -85.4, 44.3], ['Minnesota', -94.3, 46.3], ['Mississippi', -89.7, 32.7], ['Missouri', -92.5, 38.4],
  ['Montana', -109.6, 47], ['Nebraska', -99.8, 41.5], ['Nevada', -116.6, 39.3], ['New Hampshire', -71.6, 43.7], ['New Jersey', -74.7, 40.1],
  ['New Mexico', -106.1, 34.4], ['New York', -75.5, 42.9], ['North Carolina', -79.4, 35.6], ['North Dakota', -100.5, 47.5], ['Ohio', -82.8, 40.3],
  ['Oklahoma', -97.5, 35.6], ['Oregon', -120.6, 43.9], ['Pennsylvania', -77.8, 40.9], ['Rhode Island', -71.5, 41.7], ['South Carolina', -80.9, 33.9],
  ['South Dakota', -100.2, 44.4], ['Tennessee', -86.3, 35.8], ['Texas', -99.3, 31.5], ['Utah', -111.7, 39.3], ['Vermont', -72.7, 44],
  ['Virginia', -78.8, 37.5], ['Washington', -120.5, 47.4], ['West Virginia', -80.6, 38.6], ['Wisconsin', -89.9, 44.6], ['Wyoming', -107.5, 43],
]

// Close-up labels, Eaton Fire area only. kind: town | peak | region
export const LOCAL = [
  ['Altadena', -118.131, 34.19, 'town', 25], ['Pasadena', -118.145, 34.148, 'town', 25], ['Sierra Madre', -118.053, 34.162, 'town', 40],
  ['Monrovia', -118.002, 34.144, 'town', 40], ['Arcadia', -118.035, 34.14, 'town', 40], ['Duarte', -117.977, 34.14, 'town', 60],
  ['La Cañada Flintridge', -118.2, 34.22, 'town', 40], ['Glendale', -118.255, 34.143, 'town', 25], ['Burbank', -118.309, 34.181, 'town', 40],
  ['San Marino', -118.107, 34.121, 'town', 60], ['South Pasadena', -118.15, 34.116, 'town', 60], ['Temple City', -118.058, 34.107, 'town', 60],
  ['Los Angeles', -118.244, 34.052, 'town', 12],
  ['Mount Wilson', -118.061, 34.224, 'peak', 200], ['Eaton Canyon', -118.096, 34.183, 'region', 300],
  ['San Gabriel Mountains', -118.05, 34.28, 'region', 25], ['San Gabriel Valley', -118.0, 34.09, 'region', 25],
]
