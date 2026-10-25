// Linda's scenario API, with the shared validated transport/decoder underneath.
// replayClock.js owns time; this module never creates a second replay clock.
import { openScenario } from '../data/scenario.ts'
export function loadScenario(scenario, options = {}) {
  const mode = import.meta.env.VITE_DATA_SOURCE ?? 'file'
  if (mode !== 'file') return Promise.reject(new Error(`Forecast source ${mode} is not connected; Phase E adds hub forecast delivery.`))
  return openScenario(scenario, {
    base: import.meta.env.VITE_ASSET_BASE ?? '/',
    pageUrl: window.location.href,
    ...options,
  })
}
