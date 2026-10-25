from email.message import Message
from ember.hazard.fixture_server import FixtureHandler


def handler(root, path, origin=None):
    h = FixtureHandler.__new__(FixtureHandler)
    h.root = root.resolve()
    h.directory = str(root)
    h.path = path
    h.allowed_origins = frozenset({"http://localhost:5173"})
    h.headers = Message()
    if origin:
        h.headers["Origin"] = origin
    h.errors = []
    h.send_error = lambda code, message: h.errors.append(code)
    return h


def test_read_paths_accept_only_explicit_origin(tmp_path):
    assert handler(
        tmp_path, "/static/rust-fixture/manifest.json", "http://localhost:5173"
    )._permitted()
    h = handler(tmp_path, "/", "https://unrelated.example")
    assert not h._permitted() and h.errors == [403]


def test_traversal_and_hidden_files_are_blocked(tmp_path):
    for path in ["/static/%2e%2e/secret", "/.env", "/static/.hidden"]:
        h = handler(tmp_path, path)
        assert not h._permitted() and h.errors == [403]


def test_symlinks_outside_fixture_are_blocked(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path)
    h = handler(root, "/escape/secret")
    assert not h._permitted() and h.errors == [403]


def test_missing_path_still_uses_normal_http_not_a_mock(tmp_path):
    assert handler(tmp_path, "/does-not-exist")._permitted()
