/** Phase A transport checks; forecast contents are checked by check_hazard_frontend.mjs. */
import assert from 'node:assert/strict';
const base = process.argv[2] ?? 'http://127.0.0.1:8790/';
const origin = process.argv[3] ?? 'http://localhost:5173';
const manifest = new URL('static/rust-fixture/manifest.json', base);
const get = await fetch(manifest, {headers: {Origin: origin}});
assert.equal(get.status, 200);
assert.equal(get.headers.get('access-control-allow-origin'), origin);
assert.equal((await get.json()).scenario_id, 'rust-fixture');
const head = await fetch(manifest, {method: 'HEAD', headers: {Origin: origin}});
assert.equal(head.status, 200);
assert.equal((await head.arrayBuffer()).byteLength, 0);
const preflight = await fetch(manifest, {method: 'OPTIONS', headers: {Origin: origin}});
assert.equal(preflight.status, 204);
assert.equal(preflight.headers.get('access-control-allow-methods'), 'GET, HEAD, OPTIONS');
assert.equal((await fetch(manifest, {headers: {Origin: 'https://unrelated.example'}})).status, 403);
assert.equal((await fetch(manifest, {method: 'POST'})).status, 501);
assert.equal((await fetch(new URL('missing-asset.bin', base))).status, 404);
assert.equal((await fetch(new URL('.env', base))).status, 403);
console.log(JSON.stringify({transport: 'passed', cors_origin: origin,
    read_only: true, missing_asset_is_error: true, forecast_validation: 'run check_hazard_frontend separately'}, null, 2));
