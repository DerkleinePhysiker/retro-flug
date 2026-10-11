#!/usr/bin/env python3
"""
Linien-Actionflugsimulator im Stil eines raeumlichen River-Raid-Nachfolgers.

Der Simulator verwendet die Liniengrafik, Cockpitdarstellung und grundlegende
Steuerung des Linien-Segelflugsimulators, ist aber ein eigenstaendiges Programm:
Jetstart, gewundener Canyon, Flugdrohne, Wasserziele, Bordkanone, Rueckflug und
Missionswertung. Grafik und Effekttoene werden zur Laufzeit erzeugt. Drei
optionale klassische WAV-Dateien koennen neben diesem Programm liegen.

Installation:
    python3 -m pip install pygame-ce

Start:
    python3 linien_Actionflugsimulator.py

Die JOY_AXIS- und JOY_INVERT-Konstanten unten ggf. an das eigene
Thrustmaster-Modell anpassen. Im laufenden Programm zeigt F2 die Rohwerte.
"""

import math
import os
import random
import sys
from array import array
from dataclasses import dataclass

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

try:
    import pygame
except ImportError:
    print("pygame-ce fehlt. Installation: python3 -m pip install pygame-ce")
    raise SystemExit(1)


# ---------- Joystick-Zuordnung (bei Bedarf hier aendern) -----------------
JOY_AXIS_QUERRUDER = 0
JOY_AXIS_HOEHENRUDER = 1
JOY_AXIS_SEITENRUDER = 2
JOY_AXIS_HEBEL = 3
JOY_INVERT_QUER = False
JOY_INVERT_HOEHE = False
JOY_INVERT_SEITE = False
JOY_INVERT_HEBEL = True
JOY_BUTTON_FEUER = 0
DEADZONE = 0.06

# ---------- Simulation ---------------------------------------------------
WIDTH, HEIGHT = 1280, 720
FPS = 60
GRID_STEP = 10.0                 # exakt 10 m pro Gitterfeld
STALL_SPEED = 65.0 / 3.6         # m/s, beginnender Stroemungsabriss
TAKEOFF_SPEED = 118.0 / 3.6      # m/s
MAX_AMMO = 420
FIRE_INTERVAL = 0.115
RUNWAY_LENGTH = 1050.0
RUNWAY_WIDTH = 34.0
CANYON_START = 1550.0
CANYON_END = 10100.0
CANYON_WALL_THICKNESS = 35.0
MUSIC_VOLUME = 0.50
FLUGGERAEUSCH_LAUTSTAERKE = 0.0 
G = 9.81
NEAR = 1.0
FOV_DEG = 72.0
AIRCRAFT_GROUND_HEIGHT = 2.0

# ---------- Musikdateien (neben die Python-Datei legen) -----------------
# Carmen ist unter diesem tatsaechlich vorhandenen Namen gespeichert. Fuer
# die beiden anderen Stuecke werden zusaetzlich passende WAV-Namen automatisch
# erkannt. Die ersten Namen koennen bei Bedarf einfach geaendert werden.
MUSIC_FILES = {
    "WALKUERENRITT": "wagner_walkuerenritt_render.wav",
    "CARMEN": "bizet_carmen_db_render.wav",
    "WILHELM_TELL": "rossini_wilhelm_tell_render.wav",
}

# ---------- einfache Jet-Flugleistungen --------------------------------
JET_TRIM_SPEED = 210.0 / 3.6
JET_MAX_SPEED = 430.0 / 3.6
JET_IDLE_THRUST = 0.8
JET_MAX_THRUST = 7.8

BLACK = (3, 6, 7)
GREEN = (35, 160, 76)
DARK_GREEN = (18, 72, 40)
BRIGHT_GREEN = (90, 235, 125)
WHITE = (230, 238, 225)
YELLOW = (242, 210, 70)
RED = (240, 75, 65)
CYAN = (75, 205, 220)
GREY = (90, 105, 98)
ORANGE = (245, 135, 45)


def clamp(value, low, high):
    return max(low, min(high, value))


def deadzone(value):
    if abs(value) < DEADZONE:
        return 0.0
    return math.copysign((abs(value) - DEADZONE) / (1.0 - DEADZONE), value)


def angle_wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Airport:
    """Landebahn mit lokalem Koordinatensystem: rechts und Bahnrichtung."""
    name: str
    x: float
    z: float
    heading: float
    length: float
    width: float
    color: tuple
    marker_color: tuple

    def point(self, right, forward, height=0.08):
        return (
            self.x + right * math.cos(self.heading) + forward * math.sin(self.heading),
            height,
            self.z - right * math.sin(self.heading) + forward * math.cos(self.heading),
        )

    def local(self, x, z):
        dx, dz = x - self.x, z - self.z
        return (dx * math.cos(self.heading) - dz * math.sin(self.heading),
                dx * math.sin(self.heading) + dz * math.cos(self.heading))

    def contains(self, x, z, margin=0.0):
        right, forward = self.local(x, z)
        return (abs(right) <= self.width * 0.5 + margin
                and -margin <= forward <= self.length + margin)


AIRPORTS = (
    Airport("EINSATZFLUGPLATZ", 0.0, 0.0, 0.0,
            RUNWAY_LENGTH, RUNWAY_WIDTH, WHITE, CYAN),
)


BRIDGES = (
    # Z-Position, Fahrbahnhoehe, Breite der Brueckenkonstruktion
    (3150.0, 46.0, 12.0),
    (5200.0, 58.0, 14.0),
    (7350.0, 38.0, 11.0),
    (9050.0, 66.0, 16.0),
)

def canyon_center(z):
    """Gewundene Mittellinie des Flusses in Weltkoordinaten."""
    if z < CANYON_START:
        return 0.0
    s = z - CANYON_START
    return (210.0 * math.sin(s / 720.0)
            + 105.0 * math.sin(s / 285.0 + 0.65)
            + 45.0 * math.sin(s / 125.0))


def river_half_width(z):
    s = max(0.0, z - CANYON_START)
    return 78.0 + 18.0 * math.sin(s / 510.0) + 12.0 * math.sin(s / 190.0)


def canyon_half_width(z):
    s = max(0.0, z - CANYON_START)
    return river_half_width(z) + 190.0 + 38.0 * math.sin(s / 640.0 + 0.8)


def canyon_wall_height(z):
    s = max(0.0, z - CANYON_START)
    return 145.0 + 55.0 * math.sin(s / 850.0 - 0.4) + 35.0 * math.sin(s / 310.0)


def terrain_height(x, z):
    """Hoehe von Flussbett, Boeschung und Canyonwand am Ort x/z."""
    if not CANYON_START <= z <= CANYON_END:
        return 0.0
    offset = abs(x - canyon_center(z))
    river = river_half_width(z)
    outer = canyon_half_width(z)
    if offset <= river:
        return 0.0
    # Hinter der sichtbaren Wand beginnt wieder freier Aussenraum. Zuvor
    # blieb die Hoehe wegen clamp(..., 0, 1) unbegrenzt auf Wandniveau;
    # dadurch entstanden scheinbare Wandkollisionen weit neben dem Canyon.
    if offset > outer + CANYON_WALL_THICKNESS:
        return 0.0
    slope = clamp((offset - river) / max(1.0, outer - river), 0.0, 1.0)
    return canyon_wall_height(z) * (slope ** 0.72)


def airport_at(x, z, margin=0.0):
    for index, airport in enumerate(AIRPORTS):
        if airport.contains(x, z, margin):
            return index
    return None


