import test from "node:test";
import assert from "node:assert/strict";
import { pcmWav } from "./audio.js";
test("browser encoder produces mono 16 kHz PCM with bounded amplitudes", async () => {
  const bytes = await pcmWav(new Float32Array([-2, 0, 2])).arrayBuffer(),
    v = new DataView(bytes);
  assert.equal(v.getUint32(24, true), 16000);
  assert.equal(v.getUint16(22, true), 1);
  assert.equal(v.getInt16(44, true), -32768);
  assert.equal(v.getInt16(48, true), 32767);
  assert.equal(v.getUint32(40, true), 6);
});
test("invalid or oversized recordings are rejected", () => {
  for (const x of [
    new Float32Array(),
    new Float32Array([NaN]),
    new Float32Array(240001),
  ])
    assert.throws(() => pcmWav(x));
});
