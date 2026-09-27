# Dropbox op het scherm

Toont de foto's en video's uit een Dropbox-map op het scherm dat aan een
Raspberry Pi 3 hangt. Wie de map beheert, stuurt alles aan via Dropbox:
bestanden toevoegen of weghalen, en bijzonderheden regelen in `config.toml`.

## Hoe het werkt

```
Dropbox-map ──(elke 5 min, alleen wijzigingen)──▶ lokale kopie op de Pi ──▶ mpv ──▶ HDMI-scherm
                    config.toml ───────────────▶ duur, volgorde, schema, uitzonderingen
```

- **Offline bestendig:** er wordt afgespeeld van de lokale kopie. Valt het
  internet weg, dan blijft het scherm gewoon draaien.
- **Licht genoeg voor een Pi 3:** mpv speelt direct op het scherm af (geen
  desktop of browser nodig) en gebruikt de hardware-videodecoder.
- **Fouten in config.toml** zetten het scherm niet op zwart: de vorige
  instellingen blijven gelden en de fout komt in het logboek.

Ondersteund: `jpg jpeg png gif webp bmp` en `mp4 m4v mov mkv avi webm`.
Andere bestanden worden genegeerd. Houd video's voor de Pi 3 op maximaal
1080p H.264 (zoals de meeste telefoons en camera's opnemen).

## Installatie

1. **Pi voorbereiden.** Installeer *Raspberry Pi OS Lite (Bookworm)* met
   Raspberry Pi Imager, met wifi en SSH ingesteld. Controleer de tijdzone
   (`sudo raspi-config` → Localisation → Timezone), anders klopt het
   aan/uit-schema niet.

2. **Dropbox-app maken** op <https://www.dropbox.com/developers/apps>:
   - *Scoped access*, en kies **App folder** (de Pi ziet dan alleen
     `Dropbox/Apps/<appnaam>`, de veiligste keuze).
   - Tabblad *Permissions*: vink `files.metadata.read` en
     `files.content.read` aan en klik *Submit*.
   - Noteer de **App key**.

3. **Code op de Pi zetten en installeren:**
   ```sh
   git clone <deze repo> && cd <repo>/dropbox-signage
   ./install.sh
   ```

4. **Pi koppelen aan Dropbox:**
   ```sh
   python3 get_refresh_token.py <app key>
   ```
   Volg de stappen en zet de uitkomst in `settings.toml`. Pas daar ook
   `folder` en de aan/uit-methode aan (zie hieronder), en herstart:
   ```sh
   sudo systemctl restart dropbox-signage
   ```

5. **Map vullen:** zet foto's, video's en een `config.toml` (voorbeeld in
   [`voorbeeld/config.toml`](voorbeeld/config.toml)) in de Dropbox-map.

Logboek bekijken: `journalctl -u dropbox-signage -f`

## config.toml (in Dropbox)

Zie [`voorbeeld/config.toml`](voorbeeld/config.toml) voor alle opties met
uitleg. Samengevat:

| Onderdeel | Wat |
|---|---|
| `[display]` | duur per foto, volgorde (naam / nieuwste / willekeurig), geluid, max. videoduur, beeldvullend of niet, draaien |
| `[sync]` | hoe vaak Dropbox gecontroleerd wordt |
| `[files."naam.jpg"]` | per bestand: andere `duration`, `skip`, of alleen tonen `from`/`until` een datum |
| `[schedule]` | automatisch aan/uit per weekdag, met uitzonderingen per datum in `[schedule.dates]` |
| `[news]` | gereserveerd voor nieuwsberichten (latere versie) |

## Scherm automatisch aan en uit

Buiten de tijden in `[schedule]` gaat het scherm uit. *Hoe* dat gebeurt hangt
van het scherm af en stel je in `settings.toml` op de Pi in (`[power] method`):

| Methode | Wanneer |
|---|---|
| `black` | Standaard. Toont zwart; het scherm zelf blijft aan. |
| `cec` | TV's: zet de TV in stand-by via HDMI-CEC en weer aan. Meestal de beste keuze voor een TV. Zet CEC aan in het TV-menu (heet vaak Anynet+, Bravia Sync, SimpLink). |
| `vcgencmd` | Monitoren: zet het HDMI-signaal uit zodat de monitor gaat slapen. Werkt alleen met de fkms-driver (`dtoverlay=vc4-fkms-v3d` in `/boot/firmware/config.txt`). |
| `command` | Eigen commando's in `on_command` / `off_command`. |

## Nieuws (latere versie)

De code is erop voorbereid: `playlist.interleave()` voegt extra dia's toe na
elke *n* foto's/video's, en `[news]` in `config.toml` wordt al ingelezen. Een
nieuwsbron hoeft alleen berichten van de API op te halen, ze als afbeelding
te renderen (bv. met Pillow) en als `Slide` aan te leveren; de speler en de
rest blijven ongewijzigd.

## Ontwikkelen

```sh
python3 -m unittest discover -s tests
```

Lokaal draaien (met desktop, in een venster): zet in `settings.toml`
`mpv_args = []` en start `python3 -m signage.main`.
