/** Pure frozen-wire decoder. No probability is invented between the five quantiles. */
export type RasterMode = 'probability' | 0 | 1 | 2 | 3 | 4
export const PROBABILITY_COLORS = [
  [0, 0, 0, 0],
  [253, 224, 197, 180],
  [249, 185, 138, 190],
  [240, 122, 58, 205],
  [232, 89, 12, 215],
  [217, 58, 43, 225],
] as const
export const BURNED_COLOR = [75, 55, 50, 230] as const
export interface DecodedRaster {
  rgba: Uint8ClampedArray
  reached: number
  burned: number
  classes: Uint8Array
}
export function decodeForecast(
  bytes: Uint8Array,
  nx: number,
  ny: number,
  issueMs: number,
  viewMs: number,
  horizonMin: number,
  mode: RasterMode = 'probability'
): DecodedRaster {
  const cells = nx * ny
  if (
    !Number.isSafeInteger(nx) ||
    !Number.isSafeInteger(ny) ||
    nx < 1 ||
    ny < 1 ||
    cells > 2_000_000 ||
    bytes.length !== 5 * cells ||
    !Number.isSafeInteger(issueMs) ||
    !Number.isSafeInteger(viewMs) ||
    viewMs < issueMs ||
    !Number.isFinite(horizonMin) ||
    horizonMin <= 0 ||
    horizonMin > 508 ||
    (mode !== 'probability' && ![0, 1, 2, 3, 4].includes(mode))
  )
    throw new Error('Invalid raster selection')
  if (viewMs > issueMs + horizonMin * 60000)
    throw new Error('View exceeds forecast horizon')
  const rgba = new Uint8ClampedArray(4 * cells),
    classes = new Uint8Array(cells)
  const leadByte = Math.floor((viewMs - issueMs) / 120000)
  let reached = 0,
    burned = 0
  for (let cell = 0; cell < cells; cell++) {
    let count = 0,
      allBurned = true,
      previous = 0
    for (let q = 0; q < 5; q++) {
      const value = bytes[q * cells + cell]
      if (
        value < previous ||
        (value !== 255 && value > Math.ceil(horizonMin / 2))
      )
        throw new Error('Invalid quantile encoding')
      previous = value
      allBurned = allBurned && value === 0
      if (value !== 255 && value <= leadByte) count++
    }
    classes[cell] = count
    const visible =
      mode === 'probability'
        ? count > 0
        : bytes[mode * cells + cell] !== 255 &&
          bytes[mode * cells + cell] <= leadByte
    const color = allBurned
      ? BURNED_COLOR
      : visible
        ? mode === 'probability'
          ? PROBABILITY_COLORS[count]
          : PROBABILITY_COLORS[4]
        : PROBABILITY_COLORS[0]
    if (allBurned) burned++
    if (color[3] > 0) reached++
    rgba.set(color, 4 * cell)
  }
  return { rgba, reached, burned, classes }
}
