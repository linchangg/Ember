#!/usr/bin/env python3
"""Generate the normalized 64x64 FM1 integration bundle in ENGINE_BUILD_PLAN.md."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'fixtures/generated/fm1');args=p.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    n=64;size=n*n
    def f64(path,values):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(struct.pack('<'+'d'*size,*values))
    f64(out/'elevation.f64le',[0.]*size)
    (out/'fuel_id.u16le').write_bytes(struct.pack('<'+'H'*size,*([1]*size)))
    for name in ['valid.u8','burnable.u8']:(out/name).write_bytes(bytes([1])*size)
    phi=[math.hypot((c+.5)*10-320,(r+.5)*10-320)-45 for r in range(n) for c in range(n)]
    assert min(phi)<0<max(phi) and all(v!=0 for v in phi)
    f64(out/'initial_phi.f64le',phi)
    shutil.copyfile(ROOT/'docs/spec/fuel_models.csv',out/'fuel_models.csv')
    for frame in ['000000','000600']:
        for name,value in [('wind_east',0),('wind_north',0),('m1',.05),('m10',.07),('m100',.09),('mlh',.60),('mlw',.90)]:
            f64(out/'weather'/frame/(name+'.f64le'),[value]*size)
    manifest={
      'schema_version':1,'incident_id':'synthetic-fm1','start_utc':'2026-01-01T00:00:00Z',
      'grid':{'width':n,'height':n,'cell_size_m':10.,'x0_m':0.,'y0_m':0.,'crs':'LOCAL_METRIC_TEST'},
      'landscape':{'elevation':'elevation.f64le','fuel_id':'fuel_id.u16le','valid':'valid.u8','burnable':'burnable.u8','fuel_catalog':'fuel_models.csv'},
      'initial':{'phi':'initial_phi.f64le','perimeter_activity':'assume_entire_boundary_active'},
      'environment':{'wind_reference':'midflame_horizontal_heading_surface_speed','interpolation':'hold_right_continuous',
        'frames':[{'time_s':t,'observed_or_valid_utc':stamp,'available_utc':'2026-01-01T00:00:00Z','directory':'weather/'+frame}
                  for t,stamp,frame in [(0.,'2026-01-01T00:00:00Z','000000'),(600.,'2026-01-01T00:10:00Z','000600')]],'coverage_end_s':3600.},
      'simulation':{'horizons_s':[300.,600.,1800.,3600.],'cfl':.4,'max_step_s':30.,'wind_limit':'effective_0p9_ir',
                    'ellipse_max_lb':8.,'reinitialize':False,'solver':'level_set_lf_euler_v1'}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    checksums=[{'path':str(path.relative_to(out)),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(out.rglob('*')) if path.is_file() and path.name!='checksums.json']
    (out/'checksums.json').write_text(json.dumps(checksums,indent=2)+'\n')
    print(f'Created normalized integration fixture: {out} ({len(checksums)} files plus checksum inventory)')


if __name__=='__main__':main()
