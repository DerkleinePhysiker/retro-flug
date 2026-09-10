#!/usr/bin/env python3
"""
Linien-Flugsimulator im Stil frueher Heimcomputer-Flugsimulatoren.

Installation:
    python3 -m pip install pygame-ce

Start:
    python3 linien_flugsimulator.py

Die vier JOY_AXIS-Konstanten unten ggf. an das eigene Thrustmaster-Modell
anpassen. Im laufenden Programm zeigt F2 alle rohen Achswerte an.
"""

import math
import os
import sys
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
JOY_INVERT_HEBEL = True
DEADZONE = 0.06

# ---------- Simulation ---------------------------------------------------
WIDTH, HEIGHT = 1280, 720
FPS = 60
GRID_STEP = 10.0                 # exakt 10 m pro Gitterfeld
STALL_SPEED = 65.0 / 3.6         # m/s
G = 9.81
NEAR = 1.0
FOV_DEG = 72.0
CANNON_SPEED = 260.0              # m/s relativ zum Flugzeug
CANNON_INTERVAL = 0.14            # Sekunden zwischen zwei Schuessen

BLACK = (3, 6, 7)
GREEN = (35, 160, 76)
DARK_GREEN = (18, 72, 40)
BRIGHT_GREEN = (90, 235, 125)
WHITE = (230, 238, 225)
YELLOW = (242, 210, 70)
RED = (240, 75, 65)
CYAN = (75, 205, 220)
GREY = (90, 105, 98)


def clamp(value, low, high):
    return max(low, min(high, value))


def deadzone(value):
    if abs(value) < DEADZONE:
        return 0.0
    return math.copysign((abs(value) - DEADZONE) / (1.0 - DEADZONE), value)


def angle_wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass
class Aircraft:
    x: float = 0.0
    y: float = 80.0
    z: float = -350.0
    heading: float = 0.0
    pitch: float = math.radians(-2.0)
    roll: float = 0.0
    speed: float = 105.0 / 3.6
    vertical_speed: float = 0.0
    mode: str = "SEGLER"
    crashed: bool = False
    landed: bool = False

    def reset(self):
        self.x, self.y, self.z = 0.0, 80.0, -350.0
        self.heading = 0.0
        self.pitch = math.radians(-2.0)
        self.roll = 0.0
        self.speed = 105.0 / 3.6
        self.vertical_speed = 0.0
        self.crashed = False
        self.landed = False

    def jet_start(self):
        """Duesenflugzeug am Anfang der Startbahn bereitstellen."""
        self.x, self.y, self.z = 0.0, 1.6, 35.0
        self.heading = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.speed = 0.0
        self.vertical_speed = 0.0
        self.mode = "JET"
        self.crashed = False
        self.landed = True

    def update(self, dt, aileron, elevator, rudder, lever):
        if self.crashed:
            return

        # Nach einer gelungenen Landung nur noch am Boden ausrollen. Ohne
        # diesen eigenen Zustand wuerde die Stallphysik unter 65 km/h erneut
        # eine Sinkgeschwindigkeit erzeugen und nachtraeglich "Bruchlandung"
        # ausloesen.
        if self.landed:
            if self.mode == "JET":
                acceleration = 12.0 * lever - 0.8 - 0.002 * self.speed ** 2
            else:
                acceleration = -(2.0 + 7.0 * lever)
            self.speed = max(0.0, self.speed + acceleration * dt)
            steering = math.radians(12.0) * rudder * clamp(self.speed / 15.0, 0.0, 1.0)
            self.heading = angle_wrap(self.heading + steering * dt)
            self.x += math.sin(self.heading) * self.speed * dt
            self.z += math.cos(self.heading) * self.speed * dt
            self.y = 1.6
            self.pitch = 0.0
            self.roll = 0.0
            self.vertical_speed = 0.0
            # Ab etwa 80 km/h kann der Jet durch Ziehen abheben.
            if self.mode == "JET" and self.speed >= 80.0 / 3.6 and elevator > 0.12:
                self.landed = False
                self.y = 1.7
                self.pitch = math.radians(5.0)
            return

        # Direkte, gut beherrschbare Ruderwirkung; mit der Fahrt skaliert.
        authority = clamp(self.speed / STALL_SPEED, 0.35, 1.6)
        self.roll += math.radians(62.0) * aileron * authority * dt
        self.pitch += math.radians(25.0) * elevator * authority * dt
        self.roll *= max(0.0, 1.0 - 0.18 * dt)
        self.pitch *= max(0.0, 1.0 - 0.025 * dt)
        self.roll = clamp(self.roll, math.radians(-82), math.radians(82))
        self.pitch = clamp(self.pitch, math.radians(-45), math.radians(38))

        # Kurvenflug durch Querneigung plus direkte Seitenruderwirkung.
        bank_turn = G * math.tan(self.roll) / max(self.speed, 8.0)
        self.heading += (bank_turn + math.radians(18) * rudder) * dt
        self.heading = angle_wrap(self.heading)

        # Hebel: Jet = Schub (0..1), Segler = Stoerklappen (0..1).
        base_drag = 0.0027 * self.speed * self.speed
        slope_accel = -G * math.sin(self.pitch)
        if self.mode == "JET":
            acceleration = 12.0 * lever - base_drag + slope_accel
        else:
            acceleration = -base_drag - 7.0 * lever + slope_accel
        self.speed = max(5.0, self.speed + acceleration * dt)

        # Unter 65 km/h: Stroemungsabriss, starkes Sinken und Nase nach unten.
        stall_ratio = clamp(self.speed / STALL_SPEED, 0.0, 1.0)
        stall_sink = (1.0 - stall_ratio) ** 2 * 14.0
        if self.speed < STALL_SPEED:
            self.pitch -= math.radians(20.0) * (1.0 - stall_ratio) * dt

        self.vertical_speed = self.speed * math.sin(self.pitch) - stall_sink
        horizontal_speed = self.speed * math.cos(self.pitch)
        self.x += math.sin(self.heading) * horizontal_speed * dt
        self.z += math.cos(self.heading) * horizontal_speed * dt
        self.y += self.vertical_speed * dt

        # Bodenberuehrung. Die Landebahn liegt von z=0 bis z=800.
        if self.y <= 1.6:
            on_runway = abs(self.x) <= 10.0 and 0.0 <= self.z <= 800.0
            hard = self.vertical_speed < -3.5 or abs(self.roll) > math.radians(12)
            if hard or not on_runway or self.speed > 45.0:
                self.crashed = True
                self.y = 1.6
            else:
                self.y = 1.6
                self.pitch = 0.0
                self.roll = 0.0
                self.landed = True
                self.speed = max(0.0, self.speed - (2.0 + 7.0 * lever) * dt)
                self.vertical_speed = 0.0


