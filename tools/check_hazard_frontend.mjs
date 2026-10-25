/** Read exactly the manifest/index/binary contracts Linda's FileSource will consume. */
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

export function checkWireConstants(source) {
  for (const [name, expected] of Object.entries({QUANTILES:[.1,.3,.5,.7,.9],LEADS_MIN:Array.from({length:12},(_,i)=>(i+1)*30)})) {
    const expression=source.match(new RegExp(`export\\s+const\\s+${name}\\s*=\\s*(\\[[\\s\\S]*?\\])\\s+as\\s+const`));
    assert(expression,`Cannot read frontend ${name}; reconcile shared contracts explicitly`);
    assert.deepEqual(JSON.parse(expression[1]),expected,`Frontend ${name} changed; update adapter, publisher and decoder together`);
  }
}

export async function checkFixture(base,scenario='rust-fixture') {
  checkWireConstants(await readFile(new URL('../web/src/contracts.ts',import.meta.url),'utf8'));
  const remote=/^https?:\/\//.test(base);
  const bytes=async relative=>{
    assert(!relative.startsWith('/')&&!relative.split('/').includes('..'),'asset path must be relative and confined');
    if(remote){const response=await fetch(new URL(relative,base.endsWith('/')?base:base+'/'));assert(response.ok,`${relative}: HTTP ${response.status}`);return new Uint8Array(await response.arrayBuffer());}
    return new Uint8Array(await readFile(path.join(base,relative)));
  };
  const json=async relative=>JSON.parse(new TextDecoder().decode(await bytes(relative)));
  const manifest=await json(`static/${scenario}/manifest.json`),index=await json(`cache/${scenario}/main/index.json`);
  assert.equal(manifest.scenario_id,scenario);assert.deepEqual(manifest.quantiles,[.1,.3,.5,.7,.9]);
  assert.equal(manifest.grid.crs,'EPSG:32611');const cells=manifest.grid.nx*manifest.grid.ny;
  assert(cells>0);for(const key of ['tl','tr','br','bl']){const [lon,lat]=manifest.grid.corners[key];assert(Math.abs(lon)<=180&&Math.abs(lat)<=90)}
  let last=-Infinity,cycles=0,preignition=0,observations=0,alerts=0;
  for(const entry of index.cycles){
    assert(Number.isSafeInteger(entry.knowledge_sim_ms)&&entry.knowledge_sim_ms>last);last=entry.knowledge_sim_ms;
    const bundle=await json(entry.bundle);assert.equal(bundle.schema_version,2);assert.equal(bundle.scenario_id,scenario);assert.equal(bundle.knowledge_sim_ms,last);
    for(const record of bundle.observations){assert(record.available_sim_ms<=last&&record.observed_sim_ms<=record.available_sim_ms);assert.equal(record.provenance,'simulated');observations++;}
    if(!bundle.cycle){preignition++;continue;}cycles++;
    const cycle=bundle.cycle;assert.equal(cycle.knowledge_sim_ms,last);assert(cycle.data_through_sim_ms<=last);assert(cycle.ess>0&&cycle.ess<=cycle.n_members+1e-8);
    const raster=await bytes(cycle.assets.arrival_q);assert.equal(raster.length,5*cells);
    let decodedCount=0;
    for(let cell=0;cell<cells;cell++){
      let previous=0;
      for(let q=0;q<5;q++){const value=raster[q*cells+cell];assert(value===255||value<=Math.ceil(cycle.horizon_min/2));assert(value>=previous,'arrival quantiles must be ordered');previous=value;
        // Actual §06 bitmap decoding rule at +120 min.
        if(value!==255&&value<=60)decodedCount++;
      }
    }
    assert(decodedCount>=0);
    const perimeter=await json(cycle.assets.perimeter),isochrones=await json(cycle.assets.isochrones);
    assert.equal(perimeter.type,'FeatureCollection');assert.deepEqual(perimeter.features.map(f=>f.id),['burned','front']);assert.equal(isochrones.type,'FeatureCollection');
    const ranks=[];
    for(const zone of bundle.zone_risk){assert.equal(zone.p_by_lead.length,12);let prior=0;for(const p of zone.p_by_lead){assert(Number.isFinite(p)&&p>=0&&p<=1&&p+1e-12>=prior);prior=p;}ranks.push(zone.rank)}
    assert.deepEqual(ranks.slice().sort((a,b)=>a-b),Array.from({length:ranks.length},(_,i)=>i+1));
    for(const alert of bundle.alerts){assert(alert.text.length<=300);assert(alert.voice_script.trim().split(/\s+/).length<=110);alerts++;}
  }
  return {scenario,forecast_cycles:cycles,preignition_cycles:preignition,observations,alerts,raster_bytes:5*cells,frontend_contract:'passed',transport:remote?'HTTP':'filesystem'};
}
if(process.argv[1]&&import.meta.url===new URL(`file://${path.resolve(process.argv[1])}`).href){
  const base=process.argv[2];if(!base)throw Error('usage: node tools/check_hazard_frontend.mjs DIRECTORY_OR_HTTP_BASE [scenario]');
  console.log(JSON.stringify(await checkFixture(base,process.argv[3]||'rust-fixture'),null,2));
}
