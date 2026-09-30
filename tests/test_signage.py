import datetime as dt
import hashlib
import os
import random
import tempfile
import threading
import tomllib
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from signage import playlist, settings as settings_mod, web
from signage.content_config import ConfigError, ContentConfigLoader, parse, parse_ranges
from signage.dropbox_sync import DropboxError, FolderSync, content_hash
from signage.state import State, StateStore, SyncStatus

EXAMPLE = Path(__file__).resolve().parent.parent / "voorbeeld" / "config.toml"


class ConfigTest(unittest.TestCase):
    def test_example_config_parses(self):
        cfg = parse(tomllib.loads(EXAMPLE.read_text()))
        self.assertEqual(cfg.rule_for("Openingstijden.JPG").duration, 20)
        self.assertTrue(cfg.rule_for("oud-logo.png").skip)
        self.assertEqual(cfg.schedule.days["sunday"], [])
        self.assertEqual(cfg.schedule.dates[dt.date(2026, 12, 31)], [(540, 960)])

    def test_defaults(self):
        cfg = parse({})
        self.assertEqual(cfg.image_duration, 10)
        self.assertTrue(cfg.schedule.is_on(dt.datetime(2026, 1, 1, 3, 0)))

    def test_invalid_values(self):
        for data in ({"display": {"order": "size"}}, {"display": {"image_duration": 0}},
                     {"display": {"rotate": 45}}, {"schedule": {"mondag": "on"}},
                     {"files": {"a.jpg": {"from": "morgen"}}}):
            with self.assertRaises(ConfigError, msg=data):
                parse(data)

    def test_ranges(self):
        self.assertEqual(parse_ranges("07:30-12:00, 13:00-24:00"), [(450, 720), (780, 1440)])
        self.assertEqual(parse_ranges("off"), [])
        self.assertEqual(parse_ranges("aan"), [(0, 1440)])
        for bad in ("18:00-07:00", "7-8", "10:75-11:00"):
            with self.assertRaises(ConfigError):
                parse_ranges(bad)

    def test_schedule(self):
        cfg = parse({"schedule": {"enabled": True, "monday": "08:00-18:00", "sunday": "off",
                                  "dates": {"2026-09-28": "off"}}})
        monday = dt.datetime(2026, 9, 21, 8, 0)
        self.assertTrue(cfg.schedule.is_on(monday))
        self.assertFalse(cfg.schedule.is_on(monday.replace(hour=7, minute=59)))
        self.assertFalse(cfg.schedule.is_on(monday.replace(hour=18)))
        self.assertFalse(cfg.schedule.is_on(dt.datetime(2026, 9, 27, 12)))  # sunday
        self.assertTrue(cfg.schedule.is_on(dt.datetime(2026, 9, 22, 3)))  # tuesday not listed
        self.assertFalse(cfg.schedule.is_on(dt.datetime(2026, 9, 28, 12)))  # date override

    def test_loader_keeps_last_good_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            loader = ContentConfigLoader(path)
            path.write_text("[display]\nimage_duration = 7\n")
            self.assertEqual(loader.load().image_duration, 7)
            path.write_text("[display\nkapot")
            os.utime(path, (1, 1))
            self.assertEqual(loader.load().image_duration, 7)
            path.unlink()
            self.assertEqual(loader.load().image_duration, 10)


class PlaylistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for i, name in enumerate(["b.jpg", "A.png", "c.mp4", "notes.txt", "config.toml", ".x.part"]):
            (self.dir / name).write_bytes(b"x")
            os.utime(self.dir / name, (i, i))

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, cfg, today=dt.date(2026, 1, 1)):
        return [s.path.name for s in playlist.build(self.dir, cfg, today, random.Random(1))]

    def test_name_order_and_filtering(self):
        self.assertEqual(self.names(parse({})), ["A.png", "b.jpg", "c.mp4"])

    def test_newest_first(self):
        self.assertEqual(self.names(parse({"display": {"order": "newest"}})), ["c.mp4", "A.png", "b.jpg"])

    def test_durations_and_rules(self):
        cfg = parse({"display": {"image_duration": 8, "max_video_duration": 30},
                     "files": {"b.jpg": {"duration": 3}, "a.png": {"skip": True},
                               "c.mp4": {"from": dt.date(2026, 2, 1)}}})
        slides = playlist.build(self.dir, cfg, dt.date(2026, 1, 1))
        self.assertEqual([(s.path.name, s.kind, s.duration) for s in slides], [("b.jpg", "image", 3)])
        slides = playlist.build(self.dir, cfg, dt.date(2026, 2, 1))
        self.assertEqual(slides[-1].duration, 30)

    def test_interleave(self):
        slides = [playlist.Slide(Path(str(i)), "image", 1) for i in range(5)]
        news = [playlist.Slide(Path("n1"), "image", 1), playlist.Slide(Path("n2"), "image", 1)]
        result = [s.path.name for s in playlist.interleave(slides, news, 2)]
        self.assertEqual(result, ["0", "1", "n1", "2", "3", "n2", "4"])