@dataclass
class Aircraft:
    """Einfaches, aber gut steuerbares strahlgetriebenes Flugzeug."""
    x: float = 0.0
    y: float = AIRCRAFT_GROUND_HEIGHT
    z: float = 35.0
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    speed: float = 0.0
    vertical_speed: float = 0.0
    crashed: bool = False
    landed: bool = True
    flight_state: str = "READY"
    sideslip: float = 0.0
    sim_time: float = 0.0
    throttle: float = 0.0
    damage: int = 0
    last_touchdown_sink: float = 0.0
    last_touchdown_speed: float = 0.0
    landing_quality: str = "--"

    def __post_init__(self):
        self.reset()

    def reset(self):
        self.sim_time = 0.0
        self.x, self.y, self.z = 0.0, AIRCRAFT_GROUND_HEIGHT, 35.0
        self.heading = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.speed = 0.0
        self.vertical_speed = 0.0
        self.crashed = False
        self.landed = True
        self.flight_state = "READY"
        self.sideslip = 0.0
        self.throttle = 0.0
        self.damage = 0
        self.last_touchdown_sink = 0.0
        self.last_touchdown_speed = 0.0
        self.landing_quality = "--"

    def update(self, dt, aileron, elevator, rudder, throttle):
        if self.crashed:
            return
        self.sim_time += dt
        self.throttle = clamp(throttle, 0.0, 1.0)

        if self.landed:
            thrust = JET_MAX_THRUST * self.throttle
            rolling_drag = 0.9 + 0.0011 * self.speed * self.speed
            wheel_brake = 8.0 if self.throttle < 0.04 and self.speed > 1.0 else 0.0
            self.speed = clamp(self.speed + (thrust - rolling_drag - wheel_brake) * dt,
                               0.0, JET_MAX_SPEED)
            if self.speed > 0.4:
                self.flight_state = "TAKEOFF"
            steering = math.radians(13.0) * rudder * clamp(self.speed / 22.0, 0.0, 1.0)
            self.heading = angle_wrap(self.heading + steering * dt)
            self.x += math.sin(self.heading) * self.speed * dt
            self.z += math.cos(self.heading) * self.speed * dt
            self.y = AIRCRAFT_GROUND_HEIGHT
            self.roll += math.radians(24.0) * aileron * dt
            self.roll *= max(0.0, 1.0 - 2.2 * dt)
            self.pitch += math.radians(18.0) * elevator * dt
            self.pitch *= max(0.0, 1.0 - 0.65 * dt)
            self.pitch = clamp(self.pitch, math.radians(-2), math.radians(14))
            self.vertical_speed = 0.0
            if self.speed >= TAKEOFF_SPEED and self.pitch > math.radians(2.5):
                self.landed = False
                self.flight_state = "FLYING"
                self.y += 0.12
            return

        authority = clamp(self.speed / STALL_SPEED, 0.25, 1.55)
        self.roll += math.radians(72.0) * aileron * authority * dt
        self.pitch += math.radians(31.0) * elevator * authority * dt
        self.roll *= max(0.0, 1.0 - 0.24 * dt)
        # Schwache Eigenstabilitaet; der Jet behaelt die gewaehlte Fluglage,
        # richtet sich bei neutralem Knueppel aber langsam aus.
        self.pitch += (-self.pitch * 0.055) * dt
        self.roll = clamp(self.roll, math.radians(-84), math.radians(84))
        self.pitch = clamp(self.pitch, math.radians(-42), math.radians(46))

        bank_turn = G * math.tan(self.roll) / max(self.speed, 8.0)
        coordinated_rudder = clamp(math.sin(self.roll) * 0.55, -0.72, 0.72)
        slip_target = math.radians(12.0) * (rudder - coordinated_rudder - 0.28 * aileron)
        slip_response = 1.0 - math.exp(-dt / 0.55)
        self.sideslip += (slip_target - self.sideslip) * slip_response
        yaw_rate = bank_turn + math.radians(15.0) * rudder - 0.24 * self.sideslip
        self.heading = angle_wrap(self.heading + yaw_rate * dt)

        thrust = JET_IDLE_THRUST + JET_MAX_THRUST * self.throttle
        drag = 1.25 + 0.00055 * self.speed * self.speed
        slope_accel = -G * math.sin(self.pitch)
        self.speed = clamp(self.speed + (thrust - drag + slope_accel) * dt,
                           4.0, JET_MAX_SPEED)

        stall_ratio = clamp(self.speed / STALL_SPEED, 0.0, 1.0)
        stall_sink = (1.0 - stall_ratio) ** 2 * 18.0
        if self.speed < STALL_SPEED:
            self.pitch -= math.radians(24.0) * (1.0 - stall_ratio) * dt

        bank_sink = max(0.0, 1.0 / max(0.28, math.cos(self.roll)) - 1.0) * 2.0
        self.vertical_speed = self.speed * math.sin(self.pitch) - stall_sink - bank_sink

        horizontal_speed = self.speed * math.cos(self.pitch)
        self.x += math.sin(self.heading) * horizontal_speed * dt
        self.z += math.cos(self.heading) * horizontal_speed * dt
        self.y += self.vertical_speed * dt

        # Im Canyon wertet die Missionslogik Wasser, Waende und Bruecken
        # getrennt aus. Ausserhalb davon gilt die normale Lande-/Bodenlogik.
        if self.y <= AIRCRAFT_GROUND_HEIGHT and CANYON_START <= self.z <= CANYON_END:
            return

        if self.y <= AIRCRAFT_GROUND_HEIGHT:
            on_runway = airport_at(self.x, self.z, margin=2.0) is not None
            self.last_touchdown_sink = self.vertical_speed
            self.last_touchdown_speed = self.speed
            hard = (self.vertical_speed < -4.2
                    or abs(self.roll) > math.radians(14.0)
                    or self.speed > 78.0
                    or not on_runway)
            if hard:
                self.crashed = True
                self.flight_state = "CRASHED"
                self.y = AIRCRAFT_GROUND_HEIGHT
                self.damage += 1
            else:
                self.y = AIRCRAFT_GROUND_HEIGHT
                self.pitch = 0.0
                self.roll = 0.0
                self.landed = True
                self.flight_state = "LANDED"
                self.vertical_speed = 0.0
                sink = abs(self.last_touchdown_sink)
                if sink < 1.4 and self.last_touchdown_speed < 60.0:
                    self.landing_quality = "SEHR GUT"
                elif sink < 2.7:
                    self.landing_quality = "GUT"
                else:
                    self.landing_quality = "HART, ABER SICHER"



@dataclass
class Bullet:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    life: float = 3.0

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.z += self.vz * dt
        self.life -= dt


@dataclass
class Explosion:
    x: float
    y: float
    z: float
    age: float = 0.0
    duration: float = 1.25
    size: float = 28.0

    def update(self, dt):
        self.age += dt


@dataclass
class Target:
    kind: str
    z: float
    lane: float
    direction: float = 1.0
    alive: bool = True
    phase: float = 0.0

    @property
    def value(self):
        return {"PATROUILLE": 120, "DROHNENSCHIFF": 240,
                "LASTKAHN": 180, "SCHNELLBOOT": 160}[self.kind]

    @property
    def radius(self):
        return {"PATROUILLE": 14.0, "DROHNENSCHIFF": 25.0,
                "LASTKAHN": 29.0, "SCHNELLBOOT": 12.0}[self.kind]

    def position(self, sim_time):
        if self.kind == "SCHNELLBOOT":
            width = river_half_width(self.z) * 0.78
            crossing = math.sin(sim_time * 0.62 * self.direction + self.phase)
            x = canyon_center(self.z) + crossing * width
            return x, 3.0, self.z
        travel = ((sim_time * (4.0 if self.kind == "PATROUILLE" else 2.2)
                   * self.direction + self.phase * 60.0) % 230.0) - 115.0
        target_z = clamp(self.z + travel, CANYON_START + 200, CANYON_END - 150)
        x = canyon_center(target_z) + self.lane * river_half_width(target_z) * 0.48
        return x, 3.0, target_z


@dataclass
class Drone:
    alive: bool = True
    hit_points: int = 3

    def position(self, sim_time):
        z = 1700.0 + 85.0 * math.sin(sim_time * 0.31)
        x = canyon_center(z) + 105.0 * math.sin(sim_time * 0.73)
        y = 245.0 + 38.0 * math.sin(sim_time * 0.47 + 0.8)
        return x, y, z


def make_targets():
    data = (
        ("PATROUILLE", 2350, -0.4, 1),
        ("LASTKAHN", 2850, 0.25, 1),
        ("SCHNELLBOOT", 3500, 0.0, 1),
        ("DROHNENSCHIFF", 4150, -0.25, -1),
        ("PATROUILLE", 4700, 0.4, 1),
        ("SCHNELLBOOT", 5650, 0.0, -1),
        ("LASTKAHN", 6100, -0.2, 1),
        ("PATROUILLE", 6500, 0.45, -1),
        ("DROHNENSCHIFF", 6950, -0.35, 1),
        ("SCHNELLBOOT", 7550, 0.0, 1),
        ("PATROUILLE", 7900, -0.45, 1),
        ("LASTKAHN", 8250, 0.3, -1),
        ("DROHNENSCHIFF", 8650, 0.0, 1),
        ("SCHNELLBOOT", 9250, 0.0, -1),
    )
    return [Target(kind, float(z), lane, float(direction), phase=i * 0.71)
            for i, (kind, z, lane, direction) in enumerate(data)]


MISSION_TEXT = {
    "START": "STARTBEREIT - SCHUB SETZEN UND ABHEBEN",
    "TAKEOFF": "STARTLAUF - FAHRT AUFNEHMEN UND ZIEHEN",
    "APPROACH": "ANFLUG AUF DAS EINSATZGEBIET",
    "DRONE": "PFLICHTZIEL: FLUGDROHNE BEKAEMPFEN",
    "DIVE": "DROHNE ZERSTOERT - ANGRIFF AUF DEN CANYON",
    "CANYON_1": "CANYON I - WASSERZIELE BEKAEMPFEN",
    "CANYON_2": "CANYON II - HOHE GESCHWINDIGKEIT",
    "EXIT": "CANYON VERLASSEN - KURS ZUM FLUGPLATZ",
    "RETURN": "RUECKFLUG - FLUGPLATZ ANFLIEGEN UND LANDEN",
    "COMPLETE": "MISSION BEENDET - AUSWERTUNG",
    "FAILED": "MISSION GESCHEITERT - R FUER NEUSTART",
}


