"""Read-only replay inventory. Run with PYTHONPATH=engine; never certifies skill."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from ember.contracts import CycleBundle, SCHEMA_VERSION


def local_asset(root: Path, reference: str) -> Path:
    """Resolve an untrusted bundle URL inside an explicit serving root."""
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc or parts.query or parts.fragment or reference.startswith('/'):
        raise ValueError(f'Expected a local asset reference: {reference}')
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Asset escapes serving root: {reference}')
    return path


def utc_ms(value: str) -> int:
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.utcoffset() is None:
        raise ValueError('Scenario time must have an explicit UTC offset')
    return int(instant.timestamp() * 1000)


def audit(scenario_dir: Path, asset_root: Path, branch: str, profile: str) -> dict:
    cfg = yaml.safe_load((scenario_dir / 'scenario.yaml').read_text())
    scn = cfg['scenario_id']
    start, end = [utc_ms(cfg[k]) for k in ('replay_start_utc', 'replay_end_utc')]
    step = int(cfg['cycle_min']) * 60000
    if step <= 0 or end < start:
        raise ValueError('Invalid scenario replay interval')
    expected = set(range(start, end + 1, step))
    ignition = utc_ms(cfg['t_ignition_utc'])
    index_path = local_asset(asset_root, f'cache/{scn}/{branch}/index.json')
    index = json.loads(index_path.read_text())
    errors, found, profiles, receipts = [], [], set(), []
    if (index['scenario_id'], index['branch_id']) != (scn, branch):
        errors.append('Index scenario/branch identity mismatch')
    wanted_members = 16 if profile == 'dev' else int(cfg['n_members'])
    for entry in index['cycles']:
        k = entry['knowledge_sim_ms']
        found.append(k)
        try:
            path = local_asset(asset_root, entry['bundle'])
            raw = path.read_bytes()
            b = CycleBundle.model_validate_json(raw)
            if b.schema_version != SCHEMA_VERSION:
                raise ValueError('Unsupported schema_version')
            if (b.scenario_id, b.branch_id, b.knowledge_sim_ms) != (scn, branch, k):
                raise ValueError('Index/bundle identity mismatch')
            if any(o.available_sim_ms > k for o in b.observations):
                raise ValueError('Future observation in bundle')
            if any(s.available_sim_ms > k for s in b.scan_events):
                raise ValueError('Future scan in bundle')
            if k >= ignition and b.cycle is None:
                raise ValueError('Missing forecast after ignition')
            if k < ignition and b.cycle is not None:
                raise ValueError('Forecast before configured ignition')
            if b.cycle is not None:
                c = b.cycle
                if (c.scenario_id, c.branch_id, c.knowledge_sim_ms) != (scn, branch, k):
                    raise ValueError('Cycle/bundle identity mismatch')
                profiles.add((c.n_members, c.model_version, c.filter_mode))
                if c.n_members != wanted_members or (profile == 'target' and '+dev' in c.model_version):
                    raise ValueError(f'Wrong member profile: {c.n_members}, {c.model_version}')
                if c.data_through_sim_ms is not None and c.data_through_sim_ms > k:
                    raise ValueError('Future cycle data-through time')
                for kind, ref in c.assets.model_dump().items():
                    asset = local_asset(asset_root, ref)
                    content = asset.read_bytes()
                    if kind == 'arrival_q':
                        if len(content) != 5 * int(cfg['grid']['nx']) * int(cfg['grid']['ny']):
                            raise ValueError('Wrong arrival raster byte count')
                    else:
                        geo = json.loads(content)
                        if geo.get('type') not in ('FeatureCollection', 'Feature', 'Polygon', 'MultiPolygon', 'LineString', 'MultiLineString', 'GeometryCollection'):
                            raise ValueError(f'Invalid GeoJSON type: {kind}')
                    receipts.append(dict(path=ref, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f'{k}: {exc}')
    if found != sorted(set(found)):
        errors.append('Index issues must be unique and increasing')
    missing, extra = sorted(expected - set(found)), sorted(set(found) - expected)
    return dict(audit_version=1, scenario_id=scn, branch_id=branch, requested_profile=profile,
        expected_cycles=len(expected), indexed_cycles=len(found), missing_issues=missing,
        unexpected_issues=extra, errors=errors, observed_profiles=sorted(profiles), assets=receipts,
        index_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(),
        scenario_sha256=hashlib.sha256((scenario_dir / 'scenario.yaml').read_bytes()).hexdigest(),
        replay_complete=not (missing or extra or errors), scientific_acceptance='not_assessed',
        limits=['Local file/schema/availability inventory only', 'Does not verify forecast accuracy, all upstream provenance, signatures, or service delivery'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario-dir', type=Path, required=True)
    p.add_argument('--asset-root', type=Path, required=True)
    p.add_argument('--branch', default='main')
    p.add_argument('--profile', choices=('dev', 'target'), required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    report = audit(a.scenario_dir, a.asset_root, a.branch, a.profile)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    # A report is an immutable run artifact; never replace earlier evidence.
    with a.out.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: report[k] for k in ('indexed_cycles','expected_cycles','replay_complete','scientific_acceptance')}))
    raise SystemExit(0 if report['replay_complete'] else 1)


if __name__ == '__main__':
    main()
