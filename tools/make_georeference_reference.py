"""Generate independent PROJ golden coordinates; never run automatically by the UI."""
import argparse
import hashlib
import json
from pathlib import Path
from pyproj import Transformer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    to_utm = Transformer.from_crs('EPSG:4326', 'EPSG:32611', always_xy=True)
    to_geo = Transformer.from_crs('EPSG:32611', 'EPSG:4326', always_xy=True)
    x0, y0 = to_utm.transform(*manifest['grid']['corners']['tl'])
    variants = [manifest['grid'], dict(manifest['grid'], nx=340, ny=340, cell_m=90)]
    cases = []
    for grid in variants:
        # This reference uses the fixture's 30.6 km domain; the target grid has the
        # same extent at 90 m. Refuse to silently regenerate it for another extent.
        for name, dx, dy in [('tr',grid['nx']*grid['cell_m'],0),('bl',0,-grid['ny']*grid['cell_m']),
                             ('br',grid['nx']*grid['cell_m'],-grid['ny']*grid['cell_m'])]:
            x, y = to_utm.transform(*grid['corners'][name])
            if ((x-x0-dx)**2+(y-y0-dy)**2)**.5 > 1:
                raise ValueError('Reference domain does not match projected grid')
        samples = []
        for row in [0, grid['ny']//4, grid['ny']//2, 3*grid['ny']//4, grid['ny']-1]:
            for col in [0, grid['nx']//4, grid['nx']//2, 3*grid['nx']//4, grid['nx']-1]:
                lon,lat = to_geo.transform(x0+(col+.5)*grid['cell_m'],y0-(row+.5)*grid['cell_m'])
                samples.append(dict(row=row,col=col,lon=lon,lat=lat))
        cases.append(dict(grid=grid,samples=samples))
    result = dict(generator='pyproj/PROJ; independent from JavaScript projection implementation',
                  manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),cases=cases)
    args.out.write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
