"""Local settings on the Pi itself (secrets and hardware), as opposed to
config.toml in Dropbox which holds everything about the content."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MPV_ARGS = ["--vo=gpu", "--gpu-context=drm", "--hwdec=auto-safe"]


@dataclass
class Settings:
    app_key: str
    refresh_token: str
    folder: str = ""
    cache_dir: Path = Path.home() / ".cache" / "dropbox-signage"
    mpv_args: list[str] = field(default_factory=lambda: list(DEFAULT_MPV_ARGS))
    power_method: str = "black"
    on_command: str = ""
    off_command: str = ""

    @property
    def media_dir(self) -> Path:
        return self.cache_dir / "media"


def load(path: Path) -> Settings:
    with path.open("rb") as f:
        data = tomllib.load(f)
    dropbox = data.get("dropbox", {})
    if not dropbox.get("app_key") or not dropbox.get("refresh_token"):
        raise ValueError(f"{path}: [dropbox] app_key and refresh_token are required "
                         "(run get_refresh_token.py)")
    settings = Settings(app_key=dropbox["app_key"], refresh_token=dropbox["refresh_token"],
                        folder=dropbox.get("folder", ""))
    if "cache_dir" in data.get("paths", {}):
        settings.cache_dir = Path(data["paths"]["cache_dir"]).expanduser()
    settings.mpv_args = data.get("player", {}).get("mpv_args", settings.mpv_args)
    power = data.get("power", {})
    settings.power_method = power.get("method", settings.power_method)
    settings.on_command = power.get("on_command", "")
    settings.off_command = power.get("off_command", "")
    return settings
