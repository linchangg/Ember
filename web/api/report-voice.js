import { voiceReport } from "./lib/voice.mjs";
export const config = { api: { bodyParser: false } };
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST")
    return res.status(405).json({ error: "POST required" });
  try {
    const chunks = [];
    let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 500000)
        return res.status(413).json({ error: "Audio upload too large" });
      chunks.push(chunk);
    }
    const contentType = req.headers["content-type"];
    if (
      typeof contentType !== "string" ||
      !contentType.startsWith("multipart/form-data;")
    )
      return res.status(400).json({ error: "Multipart WAV upload required" });
    const incoming = new Request("http://localhost/report", {
      method: "POST",
      headers: { "Content-Type": contentType },
      body: Buffer.concat(chunks),
    });
    const form = await incoming.formData(),
      audio = form.get("audio");
    if (!(audio instanceof Blob))
      return res.status(400).json({ error: "Audio file required" });
    const fields = Object.fromEntries(
      [...form.entries()].filter(([key]) => key !== "audio"),
    );
    const result = await voiceReport(
      req,
      new Uint8Array(await audio.arrayBuffer()),
      fields,
    );
    return res.status(result.status).json(result.body);
  } catch {
    return res.status(400).json({ error: "Invalid audio upload" });
  }
}
