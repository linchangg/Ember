export function pcmWav(samples) {
  if (
    !(samples instanceof Float32Array) ||
    samples.length < 1 ||
    samples.length > 240000 ||
    !samples.every(Number.isFinite)
  )
    throw Error("Invalid audio samples");
  const bytes = new ArrayBuffer(44 + samples.length * 2),
    view = new DataView(bytes);
  const text = (at, value) =>
    [...value].forEach((x, i) => view.setUint8(at + i, x.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, bytes.byteLength - 8, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => {
    const x = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + 2 * i, Math.round(x * (x < 0 ? 32768 : 32767)), true);
  });
  return new Blob([bytes], { type: "audio/wav" });
}

export async function decodeToWav(blob) {
  const Audio = window.AudioContext || window.webkitAudioContext;
  const context = new Audio();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (decoded.duration > 15.1) throw Error("Recording is too long");
    const offline = new OfflineAudioContext(
      1,
      Math.min(240000, Math.ceil(decoded.duration * 16000)),
      16000,
    );
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    return pcmWav((await offline.startRendering()).getChannelData(0));
  } finally {
    await context.close();
  }
}
