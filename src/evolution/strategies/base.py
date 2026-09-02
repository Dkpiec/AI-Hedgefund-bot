"""
Strategy Base Class with Mandatory Inside-Bar Gate
===================================================
All strategy candidates MUST subclass this and pass the inside-bar gate
before any signal can be generated.
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any


class Strategy(ABC):
    """
    Base class for all trading strategies.
    The inside-bar condition is enforced in evaluate() — subclasses cannot disable it.
    """

    # Subclass must set these
    name: str = "UnnamedStrategy"
    version_id: str = "v0"
    description: str = ""

    def __init__(self):
        if not hasattr(self, 'name') or self.name == "UnnamedStrategy":
            raise ValueError("Strategy must set class attribute 'name'")
        if not hasattr(self, 'version_id') or self.version_id == "v0":
            raise ValueError("Strategy must set class attribute 'version_id'")

    def is_inside_bar(self, highs: List[float], lows: List[float], i: int = -2) -> bool:
        """
        Check if bar at index i is an inside bar.
        Inside bar: high[i] < high[i-1] AND low[i] > low[i-1]
        Uses COMPLETED bar (index -2), never a forming bar.
        """
        if len(highs) < abs(i) + 1 or len(lows) < abs(i) + 1:
            return False
        prev_i = i - 1
        return highs[i] < highs[prev_i] and lows[i] > lows[prev_i]

    def evaluate(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Gate + generate. Subclasses override generate(), not this.
        Returns None if inside-bar gate fails.
        """
        highs = data.get("highs", [])
        lows = data.get("lows", [])
        if not self.is_inside_bar(highs, lows, i=-2):
            return None  # Inside-bar gate rejected
        return self.generate(data)

    @abstractmethod
    def generate(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Strategy-specific signal logic.
        Called only AFTER inside-bar gate passes.
        Returns {"signal": "BUY"|"SELL"|"HOLD", "confidence": int, "logic": str} or None.
        """
        raise NotImplementedError
