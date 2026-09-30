"""Entry point: setup page, background Dropbox sync, and playback on screen."""

import argparse
import datetime as dt
import logging
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import playlist, settings as settings_mod, web
from .content_config import CONFIG_FILENAME, ContentConfigLoader
from .dropbox_sync import DropboxClient, FolderSync
from .player import Mpv
from .power import ScreenPower
from .state import State, StateStore, SyncStatus

log = logging.getLogger("signage")


class SyncThread(threading.Thread):
    """Keeps the local copy in step with the chosen Dropbox folder."""

    def __init__(self, store: StateStore, media_dir: Path, config_loader: ContentConfigLoader,
                 status: SyncStatus, client_factory=DropboxClient):
        super().__init__(daemon=True, name="dropbox-sync")
        self.store = store
        self.media_dir = media_dir
        self.config_loader = config_loader
        self.status = status
        self.client_factory = client_factory
        self.wake = threading.Event()
        self._client = None
        self._client_key = None
        store.on_change(lambda _state: self.wake.set())  # new link or folder: sync right away

    def _client_for(self, state: State):
        key = (state.app_key, state.refresh_token)
        if key != self._client_key:
            self._client, self._client_key = self.client_factory(*key), key
        return self._client

    def sync_once(self) -> None:
        state = self.store.get()
        if not state.configured:
            return
        try:
            folder_sync = FolderSync(self._client_for(state), state.folder, self.media_dir)
            if folder_sync.sync():
                log.info("Dropbox folder updated")
            self.status.last_sync = dt.datetime.now()
            self.status.last_error = ""
        except Exception as e:  # network down, Dropbox hiccup: keep showing what we have
            log.error("Dropbox sync failed: %s", e)
            self.status.last_error = str(e)
        self.status.file_count = len(playlist.media_files(self.media_dir))

    def run(self) -> None:
        while True:
            self.sync_once()
            self.wake.wait(self.config_loader.current.sync_interval)
            self.wake.clear()


def setup_message(url: str) -> str:
    return f"Dit scherm is nog niet ingesteld.\n\nGa op een laptop of telefoon naar\n{url}"


def run(settings: settings_mod.Settings) -> None:
    media_dir = settings.media_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(settings.state_file, State(settings.app_key, settings.refresh_token,
                                                  settings.folder))
    config_loader = ContentConfigLoader(media_dir / CONFIG_FILENAME)
    status = SyncStatus()
    SyncThread(store, media_dir, config_loader, status).start()
    web.serve(web.SetupApp(store, settings.base_folder, status, settings.app_key), settings.web_port)

    player = Mpv(settings.mpv_args, str(Path(tempfile.gettempdir()) / "dropbox-signage-mpv.sock"))
    power = ScreenPower(settings.power_method, settings.on_command, settings.off_command)
    screen_on = None

    try:
        while True:
            state, version = store.get(), store.version
            if not state.configured:
                if screen_on is not True:
                    power.on()
                    screen_on = True
                player.show_message(setup_message(f"http://{web.local_ip()}:{settings.web_port}"))
                time.sleep(2)
                continue

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
                name = state.folder.rsplit("/", 1)[-1]
                player.show_message(f"{name}\n\n" + (
                    f"Kan Dropbox niet bereiken:\n{status.last_error}" if status.last_error else
                    "Bestanden worden opgehaald…" if status.last_sync is None else
                    "Deze map bevat nog geen foto's of video's."))
                time.sleep(2)
                continue

            player.apply_settings(cfg.video_sound, cfg.scaling, cfg.rotate)
            for slide in slides:
                # A new presentation, config or schedule takes effect at the next slide.
                if (store.version != version or config_loader.load() is not cfg
                        or not cfg.schedule.is_on(dt.datetime.now())):
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
