#!/usr/bin/env python3
"""
Retro-Segelflugsimulator im Stil frueher Heimcomputer-Flugsimulatoren.

Enthaelt zwei Flugplaetze, manuell bedienten Windenstart, sichtbares Schleppseil,
Thermik mit Real- und Lernmodus, Segelflugpolare, Haubenfaden, Instrumente,
akustisches Variometer und synthetisch erzeugten Fahrtwind. Grafik und Ton
werden zur Laufzeit berechnet; weitere Dateien sind nicht erforderlich.

Installation:
    python3 -m pip install pygame-ce

Start:
    python3 linien_Segelflugsimulator.py

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
JOY_BUTTON_AUSKLINKEN = 0
JOY_BUTTON_WINDENSTART = 1
DEADZONE = 0.06

# ---------- Simulation ---------------------------------------------------
WIDTH, HEIGHT = 1280, 720
FPS = 60
GRID_STEP = 10.0                 # exakt 10 m pro Gitterfeld
STALL_SPEED = 65.0 / 3.6         # m/s
G = 9.81
NEAR = 1.0
FOV_DEG = 72.0
AIRCRAFT_GROUND_HEIGHT = 1.6

# ---------- Flugleistungen der ASK 21 ----------------------------------
ASK21_TRIM_SPEED = 85.0 / 3.6
ASK21_TRIM_PITCH = math.radians(-2.0)
ASK21_PITCH_STABILITY = 0.48
ASK21_MIN_SINK = 0.65
ASK21_MIN_SINK_SPEED = 67.0 / 3.6
ASK21_BEST_GLIDE_SPEED = 90.0 / 3.6
ASK21_BEST_GLIDE_RATIO = 34.0
# Bei Trimmfahrt gleichen sich Bahnneigung und aerodynamischer Widerstand aus.
ASK21_DRAG_COEFFICIENT = (G * abs(math.sin(ASK21_TRIM_PITCH))
                          / ASK21_TRIM_SPEED ** 2)

# ---------- Wetter (hier leicht von Hand einstellbar) -------------------
# Erlaubte Startwerte: "RUHIG", "NORMAL" oder "STARK".
WETTER_START = "NORMAL"
WETTER_REIHENFOLGE = ("RUHIG", "NORMAL", "STARK")
WETTER_PROFILE = {
    #                 Wind X  Wind Z  Thermikstaerke  Thermikbreite
    "RUHIG":  dict(wind_x=0.6, wind_z=0.2, staerke=0.72, breite=1.25),
    "NORMAL": dict(wind_x=1.4, wind_z=0.6, staerke=1.00, breite=1.55),
    "STARK":  dict(wind_x=2.4, wind_z=1.0, staerke=1.35, breite=1.85),
}

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
    Airport("NORDPLATZ", 0.0, 0.0, 0.0, 820.0, 20.0, WHITE, CYAN),
    Airport("WESTFELD", 2600.0, 2300.0, math.radians(-90.0),
            760.0, 24.0, YELLOW, ORANGE),
)


@dataclass(frozen=True)
class Thermal:
    base_x: float
    base_z: float
    strength: float
    radius: float
    top: float
    drift_x: float
    drift_z: float

    def center(self, height, sim_time):
        # Der Kern wandert mit dem Wind und neigt sich mit der Hoehe.
        time_factor = min(sim_time, 900.0)
        lean_time = clamp(height, 0.0, self.top) / 4.5
        return (self.base_x + self.drift_x * (time_factor + lean_time),
                self.base_z + self.drift_z * (time_factor + lean_time))

    def lift_at(self, x, y, z, sim_time):
        if y < 20.0 or y > self.top:
            return 0.0
        cx, cz = self.center(y, sim_time)
        distance = math.hypot(x - cx, z - cz)
        normalized = distance / self.radius
        lower_fade = clamp((y - 20.0) / 70.0, 0.0, 1.0)
        upper_fade = clamp((self.top - y) / 130.0, 0.0, 1.0)
        height_factor = min(lower_fade, upper_fade)
        if normalized <= 1.0:
            return self.strength * (1.0 - normalized ** 1.7) * height_factor
        if normalized <= 1.35:
            ring = math.sin((normalized - 1.0) / 0.35 * math.pi)
            return -0.55 * ring * height_factor
        return 0.0


THERMIK_GRUNDDATEN = (
    # X, Z, Grundstaerke, Grundradius, Obergrenze, Windfaktor X/Z
    (680, 760, 5.5, 175, 1050, 1.0, 1.0),
    (1500, 1450, 7.5, 230, 1450, 1.15, 0.9),
    (2240, 2050, 4.8, 155, 850, 0.8, 1.2),
    (1320, 2550, 6.4, 205, 1200, 1.0, 1.0),
    (-480, 1720, 5.3, 170, 980, 0.9, 1.0),
)


def build_weather(profile_name):
    """Erzeugt Wind und Thermikfelder aus einem gut lesbaren Wetterprofil."""
    profile = WETTER_PROFILE[profile_name]
    wind_x, wind_z = profile["wind_x"], profile["wind_z"]
    thermals = tuple(
        Thermal(x, z, strength * profile["staerke"], radius * profile["breite"],
                top, wind_x * drift_x, wind_z * drift_z)
        for x, z, strength, radius, top, drift_x, drift_z in THERMIK_GRUNDDATEN
    )
    return wind_x, wind_z, thermals


WIND_X, WIND_Z, THERMALS = build_weather(WETTER_START)


def airport_at(x, z, margin=0.0):
    for index, airport in enumerate(AIRPORTS):
        if airport.contains(x, z, margin):
            return index
    return None


@dataclass
class Aircraft:
    """Segelflugzeug mit Thermik-, Winden-, Seitenflug- und Landezustaenden."""
    x: float = 0.0
    y: float = AIRCRAFT_GROUND_HEIGHT
    z: float = 20.0
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    speed: float = 0.0
    vertical_speed: float = 0.0
    mode: str = "SEGLER"
    crashed: bool = False
    landed: bool = True
    outside_landing: bool = False
    flight_state: str = "WINCH_READY"
    active_airport: int = 0
    last_landed_airport: int = 0
    cable_attached: bool = True
    winch_elapsed: float = 0.0
    cable_tension: float = 0.0
    sideslip: float = 0.0
    own_sink: float = 0.0
    thermal_lift: float = 0.0
    wing_lift_difference: float = 0.0
    sim_time: float = 0.0

    def __post_init__(self):
        self.prepare_winch(self.active_airport)

    def reset(self):
        self.sim_time = 0.0
        self.last_landed_airport = 0
        self.prepare_winch(0)

    def prepare_winch(self, airport_index=None):
        """Am zuletzt erreichten Flugplatz einen Windenstart vorbereiten."""
        if airport_index is None:
            airport_index = self.last_landed_airport
        airport_index = int(clamp(airport_index, 0, len(AIRPORTS) - 1))
        airport = AIRPORTS[airport_index]
        self.active_airport = airport_index
        self.x, self.y, self.z = airport.point(0.0, 20.0, AIRCRAFT_GROUND_HEIGHT)
        self.heading = airport.heading
        self.pitch = 0.0
        self.roll = 0.0
        self.speed = 0.0
        self.vertical_speed = 0.0
        self.crashed = False
        self.landed = True
        self.outside_landing = False
        self.flight_state = "WINCH_READY"
        self.cable_attached = True
        self.winch_elapsed = 0.0
        self.cable_tension = 0.0
        self.sideslip = 0.0
        self.own_sink = 0.0
        self.thermal_lift = 0.0
        self.wing_lift_difference = 0.0

    def start_winch(self):
        if self.flight_state == "WINCH_READY" and self.cable_attached:
            self.flight_state = "WINCH_TOW"
            self.winch_elapsed = 0.0
            return True
        return False

    def release_cable(self):
        if not self.cable_attached:
            return False
        self.cable_attached = False
        self.cable_tension = 0.0
        if self.flight_state == "WINCH_TOW":
            self.flight_state = "FLYING"
            self.landed = False
        return True

    def winch_point(self):
        airport = AIRPORTS[self.active_airport]
        return airport.point(0.0, airport.length - 18.0, 0.7)

    def update(self, dt, aileron, elevator, rudder, lever):
        if self.crashed:
            return
        self.sim_time += dt

        if self.flight_state == "WINCH_READY":
            self.speed = 0.0
            self.vertical_speed = 0.0
            return

        if self.flight_state == "LANDED":
            self.speed = max(0.0, self.speed - (1.3 + 7.0 * lever) * dt)
            steering = math.radians(10.0) * rudder * clamp(self.speed / 15.0, 0.0, 1.0)
            self.heading = angle_wrap(self.heading + steering * dt)
            self.x += math.sin(self.heading) * self.speed * dt
            self.z += math.cos(self.heading) * self.speed * dt
            self.y = AIRCRAFT_GROUND_HEIGHT
            self.pitch = self.roll = self.vertical_speed = 0.0
            return

        # An einer Thermikkante geraet zuerst nur eine Tragflaeche in den
        # staerkeren Aufwind. Diese Flaeche hebt sich: Das Flugzeug rollt
        # dadurch zunaechst vom Thermikkern weg und muss gegengesteuert werden.
        half_span = 7.5
        right_x = self.x + math.cos(self.heading) * half_span
        right_z = self.z - math.sin(self.heading) * half_span
        left_x = self.x - math.cos(self.heading) * half_span
        left_z = self.z + math.sin(self.heading) * half_span
        right_lift = sum(t.lift_at(right_x, self.y, right_z, self.sim_time)
                         for t in THERMALS)
        left_lift = sum(t.lift_at(left_x, self.y, left_z, self.sim_time)
                        for t in THERMALS)
        self.wing_lift_difference = right_lift - left_lift

        authority = clamp(self.speed / STALL_SPEED, 0.25, 1.55)
        self.roll += math.radians(55.0) * aileron * authority * dt
        self.roll -= math.radians(8.0) * self.wing_lift_difference * dt
        self.pitch += math.radians(22.0) * elevator * authority * dt
        self.roll *= max(0.0, 1.0 - 0.17 * dt)
        if self.flight_state == "FLYING":
            # Laengsstabilitaet: zu langsam -> Nase senken, zu schnell -> heben.
            speed_correction = math.radians(0.30) * (self.speed - ASK21_TRIM_SPEED)
            neutral_pitch = clamp(ASK21_TRIM_PITCH + speed_correction,
                                  math.radians(-8.0), math.radians(3.0))
            self.pitch += ((neutral_pitch - self.pitch)
                           * ASK21_PITCH_STABILITY * dt)
        else:
            # Im Windenstart bestimmt der Pilot den steilen Bahnwinkel.
            self.pitch *= max(0.0, 1.0 - 0.025 * dt)
        self.roll = clamp(self.roll, math.radians(-82), math.radians(82))
        self.pitch = clamp(self.pitch, math.radians(-38), math.radians(42))

        bank_turn = G * math.tan(self.roll) / max(self.speed, 8.0)
        coordinated_rudder = clamp(math.sin(self.roll) * 0.62, -0.75, 0.75)
        slip_target = math.radians(13.0) * (rudder - coordinated_rudder - 0.32 * aileron)
        slip_response = 1.0 - math.exp(-dt / 0.55)
        self.sideslip += (slip_target - self.sideslip) * slip_response
        yaw_rate = bank_turn + math.radians(12.0) * rudder - 0.22 * self.sideslip
        self.heading = angle_wrap(self.heading + yaw_rate * dt)

        base_drag = ASK21_DRAG_COEFFICIENT * self.speed * self.speed
        spoiler_drag = 6.5 * lever
        slope_accel = -G * math.sin(self.pitch)
        cable_vertical = 0.0
        cable_acceleration = 0.0

        if self.cable_attached:
            wx, wy, wz = self.winch_point()
            dx, dy, dz = wx - self.x, wy - self.y, wz - self.z
            cable_length = max(1.0, math.sqrt(dx * dx + dy * dy + dz * dz))
            ux, uy, uz = dx / cable_length, dy / cable_length, dz / cable_length
            alignment = ux * math.sin(self.heading) + uz * math.cos(self.heading)
            if self.flight_state == "WINCH_TOW":
                self.winch_elapsed += dt
                ramp = clamp(self.winch_elapsed / 1.6, 0.0, 1.0)
                speed_factor = clamp((42.0 - self.speed) / 22.0, 0.25, 1.0)
                self.cable_tension = 18.0 * ramp * speed_factor
            else:
                # Ein vergessenes Seil bleibt am Windenpunkt haengen.
                self.cable_tension = clamp(5.0 + cable_length / 180.0, 5.0, 18.0)
            cable_acceleration = self.cable_tension * alignment
            cable_vertical = self.cable_tension * uy * 0.48
        else:
            self.cable_tension = 0.0

        self.speed = max(3.0, self.speed +
                         (cable_acceleration - base_drag - spoiler_drag + slope_accel) * dt)

        stall_ratio = clamp(self.speed / STALL_SPEED, 0.0, 1.0)
        stall_sink = (1.0 - stall_ratio) ** 2 * 12.0
        if self.speed < STALL_SPEED:
            self.pitch -= math.radians(18.0) * (1.0 - stall_ratio) * dt

        # Polare: 0,65 m/s geringstes Sinken bei etwa 67 km/h und
        # rund 0,74 m/s bei 90 km/h, entsprechend einer Gleitzahl von 34.
        speed_penalty = 0.0021 * (self.speed - ASK21_MIN_SINK_SPEED) ** 2
        bank_factor = 1.0 / max(0.32, math.cos(self.roll))
        self.own_sink = ((ASK21_MIN_SINK + speed_penalty) * bank_factor ** 1.45
                         + 4.8 * lever)
        self.thermal_lift = sum(t.lift_at(self.x, self.y, self.z, self.sim_time)
                                for t in THERMALS)
        # ASK21_TRIM_PITCH ist die sichtbare Flugzeuglage im stabilen Gleiten;
        # die Polare liefert dabei bereits das tatsaechliche Eigensinken.
        pitch_exchange = self.speed * math.sin(self.pitch - ASK21_TRIM_PITCH)
        self.vertical_speed = (pitch_exchange + self.thermal_lift
                               - self.own_sink - stall_sink + cable_vertical)

        horizontal_speed = self.speed * math.cos(self.pitch)
        self.x += (math.sin(self.heading) * horizontal_speed + WIND_X) * dt
        self.z += (math.cos(self.heading) * horizontal_speed + WIND_Z) * dt
        self.y += self.vertical_speed * dt

        if (self.flight_state == "WINCH_TOW"
                and self.y <= AIRCRAFT_GROUND_HEIGHT + 0.08
                and (self.speed < STALL_SPEED or self.pitch < math.radians(2.0))):
            self.y = AIRCRAFT_GROUND_HEIGHT
            self.vertical_speed = 0.0
            self.roll *= max(0.0, 1.0 - 1.8 * dt)
            return
        if self.flight_state == "WINCH_TOW" and self.y > AIRCRAFT_GROUND_HEIGHT + 0.08:
            self.landed = False

        if self.y <= AIRCRAFT_GROUND_HEIGHT:
            landed_airport = airport_at(self.x, self.z, margin=1.5)
            hard = (self.vertical_speed < -3.5
                    or abs(self.roll) > math.radians(12.0)
                    or self.speed > 45.0)
            cable_danger = self.cable_attached and self.cable_tension > 9.0
            if hard or cable_danger:
                self.crashed = True
                self.flight_state = "CRASHED"
                self.y = AIRCRAFT_GROUND_HEIGHT
            else:
                self.y = AIRCRAFT_GROUND_HEIGHT
                self.pitch = 0.0
                self.roll = 0.0
                self.landed = True
                self.flight_state = "LANDED"
                self.outside_landing = landed_airport is None
                if landed_airport is not None:
                    self.last_landed_airport = landed_airport
                    self.active_airport = landed_airport
                self.vertical_speed = 0.0



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


def draw_world(surface, camera, aircraft, font_small):
    # Bewegliches 10-m-Gitter um das Flugzeug; staerkere Linien alle 50/100 m.
    extent_side = 500
    z_back = int(math.floor((aircraft.z - 200) / GRID_STEP) * GRID_STEP)
    z_front = int(math.ceil((aircraft.z + 1400) / GRID_STEP) * GRID_STEP)
    x_left = int(math.floor((aircraft.x - extent_side) / GRID_STEP) * GRID_STEP)
    x_right = int(math.ceil((aircraft.x + extent_side) / GRID_STEP) * GRID_STEP)

    for x in range(x_left, x_right + 1, int(GRID_STEP)):
        color = GREEN if x % 50 else BRIGHT_GREEN
        width = 1 if x % 100 else 2
        clipped_line(surface, camera, (x, 0, z_back), (x, 0, z_front), color, width)
    for z in range(z_back, z_front + 1, int(GRID_STEP)):
        color = DARK_GREEN if z % 50 else GREEN
        width = 1 if z % 100 else 2
        clipped_line(surface, camera, (x_left, 0, z), (x_right, 0, z), color, width)

    # Beide Flugplaetze mit eigener Farbe, Windsack-/Hallenumgebung und Winde.
    for airport_index, airport in enumerate(AIRPORTS):
        draw_airport(surface, camera, airport, airport_index, font_small)

    # Natuerliche Thermikhinweise bleiben auch im Realmodus sichtbar.
    draw_thermal_landmarks(surface, camera, aircraft)

    # Einige einfache Orientierungspunkte aus Drahtgitterlinien.
    draw_box(surface, camera, (-170, 0, 260), (35, 28, 35), CYAN)
    draw_box(surface, camera, (145, 0, 520), (55, 18, 40), YELLOW)
    draw_pylon(surface, camera, (260, 0, 160), 65, RED)

    # Der zweite Platz hat eine andere Umgebung und ist aus der Ferne erkennbar.
    draw_box(surface, camera, (2460, 0, 2170), (70, 24, 42), ORANGE)
    draw_box(surface, camera, (2650, 0, 2390), (32, 42, 32), YELLOW)
    draw_pylon(surface, camera, (2720, 0, 2250), 85, CYAN)


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

    # Kleine Drahtgitterwinde am Bahnende.
    winch = airport.point(0, airport.length - 18, 0)
    draw_box(surface, camera, (winch[0] - 3, 0, winch[2] - 3),
             (6, 4, 6), airport.marker_color)
    label = camera.project(airport.point(0, 15, 0.2))
    if label and -200 < label[0] < WIDTH + 200 and -100 < label[1] < HEIGHT:
        text = font_small.render(f"{airport_index + 1} {airport.name}", True, airport.color)
        surface.blit(text, (label[0] - text.get_width() // 2, label[1]))


def draw_thermal_landmarks(surface, camera, aircraft):
    """Kornfelder fuer niedrige und Wolken fuer hochreichende Thermik."""
    for thermal in THERMALS:
        if thermal.top <= 1000.0:
            draw_cornfield(surface, camera, thermal)
        else:
            cx, cz = thermal.center(thermal.top, aircraft.sim_time)
            if math.hypot(cx - aircraft.x, cz - aircraft.z) <= 3600.0:
                draw_wire_cloud(surface, camera, cx, thermal.top, cz,
                                max(75.0, thermal.radius * 0.48))


def draw_cornfield(surface, camera, thermal):
    """Gelbes Bodengitter als stationaerer, sonnenwarmer Thermikausloeser."""
    half_x = clamp(thermal.radius * 0.52, 85.0, 180.0)
    half_z = clamp(thermal.radius * 0.38, 65.0, 135.0)
    left, right = thermal.base_x - half_x, thermal.base_x + half_x
    back, front = thermal.base_z - half_z, thermal.base_z + half_z
    field_color = (205, 175, 45)
    border_color = (245, 210, 65)
    for x in range(int(left), int(right) + 1, 20):
        clipped_line(surface, camera, (x, 0.12, back), (x, 0.12, front),
                     field_color, 1)
    for z in range(int(back), int(front) + 1, 20):
        clipped_line(surface, camera, (left, 0.12, z), (right, 0.12, z),
                     field_color, 1)
    corners = ((left, 0.14, back), (right, 0.14, back),
               (right, 0.14, front), (left, 0.14, front))
    for index in range(4):
        clipped_line(surface, camera, corners[index], corners[(index + 1) % 4],
                     border_color, 2)


def draw_wire_cloud(surface, camera, x, y, z, size):
    """Lockere Wolke aus wenigen dreidimensionalen Retro-Drahtgitterloben."""
    lobes = ((-0.62, 0.00, 0.56), (-0.22, -0.12, 0.72),
             (0.28, 0.05, 0.82), (0.72, -0.02, 0.52),
             (0.05, 0.14, 1.00))
    color = (190, 205, 210)
    for offset_x, offset_z, scale in lobes:
        cx = x + offset_x * size
        cz = z + offset_z * size
        cy = y + (scale - 0.55) * size * 0.28
        rx, rz, ry = size * scale, size * scale * 0.58, size * scale * 0.36
        horizontal = []
        front_arc = []
        side_arc = []
        for step in range(17):
            angle = step * 2.0 * math.pi / 16.0
            horizontal.append((cx + math.cos(angle) * rx, cy,
                               cz + math.sin(angle) * rz))
            front_arc.append((cx + math.cos(angle) * rx,
                              cy + math.sin(angle) * ry, cz))
            side_arc.append((cx, cy + math.sin(angle) * ry,
                             cz + math.cos(angle) * rz))
        for ring in (horizontal, front_arc, side_arc):
            for p1, p2 in zip(ring, ring[1:]):
                clipped_line(surface, camera, p1, p2, color, 1)


def draw_cable(surface, camera, aircraft):
    if not aircraft.cable_attached:
        return
    # Das Seil verlaeuft vom Bug zur ortsfesten Winde und zeigt so Winkel/Auslenkung.
    nose = (aircraft.x, aircraft.y - 0.35, aircraft.z)
    clipped_line(surface, camera, nose, aircraft.winch_point(), (205, 185, 120), 2)


def draw_flight_path(surface, camera, flight_path):
    """Zeichnet die wirklich geflogene dreidimensionale Spur in die Aussenwelt."""
    if len(flight_path) < 2:
        return
    # Aeltere Teile sind dunkler, der zuletzt geflogene Abschnitt leuchtet.
    start = max(1, len(flight_path) - 2400)
    for index in range(start, len(flight_path)):
        age = len(flight_path) - index
        if age < 160:
            color, width = (205, 105, 245), 2
        elif age < 600:
            color, width = (120, 85, 180), 1
        else:
            color, width = (65, 55, 95), 1
        clipped_line(surface, camera, flight_path[index - 1],
                     flight_path[index], color, width)


def draw_thermals(surface, camera, aircraft):
    """Durchsichtige Retro-Spiralen und Ringe fuer den Lernmodus."""
    overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    for thermal in THERMALS:
        cx_here, cz_here = thermal.center(aircraft.y, aircraft.sim_time)
        if math.hypot(cx_here - aircraft.x, cz_here - aircraft.z) > 1500.0:
            continue
        color = (70, 205, 225, 52) if thermal.strength < 4.0 else (245, 155, 55, 62)
        previous = None
        for height in range(50, int(thermal.top), 42):
            cx, cz = thermal.center(height, aircraft.sim_time)
            angle = height * 0.045
            radius = thermal.radius * 0.48
            point = (cx + math.cos(angle) * radius, height,
                     cz + math.sin(angle) * radius)
            if previous is not None:
                clipped_line(overlay, camera, previous, point, color, 2)
            previous = point
        for height in range(120, int(thermal.top), 260):
            cx, cz = thermal.center(height, aircraft.sim_time)
            for ring_radius, alpha in ((thermal.radius * 0.35, 76),
                                       (thermal.radius, 44),
                                       (thermal.radius * 1.35, 30)):
                points = []
                for step in range(17):
                    angle = step * 2.0 * math.pi / 16.0
                    points.append((cx + math.cos(angle) * ring_radius, height,
                                   cz + math.sin(angle) * ring_radius))
                ring_color = (color[0], color[1], color[2], alpha)
                for p1, p2 in zip(points, points[1:]):
                    clipped_line(overlay, camera, p1, p2, ring_color, 1)
    surface.blit(overlay, (0, 0))


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
    """Erzeugt Variometer und Fahrtwind vollstaendig aus berechneten Samples."""
    def __init__(self):
        self.available = False
        self.enabled = False
        self.flow_index = -1
        self.sink_index = -1
        self.vario_timer = 0.0
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(44100, -16, 2, 512)
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            self.flow_channel = pygame.mixer.Channel(0)
            self.stall_channel = pygame.mixer.Channel(1)
            self.vario_channel = pygame.mixer.Channel(2)
            self.flow_sounds = [self._noise(0.65, i / 6.0, 100 + i) for i in range(7)]
            self.stall_sound = self._noise(0.70, 0.18, 812, buffet=True)
            self.climb_sounds = [self._tone(520 + i * 85, 0.12) for i in range(8)]
            self.sink_sounds = [self._tone(freq, 0.55) for freq in (285, 255, 225, 195, 170)]
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

    def toggle(self):
        if not self.available:
            return
        self.enabled = not self.enabled
        if not self.enabled:
            self.stop()

    def stop(self):
        if pygame.mixer.get_init():
            for channel_number in (0, 1, 2):
                pygame.mixer.Channel(channel_number).stop()
        self.flow_index = self.sink_index = -1

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
        self.flow_channel.set_volume(flow_volume)

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
                self.vario_channel.set_volume(0.36)
                self.vario_timer = interval
        elif vertical < -0.45:
            index = int(clamp((-vertical - 0.45) / 1.35, 0.0, 4.0))
            if index != self.sink_index:
                self.vario_channel.stop()
                self.vario_channel.play(self.sink_sounds[index], loops=-1)
                self.vario_channel.set_volume(0.22)
                self.sink_index = index
        else:
            self.vario_channel.stop()
            self.sink_index = -1
            self.vario_timer = 0.0


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


def draw_instruments(surface, aircraft, lever, font, font_small):
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

    # Schub-/Stoerklappenhebel und Statusdaten.
    bar_x, bar_y, bar_w, bar_h = 675, panel_y + 25, 25, 90
    pygame.draw.rect(surface, GREY, (bar_x, bar_y, bar_w, bar_h), 2)
    fill = int((bar_h - 4) * lever)
    pygame.draw.rect(surface, RED,
                     (bar_x + 3, bar_y + bar_h - 3 - fill, bar_w - 6, fill))
    lever_name = "STOER"
    surface.blit(font_small.render(lever_name, True, WHITE), (710, bar_y + 5))
    state_names = {"WINCH_READY": "WINDE BEREIT", "WINCH_TOW": "WINDENSTART",
                   "FLYING": "FREIFLUG", "LANDED": "GELANDET", "CRASHED": "BRUCH"}
    state = state_names.get(aircraft.flight_state, aircraft.flight_state)
    surface.blit(font.render(state, True, YELLOW), (710, bar_y + 29))
    surface.blit(font_small.render(
        f"Thermik {aircraft.thermal_lift:+.1f}  Eigensinken {aircraft.own_sink:.1f} m/s",
        True, CYAN), (710, bar_y + 57))
    surface.blit(font_small.render(
        f"X {aircraft.x:+7.0f} m  Z {aircraft.z:+7.0f} m  Platz {aircraft.active_airport + 1}",
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


def main():
    global WIDTH, HEIGHT, WIND_X, WIND_Z, THERMALS
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.init()
    pygame.joystick.init()
    desktop = pygame.display.Info()
    WIDTH = min(WIDTH, max(960, desktop.current_w - 80))
    HEIGHT = min(HEIGHT, max(560, desktop.current_h - 100))
    windowed_size = (WIDTH, HEIGHT)
    screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
    pygame.display.set_caption("Linien-Segelflugsimulator")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 28)
    font_small = pygame.font.Font(None, 21)

    joystick = None
    if pygame.joystick.get_count():
        joystick = pygame.joystick.Joystick(0)
        joystick.init()

    aircraft = Aircraft()
    sounds = SoundSystem()
    lever = 0.0
    view_yaw = 0.0
    view_pitch = 0.0
    # Beim Start nur einen kleinen Hinweis zeigen; H/F1 klappt die Anleitung auf.
    show_help = False
    show_axes = False
    show_thermals = False
    show_flight_path = False
    flight_path = []
    flight_path_timer = 0.0
    weather_name = WETTER_START
    paused = False
    fullscreen = False
    start_notice = 0.0
    start_button_previous = False
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
                elif event.key == pygame.K_h or event.key == pygame.K_F1:
                    show_help = not show_help
                elif event.key == pygame.K_F2:
                    show_axes = not show_axes
                elif event.key == pygame.K_F4:
                    if aircraft.flight_state == "LANDED" and not aircraft.outside_landing:
                        aircraft.prepare_winch(aircraft.last_landed_airport)
                        lever = 0.0
                        flight_path.clear()
                        flight_path_timer = 0.0
                elif event.key in (pygame.K_F5, pygame.K_g):
                    if aircraft.start_winch():
                        start_notice = 2.5
                elif event.key == pygame.K_SPACE:
                    aircraft.release_cable()
                elif event.key == pygame.K_t:
                    show_thermals = not show_thermals
                elif event.key == pygame.K_F6:
                    weather_index = ((WETTER_REIHENFOLGE.index(weather_name) + 1)
                                     % len(WETTER_REIHENFOLGE))
                    weather_name = WETTER_REIHENFOLGE[weather_index]
                    WIND_X, WIND_Z, THERMALS = build_weather(weather_name)
                elif event.key == pygame.K_F7:
                    show_flight_path = not show_flight_path
                elif event.key == pygame.K_v:
                    sounds.toggle()
                elif event.key == pygame.K_r:
                    aircraft.reset()
                    lever = 0.0
                    flight_path.clear()
                    flight_path_timer = 0.0
                elif event.key == pygame.K_p:
                    paused = not paused

        keys = pygame.key.get_pressed()
        aileron, elevator, rudder = read_controls(joystick, keys)

        # Analoger Hebel oder Tastatur +/-.
        if joystick is not None and 0 <= JOY_AXIS_HEBEL < joystick.get_numaxes():
            raw = joystick.get_axis(JOY_AXIS_HEBEL)
            if JOY_INVERT_HEBEL:
                raw = -raw
            lever = clamp((raw + 1.0) * 0.5, 0.0, 1.0)
        if keys[pygame.K_PLUS] or keys[pygame.K_KP_PLUS]:
            lever = clamp(lever + dt * 0.55, 0, 1)
        if keys[pygame.K_MINUS] or keys[pygame.K_KP_MINUS]:
            lever = clamp(lever - dt * 0.55, 0, 1)

        # Coolie-Hat: Blick links/rechts, oben/unten; beim Loslassen nach vorn.
        hat = (0, 0)
        if joystick is not None and joystick.get_numhats() > 0:
            hat = joystick.get_hat(0)
        if keys[pygame.K_j]:
            hat = (-1, hat[1])
        if keys[pygame.K_l]:
            hat = (1, hat[1])
        view_yaw = math.radians(hat[0] * 90)
        view_pitch = math.radians(hat[1] * 35)

        if not paused:
            joystick_start = (joystick is not None
                              and 0 <= JOY_BUTTON_WINDENSTART < joystick.get_numbuttons()
                              and joystick.get_button(JOY_BUTTON_WINDENSTART))
            if joystick_start and not start_button_previous:
                if aircraft.start_winch():
                    start_notice = 2.5
            start_button_previous = bool(joystick_start)
            aircraft.update(dt, aileron, elevator, rudder, lever)
            flight_path_timer -= dt
            if (aircraft.flight_state in ("WINCH_TOW", "FLYING")
                    and flight_path_timer <= 0.0):
                point = (aircraft.x, aircraft.y, aircraft.z)
                if (not flight_path
                        or math.dist(point, flight_path[-1]) >= 4.0):
                    flight_path.append(point)
                    if len(flight_path) > 2400:
                        del flight_path[:200]
                flight_path_timer = 0.22
            joystick_release = (joystick is not None
                                and 0 <= JOY_BUTTON_AUSKLINKEN < joystick.get_numbuttons()
                                and joystick.get_button(JOY_BUTTON_AUSKLINKEN))
            if joystick_release:
                aircraft.release_cable()

        start_notice = max(0.0, start_notice - dt)

        sounds.update(dt, aircraft)

        camera = Camera(aircraft, view_yaw, view_pitch)
        draw_sky_and_horizon(screen, camera, font_small)
        draw_world(screen, camera, aircraft, font_small)
        if show_flight_path:
            draw_flight_path(screen, camera, flight_path)
        if show_thermals:
            draw_thermals(screen, camera, aircraft)
        draw_cable(screen, camera, aircraft)

        # Einfaches Visier und Blickrichtungsanzeige.
        if hat == (0, 0):
            pygame.draw.line(screen, GREEN, (WIDTH // 2 - 14, HEIGHT // 2 - 40),
                             (WIDTH // 2 + 14, HEIGHT // 2 - 40), 1)
        direction = "VORN"
        if hat[0] < 0:
            direction = "BLICK LINKS"
        elif hat[0] > 0:
            direction = "BLICK RECHTS"
        elif hat[1] > 0:
            direction = "BLICK OBEN"
        elif hat[1] < 0:
            direction = "BLICK UNTEN"
        screen.blit(font_small.render(direction, True, GREEN), (12, 12))

        joy_name = joystick.get_name() if joystick else "kein Joystick - Tastatur aktiv"
        screen.blit(font_small.render(joy_name, True, GREY), (12, 34))
        thermal_mode = ("LERNMODUS: THERMIK SICHTBAR" if show_thermals
                        else "REALMODUS: THERMIK UNSICHTBAR")
        thermal_mode += "   FLUGSPUR " + ("AN" if show_flight_path else "AUS")
        screen.blit(font_small.render(thermal_mode, True, CYAN if show_thermals else GREY), (12, 58))
        cable_text = "SEIL EINGEKLINKT" if aircraft.cable_attached else "SEIL FREI"
        cable_color = RED if aircraft.cable_attached and aircraft.flight_state == "FLYING" else YELLOW
        screen.blit(font_small.render(
            f"{cable_text}  ZUG {aircraft.cable_tension:4.1f}  WETTER {weather_name}  "
            f"WIND {WIND_X:+.1f}/{WIND_Z:+.1f} m/s",
            True, cable_color), (12, 80))
        navigation = []
        for index, airport in enumerate(AIRPORTS):
            distance = math.hypot(airport.x - aircraft.x, airport.z - aircraft.z) / 1000.0
            bearing = math.degrees(math.atan2(airport.x - aircraft.x,
                                             airport.z - aircraft.z)) % 360.0
            navigation.append(f"P{index + 1} {distance:.1f}km/{bearing:03.0f}Grad")
        screen.blit(font_small.render("   ".join(navigation), True, GREEN), (12, 104))
        draw_yaw_string(screen, aircraft, font_small)
        draw_instruments(screen, aircraft, lever, font, font_small)

        if aircraft.speed < STALL_SPEED and not aircraft.crashed and not aircraft.landed:
            warning = font.render("UEBERZIEHEN - FAHRT UNTER 65 km/h", True, RED)
            screen.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 55))
        if aircraft.flight_state == "WINCH_READY":
            ground_text = f"{AIRPORTS[aircraft.active_airport].name}: F5 ODER G = WINDENSTART"
            message = font.render(ground_text, True, GREEN)
            screen.blit(message, (WIDTH // 2 - message.get_width() // 2, 25))
        elif aircraft.flight_state == "WINCH_TOW":
            ground_text = "WINDENSTART - ZIEHEN, FLUEGEL HALTEN, LEERTASTE AUSKLINKEN"
            message = font.render(ground_text, True, YELLOW)
            screen.blit(message, (WIDTH // 2 - message.get_width() // 2, 25))
        elif aircraft.flight_state == "LANDED":
            if aircraft.outside_landing:
                ground_text = "AUSSENLANDUNG - SICHER AUFGESETZT - R FUER RUECKTRANSPORT"
            else:
                ground_text = (f"GELANDET AUF {AIRPORTS[aircraft.last_landed_airport].name}"
                               " - F4 NEUEN WINDENSTART VORBEREITEN")
            message = font.render(ground_text, True, GREEN)
            screen.blit(message, (WIDTH // 2 - message.get_width() // 2, 25))
        if start_notice > 0.0:
            notice = pygame.font.Font(None, 38).render(
                "STARTSIGNAL EMPFANGEN - WINDE ZIEHT", True, YELLOW)
            screen.blit(notice, (WIDTH // 2 - notice.get_width() // 2, 62))
        if aircraft.crashed:
            warning = pygame.font.Font(None, 54).render("BRUCHLANDUNG - R ZUM NEUSTART", True, RED)
            screen.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 105))
        if paused:
            pause_text = font.render("PAUSE", True, YELLOW)
            screen.blit(pause_text, (WIDTH // 2 - pause_text.get_width() // 2, 90))

        if show_axes and joystick:
            values = "  ".join(f"A{i}:{joystick.get_axis(i):+.2f}"
                               for i in range(joystick.get_numaxes()))
            screen.blit(font_small.render(values, True, YELLOW), (12, 128))

        if show_help:
            box = pygame.Surface((590, 398), pygame.SRCALPHA)
            box.fill((0, 0, 0, 215))
            pygame.draw.rect(box, GREEN, box.get_rect(), 1)
            lines = [
                "RETRO-SEGEFLUG - STEUERUNG",
                "Joystick: Knueppel / Seitenruder / Hebel",
                "Coolie-Hat: Blick links, rechts, oben, unten",
                "Tastatur: W/S Hoehe, A/D Quer, Q/E Seite",
                "+/- Stoerklappen    J/L Seitenblick",
                "F4 nach Landung: Windenstart am erreichten Platz vorbereiten",
                "F5 oder G / Joystick-Knopf 1: Windenstart ausloesen",
                "Beim Beschleunigen ziehen; Quer- und Seitenruder koordinieren",
                "LEERTASTE / Joystick-Knopf 0: Seil manuell ausklinken",
                "Ohne Ausklinken bleibt das Seil wirksam - R setzt sicher zurueck",
                "T: Realmodus / sichtbare Thermik (Lernmodus)",
                "Kornfelder/Wolken bleiben im Realmodus als Suchhinweise sichtbar",
                "F6: Wetter RUHIG / NORMAL / STARK umschalten",
                "F7: dreidimensionale geflogene Flugspur ein-/ausblenden",
                "In der Thermik kreisen; Vario = Thermik minus Eigensinken",
                "Thermikkante hebt eine Flaeche: mit Querruder gegenhalten",
                "Haubenfaden mit Seitenruder mittig halten",
                "V Ton ein/aus    P Pause    R Neustart auf Platz 1",
                "F11/Alt+Enter Vollbild    Esc zurueck",
                "F2 rohe Achsenwerte    F1/H Hilfe",
                "Ziel: von NORDPLATZ nach WESTFELD und zurueck",
                "Landung: <162 km/h, Sinken <3.5 m/s, Fluegel eben",
                "Abseits der Pisten: sichere AUSSENLANDUNG, R = Ruecktransport",
            ]
            for i, line in enumerate(lines):
                color = YELLOW if i == 0 else WHITE
                box.blit(font_small.render(line, True, color), (14, 8 + i * 16))
            screen.blit(box, (WIDTH - 608, 18))
        else:
            small_help = pygame.Surface((174, 29), pygame.SRCALPHA)
            small_help.fill((0, 0, 0, 175))
            pygame.draw.rect(small_help, GREEN, small_help.get_rect(), 1)
            hint = font_small.render("H: ANLEITUNG", True, GREEN)
            small_help.blit(hint, (10, 6))
            screen.blit(small_help, (WIDTH - 192, 18))

        pygame.display.flip()

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