@dataclass
class Bullet:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    life: float = 3.2

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.z += self.vz * dt
        self.life -= dt


@dataclass
class Drone:
    center_x: float
    center_y: float
    center_z: float
    radius: float
    phase: float
    angular_speed: float
    alive: bool = True

    def update(self, dt):
        if self.alive:
            self.phase = angle_wrap(self.phase + self.angular_speed * dt)

    def position(self):
        return (
            self.center_x + math.cos(self.phase) * self.radius,
            self.center_y + math.sin(self.phase * 1.7) * 22.0,
            self.center_z + math.sin(self.phase) * self.radius,
        )


def make_drones():
    return [
        Drone(-180, 130, 900, 150, 0.2, 0.22),
        Drone(220, 190, 1250, 210, 1.8, -0.18),
        Drone(-320, 260, 1650, 260, 3.4, 0.15),
        Drone(350, 115, 1900, 180, 4.6, -0.25),
        Drone(0, 330, 2250, 310, 5.5, 0.12),
    ]


def fire_cannon(aircraft):
    cp = math.cos(aircraft.pitch)
    forward_x = math.sin(aircraft.heading) * cp
    forward_y = math.sin(aircraft.pitch)
    forward_z = math.cos(aircraft.heading) * cp
    muzzle = 5.0
    projectile_speed = CANNON_SPEED + aircraft.speed
    return Bullet(
        aircraft.x + forward_x * muzzle,
        aircraft.y + forward_y * muzzle,
        aircraft.z + forward_z * muzzle,
        forward_x * projectile_speed,
        forward_y * projectile_speed,
        forward_z * projectile_speed,
    )


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

    # Landebahn: 800 x 20 m, Umriss und Mittellinie.
    runway = [(-10, 0.08, 0), (10, 0.08, 0), (10, 0.08, 800), (-10, 0.08, 800)]
    for i in range(4):
        clipped_line(surface, camera, runway[i], runway[(i + 1) % 4], WHITE, 3)
    for z in range(20, 790, 40):
        clipped_line(surface, camera, (0, 0.1, z), (0, 0.1, z + 20), WHITE, 2)
    for x in (-7, -4, 4, 7):
        clipped_line(surface, camera, (x, 0.1, 12), (x, 0.1, 35), WHITE, 2)

    # Einige einfache Orientierungspunkte aus Drahtgitterlinien.
    draw_box(surface, camera, (-170, 0, 260), (35, 28, 35), CYAN)
    draw_box(surface, camera, (145, 0, 520), (55, 18, 40), YELLOW)
    draw_pylon(surface, camera, (260, 0, 160), 65, RED)

    # Beschriftung der Bahn, sofern sie im Blickfeld liegt.
    label = camera.project((0, 0.2, 15))
    if label and -200 < label[0] < WIDTH + 200 and -100 < label[1] < HEIGHT:
        text = font_small.render("PISTE 36", True, WHITE)
        surface.blit(text, (label[0] - text.get_width() // 2, label[1]))


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


def draw_drone(surface, camera, drone):
    """Einfache Zieldrohne als leuchtendes Drahtgittermodell."""
    if not drone.alive:
        return
    x, y, z = drone.position()
    points = [
        (x, y, z + 10),       # Nase
        (x, y, z - 8),        # Heck
        (x - 13, y, z - 2),   # linker Fluegel
        (x + 13, y, z - 2),   # rechter Fluegel
        (x, y + 4, z - 3),
        (x, y - 4, z - 3),
    ]
    for a, b in ((0, 2), (0, 3), (2, 1), (3, 1), (2, 3), (4, 1), (5, 1)):
        clipped_line(surface, camera, points[a], points[b], RED, 2)


def draw_bullet(surface, camera, bullet):
    speed = math.sqrt(bullet.vx ** 2 + bullet.vy ** 2 + bullet.vz ** 2)
    if speed <= 0.0:
        return
    trail = 7.0 / speed
    tail = (bullet.x - bullet.vx * trail,
            bullet.y - bullet.vy * trail,
            bullet.z - bullet.vz * trail)
    clipped_line(surface, camera, tail, (bullet.x, bullet.y, bullet.z), YELLOW, 2)


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
    pygame.draw.rect(surface, YELLOW if aircraft.mode == "JET" else RED,
                     (bar_x + 3, bar_y + bar_h - 3 - fill, bar_w - 6, fill))
    lever_name = "SCHUB" if aircraft.mode == "JET" else "STOER"
    surface.blit(font_small.render(lever_name, True, WHITE), (710, bar_y + 5))
    surface.blit(font.render(aircraft.mode, True, YELLOW), (710, bar_y + 32))
    surface.blit(font_small.render(f"X {aircraft.x:+7.0f} m   Z {aircraft.z:+7.0f} m", True, GREEN),
                 (710, bar_y + 64))
    surface.blit(font_small.render("Gitter: 10 m   Piste: X=0, Z=0..800", True, GREEN),
                 (710, bar_y + 84))


def axis_value(joystick, index, invert=False):
    if joystick is None or not 0 <= index < joystick.get_numaxes():
        return 0.0
    value = joystick.get_axis(index)
    return deadzone(-value if invert else value)


def read_controls(joystick, keys):
    # Joystick hat Vorrang; Tastaturwerte werden addiert und begrenzt.
    aileron = axis_value(joystick, JOY_AXIS_QUERRUDER, JOY_INVERT_QUER)
    elevator = axis_value(joystick, JOY_AXIS_HOEHENRUDER, JOY_INVERT_HOEHE)
    rudder = axis_value(joystick, JOY_AXIS_SEITENRUDER)

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
    global WIDTH, HEIGHT
    pygame.init()
    pygame.joystick.init()
    desktop = pygame.display.Info()
    WIDTH = min(WIDTH, max(960, desktop.current_w - 80))
    HEIGHT = min(HEIGHT, max(560, desktop.current_h - 100))
    windowed_size = (WIDTH, HEIGHT)
    screen = pygame.display.set_mode(windowed_size, pygame.RESIZABLE)
    pygame.display.set_caption("Linien-Flugsimulator")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 28)
    font_small = pygame.font.Font(None, 21)

    joystick = None
    if pygame.joystick.get_count():
        joystick = pygame.joystick.Joystick(0)
        joystick.init()

    aircraft = Aircraft()
    drones = make_drones()
    bullets = []
    hits = 0
    cannon_cooldown = 0.0
    lever = 0.0
    view_yaw = 0.0
    view_pitch = 0.0
    # Beim Start nur einen kleinen Hinweis zeigen; H/F1 klappt die Anleitung auf.
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
                elif event.key == pygame.K_F3:
                    aircraft.jet_start()
                    lever = 0.0
                    drones = make_drones()
                    bullets.clear()
                    hits = 0
                elif event.key == pygame.K_m:
                    aircraft.mode = "JET" if aircraft.mode == "SEGLER" else "SEGLER"
                elif event.key == pygame.K_r:
                    aircraft.reset()
                    drones = make_drones()
                    bullets.clear()
                    hits = 0
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
            aircraft.update(dt, aileron, elevator, rudder, lever)
            for drone in drones:
                drone.update(dt)

            cannon_cooldown = max(0.0, cannon_cooldown - dt)
            joystick_trigger = (joystick is not None and joystick.get_numbuttons() > 0
                                and joystick.get_button(0))
            if ((keys[pygame.K_SPACE] or joystick_trigger)
                    and cannon_cooldown <= 0.0 and not aircraft.crashed):
                bullets.append(fire_cannon(aircraft))
                cannon_cooldown = CANNON_INTERVAL

            for bullet in bullets:
                bullet.update(dt)
                for drone in drones:
                    if not drone.alive:
                        continue
                    dx = bullet.x - drone.position()[0]
                    dy = bullet.y - drone.position()[1]
                    dz = bullet.z - drone.position()[2]
                    if dx * dx + dy * dy + dz * dz <= 16.0 ** 2:
                        drone.alive = False
                        bullet.life = 0.0
                        hits += 1
                        break
            bullets = [bullet for bullet in bullets if bullet.life > 0.0 and bullet.y > 0.0]

        camera = Camera(aircraft, view_yaw, view_pitch)
        draw_sky_and_horizon(screen, camera, font_small)
        draw_world(screen, camera, aircraft, font_small)
        for drone in drones:
            draw_drone(screen, camera, drone)
        for bullet in bullets:
            draw_bullet(screen, camera, bullet)

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
        targets_left = sum(1 for drone in drones if drone.alive)
        combat_status = f"KANONE: LEERTASTE/KNOPF 0   TREFFER {hits}   ZIELE {targets_left}"
        screen.blit(font_small.render(combat_status, True, YELLOW), (12, 58))
        draw_instruments(screen, aircraft, lever, font, font_small)

        if aircraft.speed < STALL_SPEED and not aircraft.crashed and not aircraft.landed:
            warning = font.render("UEBERZIEHEN - FAHRT UNTER 65 km/h", True, RED)
            screen.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 55))
        if aircraft.landed:
            ground_text = "STARTLAUF - BEI 80 km/h ZIEHEN" if aircraft.mode == "JET" else "GELANDET - AUSROLLEN"
            message = font.render(ground_text, True, GREEN)
            screen.blit(message, (WIDTH // 2 - message.get_width() // 2, 55))
        if aircraft.crashed:
            warning = pygame.font.Font(None, 54).render("BRUCHLANDUNG - R ZUM NEUSTART", True, RED)
            screen.blit(warning, (WIDTH // 2 - warning.get_width() // 2, 105))
        if paused:
            pause_text = font.render("PAUSE", True, YELLOW)
            screen.blit(pause_text, (WIDTH // 2 - pause_text.get_width() // 2, 90))

        if show_axes and joystick:
            values = "  ".join(f"A{i}:{joystick.get_axis(i):+.2f}"
                               for i in range(joystick.get_numaxes()))
            screen.blit(font_small.render(values, True, YELLOW), (12, 80))

        if show_help:
            box = pygame.Surface((510, 300), pygame.SRCALPHA)
            box.fill((0, 0, 0, 215))
            pygame.draw.rect(box, GREEN, box.get_rect(), 1)
            lines = [
                "STEUERUNG",
                "Joystick: Knueppel / Seitenruder / Hebel",
                "Coolie-Hat: Blick links, rechts, oben, unten",
                "Tastatur: W/S Hoehe, A/D Quer, Q/E Seite",
                "+/- Hebel    J/L Seitenblick",
                "LEERTASTE / Joystick-Knopf 0: Kanone",
                "F3 Jet auf Startbahn (Schub, ab 80 km/h ziehen)",
                "M Segler/Jet    R Neustart    P Pause",
                "F11/Alt+Enter Vollbild    Esc zurueck",
                "F2 rohe Achsenwerte    F1/H Hilfe",
                "Ziel: auf Piste X=0, Z=0..800 landen",
                "Landung: <162 km/h, Sinken <3.5 m/s, Fluegel eben",
            ]
            for i, line in enumerate(lines):
                color = YELLOW if i == 0 else WHITE
                box.blit(font_small.render(line, True, color), (14, 12 + i * 23))
            screen.blit(box, (WIDTH - 528, 18))
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
