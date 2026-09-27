"""Entry point: sync Dropbox in the background, play the folder on screen."""

import argparse
import datetime as dt
import logging
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import playlist, settings as settings_mod
from .content_config import CONFIG_FILENAME, ContentConfigLoader
from .dropbox_sync import DropboxClient, FolderSync
from .player import Mpv
from .power import ScreenPower

log = logging.getLogger("signage")


class SyncThread(threading.Thread):
    def __init__(self, folder_sync: FolderSync, config_loader: ContentConfigLoader):
        super().__init__(daemon=True, name="dropbox-sync")
        self.folder_sync = folder_sync
        self.config_loader = config_loader
        self.wake = threading.Event()

    def run(self) -> None:
        while True:
            try:
                if self.folder_sync.sync():
                    log.info("Dropbox folder updated")
            except Exception as e:  # network down, Dropbox hiccup: keep showing what we have
                log.error("Dropbox sync failed: %s", e)
            self.wake.wait(self.config_loader.current.sync_interval)
            self.wake.clear()


def run(settings: settings_mod.Settings) -> None:
    media_dir = settings.media_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    config_loader = ContentConfigLoader(media_dir / CONFIG_FILENAME)
    client = DropboxClient(settings.app_key, settings.refresh_token)
    SyncThread(FolderSync(client, settings.folder, media_dir), config_loader).start()

    player = Mpv(settings.mpv_args, str(Path(tempfile.gettempdir()) / "dropbox-signage-mpv.sock"))
    power = ScreenPower(settings.power_method, settings.on_command, settings.off_command)
    screen_on = None

    try:
        while True:
            cfg = config_loader.load()
            should_be_on = cfg.schedule.is_on(dt.datetime.now())
            if should_be_on != screen_on:
                if should_be_on:
                    power.on()
                else:
                    player.stop()
                    power.off()
                screen_on = should_be_on
            if not screen_on:
                time.sleep(30)
                continue

            slides = playlist.build(media_dir, cfg, dt.date.today())
            if not slides:
                log.info("Nothing to show yet, waiting for files")
                player.stop()
                time.sleep(10)
                continue

            player.apply_settings(cfg.video_sound, cfg.scaling, cfg.rotate)
            for slide in slides:
                # A changed config or schedule takes effect at the next slide.
                if config_loader.load() is not cfg or not cfg.schedule.is_on(dt.datetime.now()):
                    break
                if not slide.path.exists():  # removed by the sync meanwhile
                    continue
                if player.play(slide) == "error":
                    time.sleep(1)  # avoid a tight loop when every file is broken
    finally:
        player.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Show a Dropbox folder on the connected screen")
    parser.add_argument("--settings", type=Path,
                        default=Path(__file__).resolve().parent.parent / "settings.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Let systemd's stop (SIGTERM) run the cleanup in run()'s finally block.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    run(settings_mod.load(args.settings))


if __name__ == "__main__":
    main()
