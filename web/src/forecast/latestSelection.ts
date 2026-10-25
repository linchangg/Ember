export type TimelineRequest = { issue: number; leadMin: number }

/**
 * Applies timeline requests one at a time. A request that arrives while one is
 * loading replaces the pending request. The in-flight request can finish its
 * read, then the newest request is applied. A superseded request cannot publish
 * after a newer one has been asked for.
 */
export function createTimelineSelection(
  apply: (
    request: TimelineRequest,
    stillCurrent: () => boolean
  ) => Promise<void>
) {
  let latest: TimelineRequest | null = null
  let token = 0
  let running = false

  const start = () => {
    if (running) return
    running = true
    void (async () => {
      try {
        while (latest) {
          const request = latest
          const started = token
          latest = null
          try {
            await apply(
              request,
              () => token === started && latest === null
            )
          } catch {
            /* The caller reports a failure of the request that is still current. */
          }
        }
      } finally {
        running = false
        if (latest) start()
      }
    })()
  }

  return {
    request(next: TimelineRequest) {
      token += 1
      latest = next
      start()
    },
  }
}
