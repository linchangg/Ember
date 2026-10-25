/** Local-only adapter for testing Vercel handlers. No credentials loaded implicitly. */
import { createServer } from 'node:http'
import join from '../web/api/join.js'
import tap from '../web/api/report-tap.js'
import voice from '../web/api/report-voice.js'
import status from '../web/api/cartesia/status.js'
import ack from '../web/api/cartesia/ack.js'
const routes = { '/api/join': join, '/api/report-tap': tap, '/api/report-voice': voice, '/api/cartesia/status': status, '/api/cartesia/ack': ack }
const port = Number(process.env.DEV_SERVICE_PORT || 8797)
createServer(async (req, res) => {
  res.status = code => { res.statusCode = code; return res }
  res.json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); return res }
  const host = req.headers.host?.split(':')[0]
  if (!['localhost', '127.0.0.1'].includes(host)) return res.status(403).json({ error: 'Local host only' })
  if (req.headers.origin) {
    try { if (!['localhost', '127.0.0.1'].includes(new URL(req.headers.origin).hostname)) return res.status(403).json({ error: 'Local origin only' }) }
    catch { return res.status(403).json({ error: 'Invalid origin' }) }
  }
  const path = new URL(req.url, 'http://localhost').pathname, handler = routes[path]
  if (!handler) return res.status(404).json({ error: 'Unknown service' })
  try {
    if (path !== '/api/report-voice') {
      let bytes = 0; const chunks = []
      for await (const chunk of req) {
        bytes += chunk.length
        if (bytes > 4096) return res.status(413).json({ error: 'Request too large' })
        chunks.push(chunk)
      }
      req.body = JSON.parse(Buffer.concat(chunks).toString() || '{}')
    }
    await handler(req, res)
  } catch { res.status(400).json({ error: 'Invalid request' }) }
}).listen(port, '127.0.0.1', () => console.log(`Local demo services on 127.0.0.1:${port}; voice stays disabled unless explicitly configured`))