class Mission:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = "START"
        self.drone = Drone()
        self.targets = make_targets()
        self.bullets = []
        self.explosions = []
        self.ammo = MAX_AMMO
        self.shots = 0
        self.hits = 0
        self.water_destroyed = 0
        self.score = 0
        self.collisions = 0
        self.safe_return = False
        self.fire_timer = 0.0
        self.message = ""
        self.message_timer = 0.0
        self.music_request = None

    def set_phase(self, phase, music=None, message=None):
        if phase == self.phase:
            return
        self.phase = phase
        if music:
            self.music_request = music
        if message:
            self.message = message
            self.message_timer = 4.0

    def fire(self, aircraft, sounds):
        if (self.fire_timer > 0.0 or self.ammo <= 0 or aircraft.crashed
                or aircraft.landed):
            return
        cp, sp = math.cos(aircraft.pitch), math.sin(aircraft.pitch)
        sh, ch = math.sin(aircraft.heading), math.cos(aircraft.heading)
        forward_speed = aircraft.speed + 310.0
        self.bullets.append(Bullet(
            aircraft.x + sh * 8.0, aircraft.y + sp * 8.0 - 0.7,
            aircraft.z + ch * 8.0,
            sh * cp * forward_speed, sp * forward_speed,
            ch * cp * forward_speed))
        self.ammo -= 1
        self.shots += 1
        self.fire_timer = FIRE_INTERVAL
        sounds.play_gun()

    def update(self, dt, aircraft, sounds):
        self.fire_timer = max(0.0, self.fire_timer - dt)
        self.message_timer = max(0.0, self.message_timer - dt)
        for bullet in self.bullets:
            bullet.update(dt)
        self.bullets = [b for b in self.bullets if b.life > 0.0 and b.y > -5.0]
        for explosion in self.explosions:
            explosion.update(dt)
        self.explosions = [e for e in self.explosions if e.age < e.duration]
        self._bullet_hits(aircraft, sounds)
        self._environment_collision(aircraft, sounds)
        self._advance_phase(aircraft)

    def _bullet_hits(self, aircraft, sounds):
        survivors = []
        for bullet in self.bullets:
            consumed = False
            if self.drone.alive:
                dx, dy, dz = self.drone.position(aircraft.sim_time)
                if math.dist((bullet.x, bullet.y, bullet.z), (dx, dy, dz)) < 24.0:
                    self.drone.hit_points -= 1
                    self.hits += 1
                    consumed = True
                    self.explosions.append(Explosion(dx, dy, dz, size=24.0))
                    sounds.play_explosion(0.55)
                    if self.drone.hit_points <= 0:
                        self.drone.alive = False
                        self.score += 600
                        self.explosions.append(Explosion(dx, dy, dz, duration=1.8, size=58.0))
                        self.set_phase("DIVE", message=
                                       "DROHNE ZERSTOERT - ANGRIFF AUF DEN CANYON")
            if not consumed and not self.drone.alive:
                for target in self.targets:
                    if not target.alive:
                        continue
                    tx, ty, tz = target.position(aircraft.sim_time)
                    if math.dist((bullet.x, bullet.y, bullet.z), (tx, ty, tz)) < target.radius:
                        target.alive = False
                        self.hits += 1
                        self.water_destroyed += 1
                        self.score += target.value
                        self.explosions.append(Explosion(tx, ty, tz,
                                                         size=target.radius * 1.8))
                        sounds.play_explosion(0.45 + target.radius / 80.0)
                        consumed = True
                        break
            if not consumed:
                survivors.append(bullet)
        self.bullets = survivors

    def _environment_collision(self, aircraft, sounds):
        if aircraft.crashed:
            return
        if aircraft.landed and aircraft.flight_state in ("READY", "LANDED"):
            return
        collision = None
        if CANYON_START <= aircraft.z <= CANYON_END:
            center = canyon_center(aircraft.z)
            if abs(aircraft.x - center) <= river_half_width(aircraft.z):
                if aircraft.y <= 3.0:
                    collision = "WASSERKONTAKT"
            else:
                ground = terrain_height(aircraft.x, aircraft.z)
                if aircraft.y <= ground + 3.0:
                    collision = "CANYONWAND"
            for bridge_z, deck_y, thickness in BRIDGES:
                span = canyon_half_width(bridge_z) + 20.0
                if (abs(aircraft.z - bridge_z) < thickness
                        and abs(aircraft.x - canyon_center(bridge_z)) < span
                        and deck_y - 5.0 < aircraft.y < deck_y + 7.0):
                    collision = "BRUECKE"
                    break
        elif (aircraft.y <= AIRCRAFT_GROUND_HEIGHT
              and airport_at(aircraft.x, aircraft.z, 2.0) is None):
            collision = "BODEN"

        if collision:
            aircraft.crashed = True
            aircraft.flight_state = "CRASHED"
            aircraft.damage += 1
            self.collisions += 1
            self.set_phase("FAILED", message=f"KOLLISION: {collision}")
            self.explosions.append(Explosion(aircraft.x, max(3.0, aircraft.y), aircraft.z,
                                             duration=2.0, size=70.0))
            sounds.play_explosion(1.0)

    def _advance_phase(self, aircraft):
        if aircraft.crashed:
            self.phase = "FAILED"
            return
        if self.phase == "START" and aircraft.speed > 1.0:
            self.phase = "TAKEOFF"
        if self.phase == "TAKEOFF" and not aircraft.landed:
            self.set_phase("APPROACH", "WALKUERENRITT",
                           "ABGEHOBEN - WALKUERENRITT - CANYON ANFLIEGEN")
        if self.phase == "APPROACH" and aircraft.z > 1180.0:
            self.set_phase("DRONE", message="FLUGDROHNE IST DAS ERSTE PFLICHTZIEL")
        if self.phase == "DIVE" and aircraft.z > 1900.0 and aircraft.y < 220.0:
            self.set_phase("CANYON_1", "CARMEN", "CANYON ERREICHT - CARMEN")
        if self.phase == "CANYON_1" and aircraft.z > 5950.0:
            self.set_phase("CANYON_2", "WILHELM_TELL",
                           "SCHNELLER CANYONABSCHNITT - WILHELM TELL")
        if self.phase == "CANYON_2" and aircraft.z > CANYON_END - 180.0:
            self.set_phase("EXIT", message="CANYON VERLASSEN - RUECKKURS FLIEGEN")
        if self.phase == "EXIT" and math.cos(aircraft.heading) < -0.35:
            self.set_phase("RETURN", message="RUECKFLUG ZUM EINSATZFLUGPLATZ")
        if (self.phase in ("EXIT", "RETURN") and aircraft.landed
                and aircraft.flight_state == "LANDED"):
            self.safe_return = True
            landing_bonus = {"SEHR GUT": 400, "GUT": 250,
                             "HART, ABER SICHER": 100}.get(
                                 aircraft.landing_quality, 0)
            self.score += 1000 + landing_bonus
            self.set_phase("COMPLETE", message="MISSION BEENDET - SICHERE RUECKKEHR")

    def accuracy(self):
        return 100.0 * self.hits / self.shots if self.shots else 0.0


class Camera:
    def __init__(self, aircraft, view_yaw=0.0, view_pitch=0.0):
        self.a = aircraft
        self.yaw = aircraft.heading + view_yaw
        self.pitch = aircraft.pitch + view_pitch
        # Die Welt muss sich im Cockpit entgegengesetzt zur Flugzeugneigung
        # drehen: Bei Rechtslage steigt der Horizont auf der rechten Seite.
        self.roll = -aircraft.roll if abs(view_yaw) < math.radians(50) else 0.0
        self.focal = (WIDTH * 0.5) / math.tan(math.radians(FOV_DEG * 0.5))

    def camera_coords(self, point):
        dx = point[0] - self.a.x
        dy = point[1] - self.a.y
        dz = point[2] - self.a.z

        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        right = dx * cy - dz * sy
        forward = dx * sy + dz * cy

        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        up = dy * cp - forward * sp
        depth = dy * sp + forward * cp

        cr, sr = math.cos(self.roll), math.sin(self.roll)
        screen_x = right * cr + up * sr
        screen_y = -right * sr + up * cr
        return screen_x, screen_y, depth

    def project(self, point):
        x, y, depth = self.camera_coords(point)
        if depth <= NEAR:
            return None
        scale = self.focal / depth
        return int(WIDTH / 2 + x * scale), int(HEIGHT * 0.46 - y * scale), depth


