import type { GeoCollection } from '../data/source.ts'
/** Bounds of published lon/lat vectors, used only to frame the view, never to predict spread. */
export function vectorBounds(
  collection: GeoCollection
): [[number, number], [number, number]] | null {
  let west = Infinity,
    east = -Infinity,
    south = Infinity,
    north = -Infinity
  const visit = (value: unknown) => {
    if (!Array.isArray(value)) return
    if (
      value.length === 2 &&
      typeof value[0] === 'number' &&
      typeof value[1] === 'number'
    ) {
      west = Math.min(west, value[0])
      east = Math.max(east, value[0])
      south = Math.min(south, value[1])
      north = Math.max(north, value[1])
    } else for (const child of value) visit(child)
  }
  for (const feature of collection.features) visit(feature.geometry.coordinates)
  return Number.isFinite(west)
    ? [
        [west, south],
        [east, north],
      ]
    : null
}
