# Linien-Segelflugsimulator

Ein eigenständiger Retro-Segelflugsimulator in Python, angelehnt an die Liniengrafik früher Heimcomputer-Flugsimulatoren. Das Programm verbindet eine bewusst einfache Darstellung mit nachvollziehbarer Segelflugphysik, Windenstart, Thermikflug und Landetraining.

Der Simulator besteht aus einer einzigen Python-Datei. Sämtliche Grafiken und Geräusche werden während des Programmablaufs erzeugt; zusätzliche Grafik- oder Audiodateien sind nicht erforderlich.

## Funktionen

- Retro-3D-Liniengrafik mit einem 10-m-Bodengitter
- zwei unterscheidbare Flugplätze: **NORDPLATZ** und **WESTFELD**
- Windenstart an beiden Flugplätzen mit sichtbarem Schleppseil
- manuelles Ausklinken; ein vergessenes Seil beeinflusst den weiteren Flug
- ASK-21-nahe Trimmung und Segelflugpolare
- Strömungsabriss unter ungefähr 65 km/h
- mehrere breite, mit dem Wind versetzte Thermikfelder
- drei Wetterstufen: **RUHIG**, **NORMAL** und **STARK**
- unsichtbare Thermik im Realmodus und sichtbare Thermiksäulen im Lernmodus
- Kornfelder und Wolken als natürliche Hinweise auf mögliche Thermik
- Thermikeffekt auf die Tragflächen beim Einflug in den Aufwind
- virtueller Haubenfaden für koordinierten Kurvenflug
- Fahrtmesser, Höhenmesser, Variometer, Kompass und künstlicher Horizont
- dynamischer Variometerton und geschwindigkeitsabhängiges Strömungsgeräusch
- reguläre Landungen auf beiden Flugplätzen und Außenlandungen im Gelände
- ein- und ausblendbare dreidimensionale Flugspur
- Thrustmaster-Joysticksteuerung, Coolie-Hat und Tastatursteuerung
- skalierbares Fenster und Vollbildmodus

## Voraussetzungen

- Python 3
- [`pygame-ce`](https://pyga.me/)

### Linux

Installation:

```bash
python3 -m pip install pygame-ce
```

Start:

```bash
python3 linien_Segelflugsimulator.py
```

### Windows

1. [Python für Windows](https://www.python.org/downloads/windows/) installieren. Während der Installation die Option **Add Python to PATH** aktivieren.
2. Die Eingabeaufforderung oder PowerShell im Ordner des Simulators öffnen.
3. `pygame-ce` installieren:

```powershell
py -m pip install pygame-ce
```

4. Den Simulator starten:

```powershell
py linien_Segelflugsimulator.py
```

Falls der Befehl `py` nicht verfügbar ist, kann stattdessen `python` verwendet werden:

```powershell
python -m pip install pygame-ce
python linien_Segelflugsimulator.py
```

## Start

Beim Programmstart steht das Segelflugzeug am **NORDPLATZ** bereit. Mit `F5` oder `G` wird die Winde gestartet. Das Seil muss während des Steigflugs mit der Leertaste selbst ausgeklinkt werden.

## Bedienung

| Taste | Funktion |
|---|---|
| `W` / `S` | Höhenruder |
| `A` / `D` | Querruder |
| `Q` / `E` | Seitenruder |
| `+` / `-` | Störklappen |
| `J` / `L` | Blick nach links/rechts |
| `F4` | Nach einer Flugplatzlandung neuen Windenstart vorbereiten |
| `F5` oder `G` | Windenstart auslösen |
| `Leertaste` | Schleppseil ausklinken |
| `T` | Thermik zwischen Real- und Lernmodus umschalten |
| `F6` | Wetterstufe wechseln |
| `F7` | Geflogene Flugspur ein-/ausblenden |
| `V` | Ton ein-/ausschalten |
| `P` | Pause |
| `R` | Sicherer Neustart am NORDPLATZ |
| `H` oder `F1` | Anleitung ein-/ausblenden |
| `F2` | Rohe Joystick-Achsenwerte anzeigen |
| `F11` oder `Alt`+`Enter` | Vollbild ein-/ausschalten |
| `Esc` | Vollbild verlassen, danach Programm beenden |

### Joystick

Beim Thrustmaster-Joystick steuern Knüppel, Drehachse und Schubhebel Quer-, Höhen- und Seitenruder sowie die Störklappen. Der Coolie-Hat verändert die Blickrichtung.

- Joysticktaste `0`: Seil ausklinken
- Joysticktaste `1`: Windenstart auslösen

Da sich die Achsbelegung je nach Modell und Treiber unterscheiden kann, stehen die Zuordnungen gut sichtbar am Anfang der Python-Datei:

```python
JOY_AXIS_QUERRUDER = 0
JOY_AXIS_HOEHENRUDER = 1
JOY_AXIS_SEITENRUDER = 2
JOY_AXIS_HEBEL = 3

JOY_INVERT_QUER = False
JOY_INVERT_HOEHE = False
JOY_INVERT_SEITE = False
JOY_INVERT_HEBEL = True
```

Mit `F2` lassen sich die Rohwerte kontrollieren. Eine falsch laufende Achse kann durch Ändern des zugehörigen `JOY_INVERT_...`-Wertes zwischen `False` und `True` umgekehrt werden.

## Flugaufgabe

Das Segelflugzeug startet per Winde am NORDPLATZ. Anschließend können Thermiken gesucht, zentriert und zum Höhengewinn genutzt werden. Ziel ist beispielsweise der Überlandflug zum WESTFELD mit anschließender Landung. Nach einer erfolgreichen Landung bereitet `F4` dort einen neuen Windenstart für den Rückflug vor.

Außerhalb der Landebahnen ist eine sichere Außenlandung möglich. Zu schnelles, zu steiles oder stark geneigtes Aufsetzen führt dagegen zu einer Bruchlandung.

## Thermik und Wetter anpassen

Die Startwetterlage kann am Anfang der Datei geändert werden:

```python
WETTER_START = "NORMAL"
```

Mögliche Werte sind `"RUHIG"`, `"NORMAL"` und `"STARK"`. Wind, Thermikstärke und Thermikbreite können außerdem im Wörterbuch `WETTER_PROFILE` angepasst werden. Position, Grundstärke, Radius und Obergrenze der einzelnen Aufwinde stehen in `THERMIK_GRUNDDATEN`.

## Projektziel

Der Linien-Segelflugsimulator ist kein professioneller Flugsimulator. Er soll die Grundlagen des Segelflugs in einer einfachen und gut verständlichen Umgebung erlebbar machen: Fahrt halten, koordiniert kurven, Thermik finden, Windenstarts durchführen und sicher landen.