def draw_sky_and_horizon(surface, camera, font_small):
    """Abendhimmel und lageabhaengiger Horizont; das Abendrot liegt im Westen."""
    view_bottom = HEIGHT - 142
    pitch_for_horizon = clamp(camera.pitch, math.radians(-80), math.radians(80))
    roll_for_horizon = clamp(camera.roll, math.radians(-82), math.radians(82))
    horizon_center = HEIGHT * 0.46 + camera.focal * math.tan(pitch_for_horizon)
    horizon_slope = math.tan(roll_for_horizon)
    horizon_left = horizon_center - horizon_slope * WIDTH * 0.5
    horizon_right = horizon_center + horizon_slope * WIDTH * 0.5

    # Westen entspricht Kurs 270 Grad. Je naeher der Blick nach Westen zeigt,
    # desto staerker wird das rote Leuchten am Horizont.
    west = math.radians(-90.0)
    west_difference = angle_wrap(west - camera.yaw)
    west_strength = 0.12 + 0.88 * (math.cos(west_difference) + 1.0) * 0.5

    top_color = (5, 10, 28)
    sunset_color = (238, 72, 30)
    pygame.draw.rect(surface, top_color, (0, 0, WIDTH, view_bottom))

    # Die Farbbaender liegen parallel zum Horizont. Dadurch bleiben sie im
    # Raum waagerecht und drehen sich bei Querlage sichtbar gegen das Cockpit.
    direction_x = math.cos(roll_for_horizon)
    direction_y = math.sin(roll_for_horizon)
    upward_x = math.sin(roll_for_horizon)
    upward_y = -math.cos(roll_for_horizon)
    line_length = math.hypot(WIDTH, view_bottom) * 1.5
    for distance in range(420, -1, -5):
        glow = math.exp(-distance / 95.0) * west_strength
        color = tuple(int(top_color[i] + (sunset_color[i] - top_color[i]) * glow)
                      for i in range(3))
        center_x = WIDTH * 0.5 + upward_x * distance
        center_y = horizon_center + upward_y * distance
        p1 = (int(center_x - direction_x * line_length),
              int(center_y - direction_y * line_length))
        p2 = (int(center_x + direction_x * line_length),
              int(center_y + direction_y * line_length))
        pygame.draw.line(surface, color, p1, p2, 6)

    # Dunkler Erdboden unterhalb der geneigten Horizontlinie.
    pygame.draw.polygon(surface, (2, 8, 5),
                        [(0, horizon_left), (WIDTH, horizon_right),
                         (WIDTH, view_bottom), (0, view_bottom)])

    horizon_color = tuple(int(80 + (sunset_color[i] - 80) * west_strength)
                          for i in range(3))
    pygame.draw.line(surface, horizon_color,
                     (0, int(horizon_left)), (WIDTH, int(horizon_right)), 2)

    # Die tiefstehende Sonne erscheint nur, wenn Westen im Sichtfeld liegt.
    if abs(west_difference) < math.radians(FOV_DEG * 0.55):
        sun_x = WIDTH * 0.5 + camera.focal * math.tan(west_difference)
        sun_y = horizon_center + horizon_slope * (sun_x - WIDTH * 0.5) - 18
        if -20 <= sun_x <= WIDTH + 20 and -20 <= sun_y <= view_bottom + 20:
            pygame.draw.circle(surface, (255, 125, 45),
                               (int(sun_x), int(sun_y)), 9)

    label = font_small.render("ABENDROT = WESTEN", True, (210, 110, 65))
    surface.blit(label, (WIDTH - label.get_width() - 12, view_bottom - 24))


def clipped_line(surface, camera, p1, p2, color, width=1):
    c1 = camera.camera_coords(p1)
    c2 = camera.camera_coords(p2)
    if c1[2] <= NEAR and c2[2] <= NEAR:
        return
    if c1[2] <= NEAR or c2[2] <= NEAR:
        behind, ahead = (c1, c2) if c1[2] <= NEAR else (c2, c1)
        t = (NEAR - behind[2]) / (ahead[2] - behind[2])
        cut = tuple(behind[i] + t * (ahead[i] - behind[i]) for i in range(3))
        if c1[2] <= NEAR:
            c1 = cut
        else:
            c2 = cut

    def screen(c):
        scale = camera.focal / c[2]
        return int(WIDTH / 2 + c[0] * scale), int(HEIGHT * 0.46 - c[1] * scale)

    pygame.draw.line(surface, color, screen(c1), screen(c2), width)


def clipped_polygon(surface, camera, points, color):
    """Fuellt ein 3D-Polygon und schneidet es an der nahen Kameraebene ab."""
    polygon = [camera.camera_coords(point) for point in points]
    clipped = []
    for current_index, current in enumerate(polygon):
        previous = polygon[current_index - 1]
        current_inside = current[2] > NEAR
        previous_inside = previous[2] > NEAR
        if current_inside != previous_inside:
            difference = current[2] - previous[2]
            if abs(difference) > 1e-9:
                fraction = (NEAR - previous[2]) / difference
                clipped.append(tuple(previous[i] + fraction * (current[i] - previous[i])
                                     for i in range(3)))
        if current_inside:
            clipped.append(current)
    if len(clipped) < 3:
        return False
    screen_points = []
    for x, y, depth in clipped:
        scale = camera.focal / max(depth, NEAR)
        screen_points.append((int(WIDTH / 2 + x * scale),
                              int(HEIGHT * 0.46 - y * scale)))
    pygame.draw.polygon(surface, color, screen_points)
    return True


def polygon_depth(camera, points):
    """Mittlere Kameratiefe fuer das Zeichnen von hinten nach vorn."""
    return sum(camera.camera_coords(point)[2] for point in points) / len(points)


def draw_world(surface, camera, aircraft, mission, font_small):
    # 10-m-Liniengitter in Platznaehe und ausserhalb des Canyons.
    extent_side = 650
    z_back = int(math.floor((aircraft.z - 300) / GRID_STEP) * GRID_STEP)
    z_front = int(math.ceil((aircraft.z + 1900) / GRID_STEP) * GRID_STEP)
    x_left = int(math.floor((aircraft.x - extent_side) / GRID_STEP) * GRID_STEP)
    x_right = int(math.ceil((aircraft.x + extent_side) / GRID_STEP) * GRID_STEP)
    if aircraft.z < CANYON_START + 350 or aircraft.z > CANYON_END - 350:
        for x in range(x_left, x_right + 1, int(GRID_STEP)):
            color = GREEN if x % 50 else BRIGHT_GREEN
            clipped_line(surface, camera, (x, 0, z_back), (x, 0, z_front),
                         color, 1 if x % 100 else 2)
        for z in range(z_back, z_front + 1, int(GRID_STEP)):
            color = DARK_GREEN if z % 50 else GREEN
            clipped_line(surface, camera, (x_left, 0, z), (x_right, 0, z),
                         color, 1 if z % 100 else 2)

    draw_airport(surface, camera, AIRPORTS[0], 0, font_small)
    draw_box(surface, camera, (-115, 0, 150), (45, 26, 55), CYAN)
    draw_box(surface, camera, (92, 0, 310), (62, 20, 45), YELLOW)
    draw_pylon(surface, camera, (145, 0, 780), 58, RED)
    draw_canyon(surface, camera, aircraft, mission, font_small)