class FakeClient:
    def __init__(self, files):
        self.files = files  # name -> bytes
        self.downloads = []

    def list_folder(self, path):
        return [{".tag": "file", "name": n, "path_lower": f"{path}/{n}".lower(), "size": len(b),
                 "content_hash": self._hash(b), "server_modified": "2026-01-02T03:04:05Z"}
                for n, b in self.files.items()]

    @staticmethod
    def _hash(data):
        return hashlib.sha256(hashlib.sha256(data).digest()).hexdigest() if data else hashlib.sha256().hexdigest()

    def download(self, path, dest):
        self.downloads.append(path)
        name = path.rsplit("/", 1)[1]
        dest.write_bytes(next(b for n, b in self.files.items() if n.lower() == name))


class SyncTest(unittest.TestCase):
    def test_sync_downloads_only_changes_and_removes_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "media"
            client = FakeClient({"a.jpg": b"one", "B.mp4": b"two"})
            sync = FolderSync(client, "/Scherm/", local)
            self.assertTrue(sync.sync())
            self.assertEqual(sorted(client.downloads), ["/scherm/a.jpg", "/scherm/b.mp4"])
            self.assertEqual((local / "B.mp4").read_bytes(), b"two")
            self.assertEqual(content_hash(local / "a.jpg"), client._hash(b"one"))

            client.downloads.clear()
            self.assertFalse(sync.sync())
            self.assertEqual(client.downloads, [])

            (local / ".manifest.json").unlink()  # lost manifest: hash instead of re-download
            self.assertFalse(sync.sync())
            self.assertEqual(client.downloads, [])

            client.files = {"a.jpg": b"ONE"}
            self.assertTrue(sync.sync())
            self.assertEqual(client.downloads, ["/scherm/a.jpg"])
            self.assertEqual((local / "a.jpg").read_bytes(), b"ONE")
            self.assertFalse((local / "B.mp4").exists())

    def test_switching_folder_clears_old_files_first(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "media"
            FolderSync(FakeClient({"old.jpg": b"1", "same.jpg": b"s"}), "/Present-it/A", local).sync()
            client = FakeClient({"new.jpg": b"2", "same.jpg": b"s"})
            seen_during_download = []
            original = client.download
            client.download = lambda p, dest: (seen_during_download.append(
                sorted(f.name for f in local.iterdir() if not f.name.startswith("."))), original(p, dest))
            FolderSync(client, "/Present-it/B", local).sync()
            self.assertEqual(seen_during_download[0], [])  # old.jpg gone before downloading
            self.assertEqual(sorted(f.name for f in local.iterdir() if not f.name.startswith(".")),
                             ["new.jpg", "same.jpg"])
            client.downloads.clear()
            FolderSync(client, "/present-it/b", local).sync()  # same folder, other case
            self.assertEqual(client.downloads, [])

    def test_root_folder(self):
        self.assertEqual(FolderSync(None, "/", Path("x")).remote_folder, "")
        self.assertEqual(FolderSync(None, "", Path("x")).remote_folder, "")


class StateTest(unittest.TestCase):
    def test_persists_privately_and_notifies(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "state.json"
            store = StateStore(path, State(app_key="default"))
            self.assertFalse(store.get().linked)
            seen = []
            store.on_change(seen.append)
            store.update(refresh_token="tok", folder="/x/y")
            self.assertEqual(seen[-1].folder, "/x/y")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            reloaded = StateStore(path, State(app_key="other", folder="/ignored"))
            self.assertEqual(reloaded.get(), State("default", "tok", "/x/y"))
            self.assertTrue(reloaded.get().configured)

    def test_settings_file_is_optional(self):
        s = settings_mod.load(Path("/nonexistent/settings.toml"))
        self.assertEqual(s.base_folder, "/Mediakranten/Present-it")
        self.assertEqual(s.web_port, 8080)


class FakeDropbox:
    """Stands in for DropboxClient on the setup page."""

    def __init__(self, folders=("Receptie", "Kantine"), existing=(), can_write=True):
        self.folders = list(folders)
        self.existing = set(existing)
        self.can_write = can_write
        self.uploads = []

    def __call__(self, app_key, token):  # used as client_factory
        self.last_credentials = (app_key, token)
        return self

    def list_subfolders(self, path):
        assert path == "/Mediakranten/Present-it", path
        return sorted(self.folders, key=str.lower)

    def upload_if_missing(self, path, content):
        if not self.can_write:
            raise DropboxError("Dropbox 401: missing_scope/files.content.write")
        self.uploads.append((path, content))
        if path in self.existing:
            return False
        self.existing.add(path)
        return True


class SetupWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "state.json")
        self.dropbox = FakeDropbox()
        self.exchanged = []

        def exchange(app_key, code, verifier):
            self.exchanged.append((app_key, code))
            if code.strip() != "goede-code":
                raise DropboxError("Dropbox 400: invalid_grant")
            return "refresh-123"

        self.app = web.SetupApp(self.store, "Mediakranten/Present-it/", SyncStatus(), "KEY",
                                client_factory=self.dropbox, exchange_code=exchange)
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self):
        with urllib.request.urlopen(self.base + "/") as r:
            return r.read().decode()

    def post(self, path, **fields):
        data = urllib.parse.urlencode(fields).encode()
        with urllib.request.urlopen(self.base + path, data=data) as r:  # follows the 303 to /
            return r.read().decode()

    def test_full_setup_flow(self):
        page = self.get()
        self.assertIn("Dropbox koppelen", page)
        self.assertIn("value='KEY'", page)  # app key from settings.toml prefilled

        page = self.post("/link/start", app_key="KEY")
        self.assertIn("https://www.dropbox.com/oauth2/authorize?", page)
        self.assertIn("code_challenge_method=S256", page)

        page = self.post("/link/finish", code="verkeerd")
        self.assertIn("Dat lukte niet", page)
        self.assertFalse(self.store.get().linked)

        page = self.post("/link/finish", code=" goede-code ")
        self.assertIn("Dropbox is gekoppeld", page)
        self.assertEqual(self.store.get(), State("KEY", "refresh-123", ""))
        self.assertNotIn("refresh-123", page)  # token never shown
        self.assertIn("Receptie", page)
        self.assertIn("Kantine", page)

        page = self.post("/folder", name="Receptie")
        self.assertEqual(self.store.get().folder, "/Mediakranten/Present-it/Receptie")
        self.assertEqual(self.dropbox.uploads[0][0], "/Mediakranten/Present-it/Receptie/config.toml")
        tomllib.loads(self.dropbox.uploads[0][1].decode())  # the template is valid TOML
        self.assertIn("Er staat nu een config.toml", page)
        self.assertIn("nu op het scherm", page)
        self.assertIn("Status", page)

        self.dropbox.existing.add("/Mediakranten/Present-it/Kantine/config.toml")
        page = self.post("/folder", name="Kantine")
        self.assertIn("bestaande config.toml", page)
        self.assertEqual(self.store.get().folder, "/Mediakranten/Present-it/Kantine")

    def test_template_config_parses_with_schedule_off(self):
        cfg = parse(tomllib.loads(web.config_template().decode()))
        self.assertFalse(cfg.schedule.enabled)
        self.assertEqual(cfg.files, {})

    def test_rejects_unknown_folder(self):
        self.store.update(app_key="KEY", refresh_token="t")
        page = self.post("/folder", name="../../Prive")
        self.assertIn("bestaat niet", page)
        self.assertEqual(self.store.get().folder, "")
        self.assertEqual(self.dropbox.uploads, [])

    def test_folder_still_chosen_without_write_permission(self):
        self.store.update(app_key="KEY", refresh_token="t")
        self.dropbox.can_write = False
        page = self.post("/folder", name="Kantine")
        self.assertEqual(self.store.get().folder, "/Mediakranten/Present-it/Kantine")
        self.assertIn("files.content.write", page)

    def test_escapes_folder_names(self):
        self.store.update(app_key="KEY", refresh_token="t")
        self.dropbox.folders = ["<script>x</script>"]
        page = self.get()
        self.assertNotIn("<script>x", page)
        self.assertIn("&lt;script&gt;", page)

    def test_finish_without_start(self):
        page = self.post("/link/finish", code="goede-code")
        self.assertIn("Begin opnieuw", page)

    def test_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(self.base + "/nope")
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
