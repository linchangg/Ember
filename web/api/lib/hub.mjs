/** Server-only hub adapter. Credentials come from environment, never request data. */
export function decodeRows(result) {
  const columns = result.schema.elements;
  return result.rows.map((row) =>
    Object.fromEntries(
      columns.map((column, i) => {
        const variants = column.algebraic_type?.Sum?.variants?.map(
          (v) => v.name?.some,
        );
        let value = row[i];
        if (JSON.stringify(variants) === '["some","none"]') {
          value = Array.isArray(value)
            ? value[0] === 0
              ? value[1]
              : null
            : (value?.some ?? null);
        }
        return [column.name.some, value];
      }),
    ),
  );
}

export function hubClient(env, fetcher = fetch) {
  const { STDB_URI, STDB_DB, STDB_SERVICE_TOKEN } = env;
  if (!STDB_URI || !STDB_DB || !STDB_SERVICE_TOKEN)
    throw new Error("Service unavailable");
  const base = new URL(STDB_URI);
  if (
    base.protocol !== "https:" &&
    !(
      base.protocol === "http:" &&
      ["localhost", "127.0.0.1"].includes(base.hostname)
    )
  ) {
    throw new Error("Service unavailable");
  }
  const prefix = `${base.href.replace(/\/$/, "")}/v1/database/${encodeURIComponent(STDB_DB)}`;
  async function post(path, body, contentType) {
    const response = await fetcher(prefix + path, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${STDB_SERVICE_TOKEN}`,
        "Content-Type": contentType,
      },
      body,
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Hub request failed");
    return response;
  }
  return {
    async sql(query) {
      const response = await post("/sql", query, "text/plain");
      return (await response.json()).flatMap(decodeRows);
    },
    async call(name, args) {
      await post(`/call/${name}`, JSON.stringify(args), "application/json");
    },
  };
}
