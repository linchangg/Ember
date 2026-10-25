const hasControlOrSpace = (value: string) =>
  Array.from(value).some((char) => char.codePointAt(0)! <= 32)
/** Reject paths before URL normalization can erase traversal or change origin. */
export function assetRoot(base: string, pageUrl = 'http://localhost/') {
  const url = new URL(base, pageUrl)
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error('Asset base must be a plain HTTP(S) directory URL')
  if (!url.pathname.endsWith('/')) url.pathname += '/'
  return url
}
export function assetUrl(base: URL, relative: string) {
  if (
    typeof relative !== 'string' ||
    !relative ||
    /[\\?#]/u.test(relative) ||
    hasControlOrSpace(relative) ||
    relative.startsWith('/') ||
    /^[a-z][a-z\d+.-]*:/i.test(relative)
  )
    throw new Error('Asset path must be relative and confined')
  let decoded: string
  try {
    decoded = decodeURIComponent(relative)
  } catch {
    throw new Error('Malformed asset path')
  }
  if (
    decoded.includes('%') ||
    decoded.includes('\\') ||
    decoded.split('/').some((p) => p === '..' || p === '.') ||
    decoded.startsWith('/') ||
    /[?#]/u.test(decoded) ||
    hasControlOrSpace(decoded)
  )
    throw new Error('Escaped asset path')
  const url = new URL(relative, base)
  if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname))
    throw new Error('Asset escaped root')
  return url.href
}
export function safeId(value: string) {
  if (!/^[a-zA-Z0-9_-]+$/.test(value))
    throw new Error('Unsafe scenario or branch ID')
  return value
}
export async function readResponse(
  fetcher: typeof fetch,
  url: string,
  signal: AbortSignal,
  binary = false
) {
  const response = await fetcher(url, {
    signal,
    credentials: 'omit',
    redirect: 'error',
  })
  if (!response.ok) throw new Error(`Asset HTTP ${response.status}: ${url}`)
  const limit = binary ? 16_000_000 : 32_000_000
  const declared = Number(response.headers.get('content-length') ?? 0)
  if (declared > limit) throw new Error('Asset exceeds byte budget')
  // Bound streamed bytes too; Content-Length is not always present or trustworthy.
  const reader = response.body?.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  if (!reader) throw new Error('Asset has no body')
  try {
    while (true) {
      signal.throwIfAborted()
      const { done, value } = await reader.read()
      if (done) break
      length += value.length
      if (length > limit) throw new Error('Asset exceeds byte budget')
      chunks.push(value)
    }
  } catch (error) {
    await reader.cancel().catch(() => {})
    throw error
  } finally {
    reader.releaseLock()
  }
  signal.throwIfAborted()
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.length
  }
  return binary
    ? bytes
    : (JSON.parse(new TextDecoder().decode(bytes)) as unknown)
}
