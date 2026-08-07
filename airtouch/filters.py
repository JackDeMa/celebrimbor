"""Filtro One Euro: smorza il tremolio senza introdurre latenza sui movimenti rapidi.

Riferimento: Casiez, Roussel, Vogel - "1 Euro Filter" (CHI 2012).
"""

import math


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class _LowPass:
    def __init__(self) -> None:
        self.value: float | None = None

    def __call__(self, x: float, alpha: float) -> float:
        self.value = x if self.value is None else alpha * x + (1.0 - alpha) * self.value
        return self.value

    def reset(self) -> None:
        self.value = None


class OneEuroFilter:
    """Filtra un singolo scalare campionato a intervalli irregolari."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = _LowPass()
        self._dx = _LowPass()
        self._prev_x: float | None = None
        self._prev_t: float | None = None

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._prev_x = None
        self._prev_t = None

    def __call__(self, x: float, t: float) -> float:
        if self._prev_t is None:
            dt = 1.0 / 60.0
        else:
            dt = t - self._prev_t
            if dt <= 0.0:
                dt = 1.0 / 60.0

        prev = self._prev_x if self._prev_x is not None else x
        dx = (x - prev) / dt
        edx = self._dx(dx, _alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        y = self._x(x, _alpha(cutoff, dt))

        self._prev_x = x
        self._prev_t = t
        return y


class PointFilter:
    """Comodita': applica One Euro a una coppia (x, y)."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float):
        self._fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self._fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()

    def __call__(self, x: float, y: float, t: float) -> tuple[float, float]:
        return self._fx(x, t), self._fy(y, t)
