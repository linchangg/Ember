import test from "node:test";
import assert from "node:assert/strict";
import { wavDuration, transcribeAndClassify, voiceReport } from "./voice.mjs";
function wav(seconds = 1) {
  const n = Math.round(seconds * 32000),
    b = Buffer.alloc(44 + n);
  b.write("RIFF");
  b.writeUInt32LE(36 + n, 4);
  b.write("WAVE", 8);
  b.write("fmt ", 12);
  b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22);
  b.writeUInt32LE(16000, 24);
  b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32);
  b.writeUInt16LE(16, 34);
  b.write("data", 36);
  b.writeUInt32LE(n, 40);
  return b;
}
test("PCM duration and upload limits enforced before provider calls", () => {
  assert.equal(wavDuration(wav(1)), 1);
  assert.equal(wavDuration(wav(15)), 15);
  for (const bytes of [wav(16), Buffer.from("invalid"), wav().subarray(0, 50)])
    assert.throws(() => wavDuration(bytes));
});
test("classifier selects tool blocks and cannot return an invented coordinate", async () => {
  const fetcher = async (url) =>
    url.includes("x.ai")
      ? Response.json({ text: "I see smoke nearby." })
      : Response.json({
          content: [
            { type: "text", text: "not a result" },
            {
              type: "tool_use",
              name: "classify_report",
              input: { is_report: true, kind: "smoke" },
            },
          ],
        });
  const r = await transcribeAndClassify(wav(), {
    env: { XAI_API_KEY: "test", ANTHROPIC_API_KEY: "test" },
    fetcher,
  });
  assert.equal(r.kind, "smoke");
  assert.equal(r.lon, undefined);
});
test("extra parser fields are rejected", async () => {
  const fetcher = async (url) =>
    url.includes("x.ai")
      ? Response.json({ text: "fire" })
      : Response.json({
          content: [
            {
              type: "tool_use",
              name: "classify_report",
              input: { is_report: true, kind: "fire", lon: 12 },
            },
          ],
        });
  await assert.rejects(() =>
    transcribeAndClassify(wav(), { env: {}, fetcher }),
  );
});
test("disabled voice service never calls a provider", async () => {
  let n = 0;
  const r = await voiceReport(
    {},
    wav(),
    {},
    {
      env: {},
      fetcher: async () => {
        n++;
      },
    },
  );
  assert.equal(r.status, 503);
  assert.equal(n, 0);
});