def draw_airport(surface, camera, airport, airport_index, font_small):
    half = airport.width * 0.5
    corners = [airport.point(-half, 0), airport.point(half, 0),
               airport.point(half, airport.length), airport.point(-half, airport.length)]
    for i in range(4):
        clipped_line(surface, camera, corners[i], corners[(i + 1) % 4], airport.color, 3)
    for forward in range(20, int(airport.length) - 20, 40):
        clipped_line(surface, camera, airport.point(0, forward),
                     airport.point(0, min(forward + 20, airport.length)), airport.color, 2)
    for right in (-half + 3, -half + 6, half - 6, half - 3):
        clipped_line(surface, camera, airport.point(right, 12),
                     airport.point(right, 35), airport.color, 2)

    # Anflugbefeuerung und Kontrollturm am Flugplatz.
    for forward in (-100, -70, -40, -10):
        clipped_line(surface, camera, airport.point(-18, forward, 0.2),
                     airport.point(18, forward, 0.2), airport.marker_color, 1)
    tower = airport.point(-72, 470, 0)
    draw_box(surface, camera, (tower[0] - 8, 0, tower[2] - 8),
             (16, 42, 16), airport.marker_color)
    label = camera.project(airport.point(0, 15, 0.2))
    if label and -200 < label[0] < WIDTH + 200 and -100 < label[1] < HEIGHT:
        text = font_small.render(f"{airport_index + 1} {airport.name}", True, airport.color)
        surface.blit(text, (label[0] - text.get_width() // 2, label[1]))


def draw_canyon(surface, camera, aircraft, mission, font_small):
    """Fluss, Boeschungen, Waende und Bruecken als Retro-Linienmodell."""
    start = max(CANYON_START, aircraft.z - 450.0)
    end = min(CANYON_END, aircraft.z + 2800.0)
    step = 65.0
    z_values = []
    z = math.floor(start / step) * step
    while z <= end + step:
        z_values.append(z)
        z += step

    sections = []
    for z in z_values:
        center = canyon_center(z)
        river = river_half_width(z)
        outer = canyon_half_width(z)
        wall = canyon_wall_height(z)
        sections.append({
            "lw": (center - outer, wall, z),
            "lg": (center - outer, 0.0, z),
            "lb": (center - river, 0.35, z),
            "c": (center, 0.18, z),
            "rb": (center + river, 0.35, z),
            "rg": (center + outer, 0.0, z),
            "rw": (center + outer, wall, z),
        })

    # Geschlossene Flaechen verdecken die dahinterliegenden Kurven. Alle
    # Vierecke werden nach Kameratiefe sortiert (Malerverfahren), damit der
    # Canyon auch beim Rueckflug von der anderen Seite richtig erscheint.
    faces = []
    for index, (previous, points) in enumerate(zip(sections, sections[1:])):
        shade = index % 2
        wall_left = (91, 47, 28) if shade else (108, 56, 31)
        wall_right = (112, 58, 32) if shade else (86, 45, 27)
        outer_dark = (54, 32, 25) if shade else (65, 37, 27)
        water = (10, 56, 92) if shade else (12, 66, 108)
        faces.extend((
            ([previous["lb"], previous["rb"], points["rb"], points["lb"]],
             water, (26, 125, 185), 1),
            ([previous["lw"], previous["lb"], points["lb"], points["lw"]],
             wall_left, (184, 91, 42), 1),
            ([previous["rb"], previous["rw"], points["rw"], points["rb"]],
             wall_right, (205, 103, 45), 1),
            ([previous["lg"], previous["lw"], points["lw"], points["lg"]],
             outer_dark, ORANGE, 1),
            ([previous["rw"], previous["rg"], points["rg"], points["rw"]],
             outer_dark, ORANGE, 1),
        ))
    # Auch Bruecken und Ziele gehoeren in dasselbe Malerverfahren. Wuerden sie
    # erst nach dem Canyon gezeichnet, waeren sie selbst hinter einer Wand noch
    # sichtbar. Die gemeinsame Tiefensortierung laesst nahe Waende alles
    # verdecken, was in einer weiter entfernten Canyonkurve liegt.
    render_items = [(polygon_depth(camera, face[0]), "face", face)
                    for face in faces]
    for bridge_z, deck_y, thickness in BRIDGES:
        if start - 100 <= bridge_z <= end + 100:
            center = canyon_center(bridge_z)
            depth = camera.camera_coords((center, deck_y, bridge_z))[2]
            render_items.append((depth, "bridge", (bridge_z, deck_y, thickness)))

    if mission.drone.alive:
        x, y, drone_z = mission.drone.position(aircraft.sim_time)
        depth = camera.camera_coords((x, y, drone_z))[2]
        render_items.append((depth, "drone", (x, y, drone_z,
                                               mission.drone.hit_points)))
    else:
        for target in mission.targets:
            if target.alive:
                x, y, target_z = target.position(aircraft.sim_time)
                depth = camera.camera_coords((x, y, target_z))[2]
                render_items.append((depth, "vessel",
                                     (target.kind, x, y, target_z)))

    render_items.sort(key=lambda item: item[0], reverse=True)
    for _depth, item_kind, data in render_items:
        if item_kind == "face":
            polygon, fill_color, edge_color, edge_width = data
            clipped_polygon(surface, camera, polygon, fill_color)
            for index, point in enumerate(polygon):
                clipped_line(surface, camera, polygon[index - 1], point,
                             edge_color, edge_width)
        elif item_kind == "bridge":
            draw_bridge(surface, camera, *data)
        elif item_kind == "drone":
            x, y, drone_z, hit_points = data
            draw_drone(surface, camera, x, y, drone_z, hit_points)
            marker = camera.project((x, y + 28, drone_z))
            if marker:
                text = font_small.render("PFLICHTZIEL DROHNE", True, RED)
                surface.blit(text, (marker[0] - text.get_width() // 2, marker[1]))
        elif item_kind == "vessel":
            draw_vessel(surface, camera, *data)


def draw_bridge(surface, camera, z, deck_y, thickness):
    center = canyon_center(z)
    span = canyon_half_width(z) + 22.0
    left, right = center - span, center + span
    color = (220, 105, 60)
    clipped_polygon(surface, camera,
                    [(left, deck_y, z - thickness),
                     (right, deck_y, z - thickness),
                     (right, deck_y, z + thickness),
                     (left, deck_y, z + thickness)],
                    (92, 48, 38))
    clipped_line(surface, camera, (left, deck_y, z - thickness),
                 (right, deck_y, z - thickness), color, 3)
    clipped_line(surface, camera, (left, deck_y, z + thickness),
                 (right, deck_y, z + thickness), color, 3)
    for x in (left, center - river_half_width(z) - 10,
              center + river_half_width(z) + 10, right):
        clipped_line(surface, camera, (x, 0, z), (x, deck_y, z), color, 2)
    for x in range(int(left), int(right) + 1, 35):
        clipped_line(surface, camera, (x, deck_y, z - thickness),
                     (x, deck_y, z + thickness), color, 1)






def draw_mission_objects(surface, camera, mission):
    """Geschosse und Explosionen in der 3D-Welt."""
    for bullet in mission.bullets:
        speed = math.sqrt(bullet.vx ** 2 + bullet.vy ** 2 + bullet.vz ** 2)
        back = 22.0 / max(speed, 1.0)
        tail = (bullet.x - bullet.vx * back,
                bullet.y - bullet.vy * back,
                bullet.z - bullet.vz * back)
        clipped_line(surface, camera, tail, (bullet.x, bullet.y, bullet.z),
                     (255, 235, 90), 3)

    for explosion in mission.explosions:
        draw_explosion(surface, camera, explosion)


def draw_drone(surface, camera, x, y, z, hit_points):
    color = RED if hit_points <= 1 else (245, 130, 55)
    fill = (105, 28, 24) if hit_points <= 1 else (105, 58, 24)
    clipped_polygon(surface, camera,
                    [(x - 24, y, z), (x, y, z - 11),
                     (x + 24, y, z), (x, y + 3, z + 14)], fill)
    clipped_polygon(surface, camera,
                    [(x - 5, y - 5, z - 15), (x + 5, y - 5, z - 15),
                     (x + 6, y + 5, z + 18), (x - 6, y + 5, z + 18)],
                    (72, 48, 32))
    clipped_line(surface, camera, (x - 24, y, z), (x + 24, y, z), color, 3)
    clipped_line(surface, camera, (x, y - 5, z - 15), (x, y + 5, z + 18), color, 2)
    clipped_line(surface, camera, (x - 24, y, z), (x, y + 7, z + 12), color, 1)
    clipped_line(surface, camera, (x + 24, y, z), (x, y + 7, z + 12), color, 1)
    for side in (-1, 1):
        rotor_x = x + side * 18
        clipped_line(surface, camera, (rotor_x - 10, y + 4, z),
                     (rotor_x + 10, y + 4, z), CYAN, 1)
        clipped_line(surface, camera, (rotor_x, y + 4, z - 10),
                     (rotor_x, y + 4, z + 10), CYAN, 1)


def draw_vessel(surface, camera, kind, x, y, z):
    sizes = {
        "PATROUILLE": (12, 6, 27),
        "DROHNENSCHIFF": (19, 10, 48),
        "LASTKAHN": (22, 8, 56),
        "SCHNELLBOOT": (10, 5, 23),
    }
    colors = {
        "PATROUILLE": YELLOW, "DROHNENSCHIFF": RED,
        "LASTKAHN": ORANGE, "SCHNELLBOOT": CYAN,
    }
    fills = {
        "PATROUILLE": (108, 92, 25), "DROHNENSCHIFF": (100, 27, 25),
        "LASTKAHN": (112, 55, 22), "SCHNELLBOOT": (18, 78, 88),
    }
    width, height, length = sizes[kind]
    color = colors[kind]
    draw_solid_box(surface, camera, (x - width / 2, y, z - length / 2),
                   (width, height, length), fills[kind], color)
    clipped_polygon(surface, camera,
                    [(x - width / 2, y, z + length / 2),
                     (x + width / 2, y, z + length / 2),
                     (x, y - 1, z + length / 2 + 9)], fills[kind])
    clipped_line(surface, camera, (x - width / 2, y, z + length / 2),
                 (x, y - 1, z + length / 2 + 9), color, 2)
    clipped_line(surface, camera, (x + width / 2, y, z + length / 2),
                 (x, y - 1, z + length / 2 + 9), color, 2)
    if kind in ("DROHNENSCHIFF", "PATROUILLE"):
        clipped_line(surface, camera, (x, y + height, z),
                     (x, y + height + 13, z), color, 2)


def draw_explosion(surface, camera, explosion):
    fraction = explosion.age / explosion.duration
    radius = explosion.size * (0.25 + 1.2 * fraction)
    color = YELLOW if fraction < 0.45 else RED
    rings = []
    for plane in range(3):
        points = []
        for i in range(13):
            a = i * 2 * math.pi / 12
            if plane == 0:
                point = (explosion.x + math.cos(a) * radius,
                         explosion.y + math.sin(a) * radius, explosion.z)
            elif plane == 1:
                point = (explosion.x + math.cos(a) * radius,
                         explosion.y, explosion.z + math.sin(a) * radius)
            else:
                point = (explosion.x, explosion.y + math.cos(a) * radius,
                         explosion.z + math.sin(a) * radius)
            points.append(point)
        rings.append(points)
    for ring in rings:
        for p1, p2 in zip(ring, ring[1:]):
            clipped_line(surface, camera, p1, p2, color, 2)




def draw_solid_box(surface, camera, origin, size, fill_color, edge_color):
    """Gefuellter Quader mit tiefensortierten Flaechen und Linienkontur."""
    x, y, z = origin
    sx, sy, sz = size
    points = [
        (x + dx * sx, y + dy * sy, z + dz * sz)
        for dx, dy, dz in ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
                           (0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))
    ]
    faces = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0))
    sorted_faces = sorted(
        ([points[index] for index in face] for face in faces),
        key=lambda face: polygon_depth(camera, face), reverse=True)
    for index, face in enumerate(sorted_faces):
        shade = tuple(max(0, channel - (index % 3) * 7) for channel in fill_color)
        clipped_polygon(surface, camera, face, shade)
    draw_box(surface, camera, origin, size, edge_color)


def draw_box(surface, camera, origin, size, color):
    x, y, z = origin
    sx, sy, sz = size
    pts = [
        (x + dx * sx, y + dy * sy, z + dz * sz)
        for dx, dy, dz in ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
                           (0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))
    ]
    edges = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7),
             (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
    for a, b in edges:
        clipped_line(surface, camera, pts[a], pts[b], color)


def draw_pylon(surface, camera, origin, height, color):
    x, y, z = origin
    clipped_line(surface, camera, (x - 8, y, z), (x, y + height, z), color, 2)
    clipped_line(surface, camera, (x + 8, y, z), (x, y + height, z), color, 2)
    clipped_line(surface, camera, (x - 8, y, z), (x + 8, y, z), color)
    clipped_line(surface, camera, (x - 5, y + height * .55, z),
                 (x + 5, y + height * .55, z), color)


class SoundSystem:
    """Erzeugt Fahrtwind, Vario, Triebwerk, Kanone und Explosionen."""
    def __init__(self):
        self.available = False
        self.enabled = False
        self.flow_index = -1
        self.sink_index = -1
        self.vario_timer = 0.0
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(44100, -16, 2, 512)
            pygame.mixer.set_num_channels(max(12, pygame.mixer.get_num_channels()))
            self.flow_channel = pygame.mixer.Channel(0)
            self.stall_channel = pygame.mixer.Channel(1)
            self.vario_channel = pygame.mixer.Channel(2)
            self.engine_channel = pygame.mixer.Channel(3)
            self.gun_channel = pygame.mixer.Channel(4)
            self.explosion_channel = pygame.mixer.Channel(5)
            self.flow_sounds = [self._noise(0.65, i / 6.0, 100 + i) for i in range(7)]
            self.stall_sound = self._noise(0.70, 0.18, 812, buffet=True)
            self.climb_sounds = [self._tone(520 + i * 85, 0.12) for i in range(8)]
            self.sink_sounds = [self._tone(freq, 0.55) for freq in (285, 255, 225, 195, 170)]
            self.engine_sounds = [self._engine(72 + i * 29, 0.55, 2000 + i)
                                  for i in range(8)]
            self.gun_sound = self._gun()
            self.explosion_sound = self._noise(0.65, 0.08, 9917, buffet=True)
            self.engine_index = -1
            self.available = True
            self.enabled = True
        except pygame.error:
            # Der Simulator bleibt auch auf Rechnern ohne Audiogeraet lauffaehig.
            self.enabled = False

    def _tone(self, frequency, duration):
        sample_rate = pygame.mixer.get_init()[0]
        data = array("h")
        count = int(sample_rate * duration)
        fade = max(1, int(sample_rate * 0.012))
        for i in range(count):
            envelope = min(1.0, i / fade, (count - i - 1) / fade)
            value = int(12000 * envelope * math.sin(2.0 * math.pi * frequency * i / sample_rate))
            data.extend((value, value))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def _noise(self, duration, brightness, seed, buffet=False):
        sample_rate = pygame.mixer.get_init()[0]
        rng = random.Random(seed)
        data = array("h")
        filtered = 0.0
        alpha = 0.025 + 0.34 * brightness
        count = int(sample_rate * duration)
        for i in range(count):
            white = rng.uniform(-1.0, 1.0)
            filtered += alpha * (white - filtered)
            modulation = 1.0
            if buffet:
                modulation = 0.30 + 0.70 * max(0.0, math.sin(2.0 * math.pi * 9.0 * i / sample_rate))
            value = int(clamp(filtered * modulation, -1.0, 1.0) * 15000)
            data.extend((value, value))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def _engine(self, frequency, duration, seed):
        sample_rate = pygame.mixer.get_init()[0]
        rng = random.Random(seed)
        data = array("h")
        filtered = 0.0
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            filtered += 0.055 * (rng.uniform(-1.0, 1.0) - filtered)
            wave = (0.58 * math.sin(2 * math.pi * frequency * t)
                    + 0.25 * math.sin(2 * math.pi * frequency * 2.03 * t)
                    + 0.17 * filtered)
            value = int(clamp(wave, -1.0, 1.0) * 10500)
            data.extend((value, value))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def _gun(self):
        sample_rate = pygame.mixer.get_init()[0]
        rng = random.Random(481)
        data = array("h")
        count = int(sample_rate * 0.075)
        for i in range(count):
            envelope = (1.0 - i / count) ** 2
            value = int(envelope * (rng.uniform(-1, 1) * 17000
                                     + math.sin(i * 0.61) * 7000))
            value = int(clamp(value, -30000, 30000))
            data.extend((value, value))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def play_gun(self):
        if self.enabled and self.available:
            self.gun_channel.play(self.gun_sound)
            self.gun_channel.set_volume(0.38)

    def play_explosion(self, strength=1.0):
        if self.enabled and self.available:
            self.explosion_channel.play(self.explosion_sound)
            self.explosion_channel.set_volume(clamp(0.34 * strength, 0.18, 0.55))

    def toggle(self):
        if not self.available:
            return
        self.enabled = not self.enabled
        if not self.enabled:
            self.stop()

    def stop(self):
        if pygame.mixer.get_init():
            for channel_number in (0, 1, 2, 3, 4, 5):
                pygame.mixer.Channel(channel_number).stop()
        self.flow_index = self.sink_index = self.engine_index = -1

    def update(self, dt, aircraft):
        if not self.enabled or not pygame.mixer.get_init():
            return

        kmh = aircraft.speed * 3.6
        new_flow = int(clamp((kmh - 35.0) / 28.0, 0.0, 6.0))
        if new_flow != self.flow_index:
            self.flow_channel.stop()
            self.flow_channel.play(self.flow_sounds[new_flow], loops=-1)
            self.flow_index = new_flow
        flow_volume = clamp((kmh - 25.0) / 190.0, 0.015, 0.38)
        self.flow_channel.set_volume(flow_volume*FLUGGERAEUSCH_LAUTSTAERKE)

        engine_index = int(clamp(aircraft.throttle * 7.0, 0.0, 7.0))
        if engine_index != self.engine_index:
            self.engine_channel.stop()
            self.engine_channel.play(self.engine_sounds[engine_index], loops=-1)
            self.engine_index = engine_index
        self.engine_channel.set_volume((0.10 + aircraft.throttle * 0.28))

        stall_amount = clamp((72.0 - kmh) / 13.0, 0.0, 1.0)
        if stall_amount > 0.02 and not aircraft.landed:
            if not self.stall_channel.get_busy():
                self.stall_channel.play(self.stall_sound, loops=-1)
            self.stall_channel.set_volume(0.30 * stall_amount)
        else:
            self.stall_channel.stop()

        vertical = aircraft.vertical_speed
        self.vario_timer -= dt
        if vertical > 0.25:
            if self.sink_index >= 0:
                self.vario_channel.stop()
                self.sink_index = -1
            interval = clamp(0.80 - vertical * 0.095, 0.16, 0.78)
            if self.vario_timer <= 0.0:
                index = int(clamp(vertical / 0.85, 0.0, 7.0))
                self.vario_channel.play(self.climb_sounds[index])
                self.vario_channel.set_volume(0.36*FLUGGERAEUSCH_LAUTSTAERKE)
                self.vario_timer = interval
        elif vertical < -0.45:
            index = int(clamp((-vertical - 0.45) / 1.35, 0.0, 4.0))
            if index != self.sink_index:
                self.vario_channel.stop()
                self.vario_channel.play(self.sink_sounds[index], loops=-1)
                self.vario_channel.set_volume(0.22*FLUGGERAEUSCH_LAUTSTAERKE)
                self.sink_index = index
        else:
            self.vario_channel.stop()
            self.sink_index = -1
            self.vario_timer = 0.0


class MusicController:
    """Ein Musikkanal mit weichem Wechsel und fehlertoleranter Dateisuche."""
    KEYWORDS = {
        "WALKUERENRITT": ("walk", "wagner"),
        "CARMEN": ("carmen", "bizet"),
        "WILHELM_TELL": ("tell", "rossini"),
    }

    def __init__(self):
        self.available = pygame.mixer.get_init() is not None
        self.current = None
        self.pending = None
        self.pending_timer = 0.0
        self.notice = ""
        self.notice_timer = 0.0
        self.directory = os.path.dirname(os.path.abspath(__file__))
        self.resolved = {key: self._find_file(key, name)
                         for key, name in MUSIC_FILES.items()}

    def _find_file(self, key, preferred):
        exact = os.path.join(self.directory, preferred)
        if os.path.isfile(exact):
            return exact
        try:
            wavs = [name for name in os.listdir(self.directory)
                    if name.lower().endswith(".wav")]
        except OSError:
            return None
        words = self.KEYWORDS[key]
        for name in wavs:
            lower = name.lower()
            if any(word in lower for word in words):
                return os.path.join(self.directory, name)
        return None

    def request(self, key):
        if key == self.current or key == self.pending:
            return
        path = self.resolved.get(key)
        if not self.available or not path:
            if self.available and pygame.mixer.music.get_busy():
                pygame.mixer.music.fadeout(900)
            self.notice = f"MUSIKDATEI FEHLT: {MUSIC_FILES.get(key, key)}"
            self.notice_timer = 4.5
            self.current = key
            return
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.fadeout(900)
            self.pending = key
            self.pending_timer = 0.95
        else:
            self._start(key)

    def _start(self, key):
        path = self.resolved.get(key)
        if not path:
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(MUSIC_VOLUME)
            pygame.mixer.music.play(loops=-1, fade_ms=1200)
            self.current = key
            self.pending = None
        except pygame.error:
            self.notice = f"MUSIK KANN NICHT GELADEN WERDEN: {os.path.basename(path)}"
            self.notice_timer = 4.5

    def update(self, dt):
        self.notice_timer = max(0.0, self.notice_timer - dt)
        if self.pending:
            self.pending_timer -= dt
            if self.pending_timer <= 0.0:
                self._start(self.pending)

    def stop(self):
        if self.available:
            pygame.mixer.music.fadeout(500)
        self.current = self.pending = None


def draw_yaw_string(surface, aircraft, font_small):
    """Virtueller Haubenfaden; geglaettete Auslenkung zeigt den Schiebewinkel."""
    anchor = (WIDTH // 2, 110)
    length = 54
    deflection = clamp(-aircraft.sideslip / math.radians(13.0), -1.0, 1.0)
    end = (int(anchor[0] + deflection * 45), anchor[1] + length)
    pygame.draw.circle(surface, WHITE, anchor, 3)
    mid = (int((anchor[0] + end[0]) * 0.5 + deflection * 6),
           int((anchor[1] + end[1]) * 0.5))
    pygame.draw.line(surface, (245, 235, 185), anchor, mid, 2)
    pygame.draw.line(surface, (245, 235, 185), mid, end, 2)
    label = "FADEN MITTIG"
    if deflection < -0.18:
        label = "FADEN LINKS"
    elif deflection > 0.18:
        label = "FADEN RECHTS"
    text = font_small.render(label, True, (210, 205, 160))
    surface.blit(text, (anchor[0] - text.get_width() // 2, anchor[1] + length + 3))


def draw_dial(surface, center, radius, label, value_text, fraction, color=WHITE):
    pygame.draw.circle(surface, (7, 10, 10), center, radius)
    pygame.draw.circle(surface, GREY, center, radius, 2)
    for i in range(11):
        angle = math.radians(225 - i * 27)
        p1 = (center[0] + math.cos(angle) * (radius - 6),
              center[1] - math.sin(angle) * (radius - 6))
        p2 = (center[0] + math.cos(angle) * (radius - 13),
              center[1] - math.sin(angle) * (radius - 13))
        pygame.draw.line(surface, GREY, p1, p2, 1)
    angle = math.radians(225 - clamp(fraction, 0, 1) * 270)
    needle = (center[0] + math.cos(angle) * (radius - 17),
              center[1] - math.sin(angle) * (radius - 17))
    pygame.draw.line(surface, color, center, needle, 2)
    pygame.draw.circle(surface, color, center, 3)
    tiny = pygame.font.Font(None, 16)
    val = tiny.render(value_text, True, color)
    lab = tiny.render(label, True, GREY)
    surface.blit(val, (center[0] - val.get_width() // 2, center[1] + 16))
    surface.blit(lab, (center[0] - lab.get_width() // 2, center[1] + radius - 17))


def draw_instruments(surface, aircraft, mission, font, font_small):
    panel_y = HEIGHT - 142
    pygame.draw.rect(surface, (8, 12, 12), (0, panel_y, WIDTH, 142))
    pygame.draw.line(surface, GREY, (0, panel_y), (WIDTH, panel_y), 2)

    kmh = aircraft.speed * 3.6
    draw_dial(surface, (75, panel_y + 69), 57, "FAHRT km/h", f"{kmh:3.0f}", kmh / 300,
              RED if kmh < 65 else WHITE)
    draw_dial(surface, (202, panel_y + 69), 57, "HOEHE m", f"{aircraft.y:4.0f}",
              (aircraft.y % 1000) / 1000)
    vario_fraction = (clamp(aircraft.vertical_speed, -10, 10) + 10) / 20
    draw_dial(surface, (329, panel_y + 69), 57, "VARIO m/s",
              f"{aircraft.vertical_speed:+4.1f}", vario_fraction)
    heading_deg = math.degrees(aircraft.heading) % 360
    draw_dial(surface, (456, panel_y + 69), 57, "KURS Grad", f"{heading_deg:03.0f}",
              heading_deg / 360)

    # Kuenslicher Horizont, einfach und gut ablesbar.
    c = (585, panel_y + 69)
    pygame.draw.circle(surface, (8, 12, 12), c, 57)
    pygame.draw.circle(surface, GREY, c, 57, 2)
    line_len = 43
    horizon_y = c[1] + math.degrees(aircraft.pitch) * 1.4
    dx = math.cos(aircraft.roll) * line_len
    # Auch der kuenstliche Horizont dreht sich entgegen der Flugzeuglage.
    dy = -math.sin(aircraft.roll) * line_len
    pygame.draw.line(surface, CYAN, (c[0] - dx, horizon_y - dy),
                     (c[0] + dx, horizon_y + dy), 3)
    pygame.draw.line(surface, WHITE, (c[0] - 15, c[1]), (c[0] + 15, c[1]), 2)
    lab = pygame.font.Font(None, 16).render("LAGE", True, GREY)
    surface.blit(lab, (c[0] - lab.get_width() // 2, c[1] + 40))

    # Schubhebel und Einsatzdaten.
    bar_x, bar_y, bar_w, bar_h = 675, panel_y + 25, 25, 90
    pygame.draw.rect(surface, GREY, (bar_x, bar_y, bar_w, bar_h), 2)
    fill = int((bar_h - 4) * aircraft.throttle)
    pygame.draw.rect(surface, RED,
                     (bar_x + 3, bar_y + bar_h - 3 - fill, bar_w - 6, fill))
    surface.blit(font_small.render("SCHUB", True, WHITE), (710, bar_y + 5))
    state_names = {"READY": "STARTBEREIT", "TAKEOFF": "STARTLAUF",
                   "FLYING": "FLUG", "LANDED": "GELANDET", "CRASHED": "ABSTURZ"}
    state = state_names.get(aircraft.flight_state, aircraft.flight_state)
    surface.blit(font.render(state, True, YELLOW), (710, bar_y + 29))
    surface.blit(font_small.render(
        f"MUN {mission.ammo:03d}   TREFFER {mission.hits:02d}/{mission.shots:03d}",
        True, CYAN), (710, bar_y + 57))
    surface.blit(font_small.render(
        f"X {aircraft.x:+7.0f} m  Z {aircraft.z:+7.0f} m  PUNKTE {mission.score:05d}",
        True, GREEN), (710, bar_y + 78))


def axis_value(joystick, index, invert=False):
    if joystick is None or not 0 <= index < joystick.get_numaxes():
        return 0.0
    value = joystick.get_axis(index)
    return deadzone(-value if invert else value)


def read_controls(joystick, keys):
    # Joystick hat Vorrang; Tastaturwerte werden addiert und begrenzt.
    aileron = axis_value(joystick, JOY_AXIS_QUERRUDER, JOY_INVERT_QUER)
    elevator = axis_value(joystick, JOY_AXIS_HOEHENRUDER, JOY_INVERT_HOEHE)
    rudder = axis_value(joystick, JOY_AXIS_SEITENRUDER, JOY_INVERT_SEITE)

    if keys[pygame.K_LEFT] or keys[pygame.K_a]:
        aileron -= 1
    if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
        aileron += 1
    if keys[pygame.K_UP] or keys[pygame.K_w]:
        elevator += 1
    if keys[pygame.K_DOWN] or keys[pygame.K_s]:
        elevator -= 1
    if keys[pygame.K_q]:
        rudder -= 1
    if keys[pygame.K_e]:
        rudder += 1
    return clamp(aileron, -1, 1), clamp(elevator, -1, 1), clamp(rudder, -1, 1)




def draw_mission_ui(surface, aircraft, mission, music, joystick, font, font_small,
                    show_help, show_axes):
    phase_text = MISSION_TEXT.get(mission.phase, mission.phase)
    phase = font.render(phase_text, True, YELLOW)
    surface.blit(phase, (WIDTH // 2 - phase.get_width() // 2, 22))

    distance_home = math.hypot(aircraft.x, aircraft.z) / 1000.0
    bearing_home = math.degrees(math.atan2(-aircraft.x, -aircraft.z)) % 360.0
    status = (f"PHASE {mission.phase}   PUNKTE {mission.score:05d}   "
              f"MUN {mission.ammo:03d}   HEIM {distance_home:.1f} km/{bearing_home:03.0f} Grad")
    surface.blit(font_small.render(status, True, CYAN), (12, 58))
    joy_name = joystick.get_name() if joystick else "kein Joystick - Tastatur aktiv"
    surface.blit(font_small.render(joy_name, True, GREY), (12, 82))

    if mission.message_timer > 0.0:
        message = pygame.font.Font(None, 38).render(mission.message, True, YELLOW)
        surface.blit(message, (WIDTH // 2 - message.get_width() // 2, 88))
    if music.notice_timer > 0.0:
        notice = font_small.render(music.notice, True, ORANGE)
        surface.blit(notice, (WIDTH // 2 - notice.get_width() // 2, 122))

    if aircraft.speed < STALL_SPEED and not aircraft.landed and not aircraft.crashed:
        warning = font.render("UEBERZIEHEN - FAHRT UNTER 65 km/h", True, RED)
        surface.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 55))

    if show_axes and joystick:
        values = "  ".join(f"A{i}:{joystick.get_axis(i):+.2f}"
                           for i in range(joystick.get_numaxes()))
        surface.blit(font_small.render(values, True, YELLOW), (12, 106))

    if mission.phase in ("COMPLETE", "FAILED"):
        draw_evaluation(surface, aircraft, mission, font, font_small)

    if show_help:
        box = pygame.Surface((620, 420), pygame.SRCALPHA)
        box.fill((0, 0, 0, 220))
        pygame.draw.rect(box, GREEN, box.get_rect(), 1)
        lines = [
            "LINIEN-ACTIONFLUGSIMULATOR - STEUERUNG",
            "Joystick: Knueppel / Drehachse / Schubhebel",
            "Coolie-Hat: Blick links, rechts, oben, unten",
            "Tastatur: W/S Hoehe, A/D Quer, Q/E Seite",
            "+/- oder Bild auf/ab: Schub erhoehen/verringern",
            "LEERTASTE / Joystick-Knopf 0: Bordkanone",
            "J/L: Seitenblick    H/F1: diese Anleitung",
            "V: Effektton an/aus    P: Pause    R: Neustart",
            "F11/Alt+Enter: Vollbild    Esc: Vollbild verlassen/Ende",
            "",
            "MISSION",
            "Starten und die Drohne am Canyon-Eingang zerstoeren.",
            "Danach in den Canyon sinken und Wasserziele bekaempfen.",
            "Bruecken koennen ueber- oder unterflogen werden.",
            "Wasser, Canyonwaende und Bruecken sind gefaehrlich.",
            "Nach dem Canyon umkehren und sicher zum Flugplatz landen.",
            "Die Mission zaehlt nur mit sicherer Rueckkehr als Erfolg.",
            "",
            "Musikdateien liegen als WAV neben der Python-Datei.",
            "Fehlende Musik wird gemeldet; das Spiel laeuft weiter.",
        ]
        for i, line in enumerate(lines):
            color = YELLOW if i in (0, 10) else WHITE
            box.blit(font_small.render(line, True, color), (14, 10 + i * 19))
        surface.blit(box, (WIDTH - 638, 18))
    else:
        hint = font_small.render("H: ANLEITUNG", True, GREEN)
        panel = pygame.Surface((hint.get_width() + 20, 29), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 175))
        pygame.draw.rect(panel, GREEN, panel.get_rect(), 1)
        panel.blit(hint, (10, 6))
        surface.blit(panel, (WIDTH - panel.get_width() - 16, 18))


def draw_evaluation(surface, aircraft, mission, font, font_small):
    width, height = 470, 270
    box = pygame.Surface((width, height), pygame.SRCALPHA)
    box.fill((0, 0, 0, 232))
    pygame.draw.rect(box, GREEN if mission.safe_return else RED, box.get_rect(), 2)
    title = "MISSION ERFOLGREICH" if mission.safe_return else "MISSION NICHT BEENDET"
    lines = [
        title,
        f"Flugdrohne: {'abgeschossen' if not mission.drone.alive else 'nicht zerstoert'}",
        f"Wasserziele zerstoert: {mission.water_destroyed}/{len(mission.targets)}",
        f"Trefferquote: {mission.accuracy():.1f} % ({mission.hits}/{mission.shots})",
        f"Verbliebene Munition: {mission.ammo}/{MAX_AMMO}",
        f"Kollisionen/Beschaedigungen: {mission.collisions}/{aircraft.damage}",
        f"Sichere Rueckkehr: {'JA' if mission.safe_return else 'NEIN'}",
        f"Landung: {aircraft.landing_quality}",
        f"GESAMTPUNKTZAHL: {mission.score}",
        "R: neue Mission",
    ]
    for i, line in enumerate(lines):
        fnt = font if i in (0, 8) else font_small
        color = YELLOW if i in (0, 8) else WHITE
        rendered = fnt.render(line, True, color)
        box.blit(rendered, (width // 2 - rendered.get_width() // 2, 12 + i * 25))
    surface.blit(box, (WIDTH // 2 - width // 2, HEIGHT // 2 - height // 2 - 30))


def main():
    global WIDTH, HEIGHT
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.init()
    pygame.joystick.init()
    desktop = pygame.display.Info()
    WIDTH = min(WIDTH, max(960, desktop.current_w - 80))
    HEIGHT = min(HEIGHT, max(560, desktop.current_h - 100))
    windowed_size = (WIDTH, HEIGHT)
    screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
    pygame.display.set_caption("Linien-Actionflugsimulator")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 28)
    font_small = pygame.font.Font(None, 21)

    joystick = None
    if pygame.joystick.get_count():
        joystick = pygame.joystick.Joystick(0)
        joystick.init()

    aircraft = Aircraft()
    mission = Mission()
    sounds = SoundSystem()
    music = MusicController()
    throttle = 0.0
    view_yaw = view_pitch = 0.0
    show_help = False
    show_axes = False
    paused = False
    fullscreen = False
    running = True

    def set_fullscreen(enabled):
        nonlocal screen, fullscreen, windowed_size
        global WIDTH, HEIGHT
        if enabled:
            windowed_size = screen.get_size()
            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
        WIDTH, HEIGHT = screen.get_size()
        fullscreen = enabled

    def reset_game():
        nonlocal throttle
        aircraft.reset()
        mission.reset()
        music.stop()
        throttle = 0.0

    while running:
        dt = min(clock.tick(FPS) / 1000.0, 0.04)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE and not fullscreen:
                WIDTH = max(960, event.w)
                HEIGHT = max(560, event.h)
                windowed_size = (WIDTH, HEIGHT)
                screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if fullscreen:
                        set_fullscreen(False)
                    else:
                        running = False
                elif (event.key == pygame.K_F11
                      or (event.key == pygame.K_RETURN and event.mod & pygame.KMOD_ALT)):
                    set_fullscreen(not fullscreen)
                elif event.key in (pygame.K_h, pygame.K_F1):
                    show_help = not show_help
                elif event.key == pygame.K_F2:
                    show_axes = not show_axes
                elif event.key == pygame.K_v:
                    sounds.toggle()
                elif event.key == pygame.K_p:
                    paused = not paused
                elif event.key == pygame.K_r:
                    reset_game()

        keys = pygame.key.get_pressed()
        aileron, elevator, rudder = read_controls(joystick, keys)

        # Joystick-Schubhebel oder Tastatur. Bild auf/ab ist auf vielen
        # Tastaturen leichter erreichbar als das Pluszeichen.
        if joystick is not None and 0 <= JOY_AXIS_HEBEL < joystick.get_numaxes():
            raw = joystick.get_axis(JOY_AXIS_HEBEL)
            if JOY_INVERT_HEBEL:
                raw = -raw
            throttle = clamp((raw + 1.0) * 0.5, 0.0, 1.0)
        if (keys[pygame.K_PLUS] or keys[pygame.K_KP_PLUS]
                or keys[pygame.K_PAGEUP]):
            throttle = clamp(throttle + dt * 0.48, 0.0, 1.0)
        if (keys[pygame.K_MINUS] or keys[pygame.K_KP_MINUS]
                or keys[pygame.K_PAGEDOWN]):
            throttle = clamp(throttle - dt * 0.48, 0.0, 1.0)

        hat = (0, 0)
        if joystick is not None and joystick.get_numhats() > 0:
            hat = joystick.get_hat(0)
        if keys[pygame.K_j]:
            hat = (-1, hat[1])
        if keys[pygame.K_l]:
            hat = (1, hat[1])
        view_yaw = math.radians(hat[0] * 90)
        view_pitch = math.radians(hat[1] * 35)

        fire_pressed = keys[pygame.K_SPACE]
        if (joystick is not None and 0 <= JOY_BUTTON_FEUER < joystick.get_numbuttons()
                and joystick.get_button(JOY_BUTTON_FEUER)):
            fire_pressed = True

        if not paused:
            aircraft.update(dt, aileron, elevator, rudder, throttle)
            if fire_pressed:
                mission.fire(aircraft, sounds)
            mission.update(dt, aircraft, sounds)
            if mission.music_request:
                music.request(mission.music_request)
                mission.music_request = None
            music.update(dt)
        sounds.update(dt, aircraft)

        camera = Camera(aircraft, view_yaw, view_pitch)
        draw_sky_and_horizon(screen, camera, font_small)
        draw_world(screen, camera, aircraft, mission, font_small)
        draw_mission_objects(screen, camera, mission)

        # Visier nur beim Blick nach vorn.
        if hat == (0, 0):
            cx, cy = WIDTH // 2, HEIGHT // 2 - 38
            pygame.draw.circle(screen, GREEN, (cx, cy), 14, 1)
            pygame.draw.line(screen, GREEN, (cx - 24, cy), (cx - 7, cy), 1)
            pygame.draw.line(screen, GREEN, (cx + 7, cy), (cx + 24, cy), 1)
            pygame.draw.line(screen, GREEN, (cx, cy - 24), (cx, cy - 7), 1)
            pygame.draw.line(screen, GREEN, (cx, cy + 7), (cx, cy + 24), 1)

        draw_yaw_string(screen, aircraft, font_small)
        draw_instruments(screen, aircraft, mission, font, font_small)
        draw_mission_ui(screen, aircraft, mission, music, joystick, font, font_small,
                        show_help, show_axes)

        if aircraft.crashed:
            warning = pygame.font.Font(None, 48).render(
                "ABSTURZ - R FUER NEUSTART", True, RED)
            screen.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 150))
        if paused:
            pause = font.render("PAUSE", True, YELLOW)
            screen.blit(pause, (WIDTH // 2 - pause.get_width() // 2, 145))

        pygame.display.flip()

    music.stop()
    sounds.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
