import datetime as dt
import hashlib
import os
import random
import tempfile
import tomllib
import unittest
from pathlib import Path

from signage import playlist
from signage.content_config import ConfigError, ContentConfigLoader, parse, parse_ranges
from signage.dropbox_sync import FolderSync, content_hash

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

    def test_root_folder(self):
        self.assertEqual(FolderSync(None, "/", Path("x")).remote_folder, "")
        self.assertEqual(FolderSync(None, "", Path("x")).remote_folder, "")


if __name__ == "__main__":
    unittest.main()
