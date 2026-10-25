"""Regression gates for incomplete/mislabelled/unavailable replay artifacts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import yaml
from release_audit import audit, local_asset

REPO = Path(__file__).resolve().parents[2]


class ReplayAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bundles = sorted((REPO / 'fixtures/cache/fixture/main').glob('*/bundle.json'))
        self.bundle = next(json.loads(p.read_text()) for p in bundles
                           if json.loads(p.read_text())['cycle'] is not None)
        self.k = self.bundle['knowledge_sim_ms']
        self.path = self.root / f'cache/fixture/main/{self.k}/bundle.json'
        self.path.parent.mkdir(parents=True)
        self.cfg = dict(scenario_id='fixture', cycle_min=5, n_members=128,
            replay_start_utc=self.iso(self.k), replay_end_utc=self.iso(self.k),
            t_ignition_utc=self.iso(self.k), grid=dict(nx=120, ny=120))
        for ref in self.bundle['cycle']['assets'].values():
            dest = self.root / ref
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((REPO / 'fixtures' / ref).read_bytes())
        self.bundle['cycle'].update(n_members=16, model_version='rust-levelset-pf-1+dev-n16')
        self.entries = [dict(knowledge_sim_ms=self.k, bundle=str(self.path.relative_to(self.root)))]

    @staticmethod
    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()

    def run_audit(self, profile='dev'):
        (self.root / 'scenario.yaml').write_text(yaml.safe_dump(self.cfg))
        self.path.write_text(json.dumps(self.bundle))
        (self.path.parent.parent / 'index.json').write_text(json.dumps(dict(
            scenario_id='fixture', branch_id='main', cycles=self.entries)))
        return audit(self.root, self.root, 'main', profile)

    def test_complete_local_replay_is_not_scientific_acceptance(self):
        r = self.run_audit()
        self.assertTrue(r['replay_complete'], r['errors'])
        self.assertEqual(r['scientific_acceptance'], 'not_assessed')
        self.assertEqual(len(r['assets']), 3)

    def test_missing_issue_blocks_complete_replay(self):
        self.cfg['replay_end_utc'] = self.iso(self.k + 300000)
        r = self.run_audit()
        self.assertFalse(r['replay_complete'])
        self.assertEqual(r['missing_issues'], [self.k + 300000])

    def test_development_run_cannot_pass_target(self):
        r = self.run_audit('target')
        self.assertFalse(r['replay_complete'])
        self.assertIn('Wrong member profile', str(r['errors']))

    def test_unavailable_observation_blocks_release(self):
        self.assertTrue(self.bundle['observations'])
        self.bundle['observations'][0]['available_sim_ms'] = self.k + 1
        r = self.run_audit()
        self.assertIn('Future observation', str(r['errors']))

    def test_truncated_raster_blocks_release(self):
        (self.root / self.bundle['cycle']['assets']['arrival_q']).write_bytes(b'bad')
        r = self.run_audit()
        self.assertIn('byte count', str(r['errors']))

    def test_missing_asset_blocks_release(self):
        (self.root / self.bundle['cycle']['assets']['perimeter']).unlink()
        self.assertFalse(self.run_audit()['replay_complete'])

    def test_duplicate_issue_blocks_release(self):
        self.entries.append(self.entries[0])
        self.assertIn('unique', str(self.run_audit()['errors']))

    def test_bundle_identity_must_match_index(self):
        self.bundle['branch_id'] = 'other'
        self.assertIn('identity mismatch', str(self.run_audit()['errors']))

    def test_path_escape_and_external_urls_are_rejected(self):
        for ref in ('../outside', 'https://example.com/a', '/absolute', 'file.bin?token=x'):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                local_asset(self.root, ref)
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            local_asset(self.root, 'escape/outside')


if __name__ == '__main__':
    unittest.main()
