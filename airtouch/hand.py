"""Estrazione delle feature geometriche dai 21 landmark di MediaPipe Hands.

Indici dei landmark:
    0  polso
    4  punta pollice        3  IP pollice
    8  punta indice         5  MCP indice     6  PIP indice
    12 punta medio          9  MCP medio     10  PIP medio
    16 punta anulare       13  MCP anulare   14  PIP anulare
    20 punta mignolo       17  MCP mignolo   18  PIP mignolo
"""

from dataclasses import dataclass
from math import hypot

WRIST = 0
THUMB_IP, THUMB_TIP = 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

# Dita che possono fare pinch con il pollice, nell'ordine in cui compaiono
# nell'anteprima.
PINCH_FINGERS = {"index": INDEX_TIP, "middle": MIDDLE_TIP, "ring": RING_TIP}

Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class HandFeatures:
    """Descrizione della mano indipendente da distanza e rotazione."""

    points: list[Point]
    scale: float              # dimensione della mano (polso -> MCP medio)
    index_extended: bool
    middle_extended: bool
    ring_extended: bool
    pinky_extended: bool
    thumb_extended: bool
    pinches: dict[str, float]  # "index"/"middle"/"ring" -> distanza dal pollice
    index_point: Point        # punta dell'indice: preciso ma ballerino nei pinch
    palm_point: Point         # centro del palmo (usato per swipe e assi)
    palm_outer: Point         # bordo esterno del palmo, lato mignolo

    def anchor(self, name: str) -> Point:
        """Punto di riferimento alternativo all'indice, scelto da configurazione."""
        pts = self.points
        if name == "palm_outer":
            return self.palm_outer
        if name == "palm_center":
            return self.palm_point
        if name == "pinky_mcp":
            return pts[PINKY_MCP]
        if name == "index_mcp":
            return pts[INDEX_MCP]
        if name == "wrist":
            return pts[WRIST]
        raise KeyError(name)

    @property
    def extended_count(self) -> int:
        return sum(
            (
                self.index_extended,
                self.middle_extended,
                self.ring_extended,
                self.pinky_extended,
                self.thumb_extended,
            )
        )

    @property
    def is_fist(self) -> bool:
        return not (
            self.index_extended
            or self.middle_extended
            or self.ring_extended
            or self.pinky_extended
        )


def _finger_extended(pts: list[Point], tip: int, pip: int) -> bool:
    """Dito esteso se la punta e' piu' lontana dal polso della falange media.

    Confronto basato sulle distanze (non sulla sola coordinata y): funziona
    anche con la mano ruotata o inclinata.
    """
    wrist = pts[WRIST]
    return _dist(pts[tip], wrist) > _dist(pts[pip], wrist) * 1.06


def extract(landmarks) -> HandFeatures:
    """Converte i landmark normalizzati di MediaPipe in feature utilizzabili."""
    pts: list[Point] = [(lm.x, lm.y) for lm in landmarks]

    # Scala della mano: distanza polso -> base del medio. Non cambia quando le
    # dita si chiudono, quindi e' un buon riferimento per normalizzare.
    scale = _dist(pts[WRIST], pts[MIDDLE_MCP])
    if scale < 1e-6:
        scale = 1e-6

    index_ext = _finger_extended(pts, INDEX_TIP, INDEX_PIP)
    middle_ext = _finger_extended(pts, MIDDLE_TIP, MIDDLE_PIP)
    ring_ext = _finger_extended(pts, RING_TIP, RING_PIP)
    pinky_ext = _finger_extended(pts, PINKY_TIP, PINKY_PIP)

    # Il pollice si apre lateralmente: lo si valuta rispetto alla base del mignolo.
    pinky_mcp = pts[PINKY_MCP]
    thumb_ext = _dist(pts[THUMB_TIP], pinky_mcp) > _dist(pts[THUMB_IP], pinky_mcp) * 1.10

    palm = (
        (pts[WRIST][0] + pts[INDEX_MCP][0] + pts[PINKY_MCP][0]) / 3.0,
        (pts[WRIST][1] + pts[INDEX_MCP][1] + pts[PINKY_MCP][1]) / 3.0,
    )
    # Bordo esterno del palmo (lato mignolo, il "taglio" della mano): polso e
    # base del mignolo non si spostano quando pollice, medio e anulare si
    # chiudono per un pinch, a differenza della punta dell'indice.
    palm_outer = (
        (pts[WRIST][0] + pts[PINKY_MCP][0]) / 2.0,
        (pts[WRIST][1] + pts[PINKY_MCP][1]) / 2.0,
    )

    return HandFeatures(
        points=pts,
        scale=scale,
        index_extended=index_ext,
        middle_extended=middle_ext,
        ring_extended=ring_ext,
        pinky_extended=pinky_ext,
        thumb_extended=thumb_ext,
        pinches={
            name: _dist(pts[THUMB_TIP], pts[tip]) / scale
            for name, tip in PINCH_FINGERS.items()
        },
        index_point=pts[INDEX_TIP],
        palm_point=palm,
        palm_outer=palm_outer,
    )


class Hysteresis:
    """Interruttore con due soglie: evita il tremolio a cavallo della soglia."""

    def __init__(self, on_below: float, off_above: float):
        self.on_below = on_below
        self.off_above = off_above
        self.state = False

    def update(self, value: float) -> bool:
        if self.state:
            if value > self.off_above:
                self.state = False
        else:
            if value < self.on_below:
                self.state = True
        return self.state

    def reset(self) -> None:
        self.state = False
