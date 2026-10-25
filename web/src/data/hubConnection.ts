/** Pinned 2.13 SDK transport. Each callback sees the reconciled transaction cache. */
import { DbConnection } from '../module_bindings/index.ts'
import { HUB_TABLES, adapt } from './hubRows.ts'
import type { HubRows, Row } from './hubRows.ts'
export interface HubTransport {
  close(): void
  call(name: string, args: Record<string, unknown>): Promise<void>
}
export function connectHub(
  uri: string,
  database: string,
  scenario: string,
  onRows: (rows: HubRows) => void,
  onError: (error: Error) => void,
  onReady: () => void
): HubTransport {
  let closed = false,
    queued = false,
    applied = false
  const flush = () => {
    if (closed || !applied || queued) return
    queued = true
    queueMicrotask(() => {
      queued = false
      if (closed || !applied) return
      try {
        const rows: HubRows = {}
        for (const table of HUB_TABLES)
          rows[table] = [...conn.db[table].iter()]
            .map((r) => adapt(r as unknown as Row))
            .filter((r) => r.scenario_id === scenario)
        onRows(rows)
      } catch (e) {
        onError(e instanceof Error ? e : new Error(String(e)))
      }
    })
  }
  const conn = DbConnection.builder()
    .withUri(uri)
    .withDatabaseName(database)
    .onConnect((c) => {
      if (closed) {
        c.disconnect()
        return
      }
      c.subscriptionBuilder()
        .onApplied(() => {
          applied = true
          onReady()
          flush()
        })
        .onError((ctx) => onError(new Error(String(ctx.event))))
        .subscribe(
          HUB_TABLES.map(
            (table) =>
              `SELECT * FROM ${table.replace(/[A-Z]/g, (c) => '_' + c.toLowerCase())} WHERE scenario_id = '${scenario}'`
          )
        )
    })
    .onConnectError((_c, e) => {
      applied = false
      onError(e)
    })
    .onDisconnect(() => {
      applied = false
      if (!closed)
        onError(new Error('Hub disconnected; reconnecting with full snapshot'))
    })
    .build()
  for (const table of HUB_TABLES) {
    conn.db[table].onInsert(flush)
    conn.db[table].onDelete(flush)
    // All subscribed tables have primary keys, hence onUpdate in generated handles.
    conn.db[table].onUpdate(flush)
  }
  return {
    close() {
      closed = true
      applied = false
      conn.disconnect()
    },
    async call(name, args) {
      if (closed || !applied) throw new Error('Hub is not synchronized')
      const reducers = conn.reducers as unknown as Record<
        string,
        (args: Record<string, unknown>) => Promise<void>
      >
      if (!reducers[name]) throw new Error('Unknown hub action')
      await reducers[name](args)
    },
  }
}
