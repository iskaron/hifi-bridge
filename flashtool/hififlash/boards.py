"""Supported boards (DECISIONS #51): the ``compatible`` string is what the Buildroot defconfig writes into the image manifest
(``BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE``), so an image for one board is never written for another by mistake."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    compatible: str


BOARDS = (
    Board("rpizero2w", "Raspberry Pi Zero 2 W", "hifi-bridge-rpizero2w"),
    Board("rpi3", "Raspberry Pi 3 Model B+", "hifi-bridge-rpi3"),
    Board("rpi4", "Raspberry Pi 4 Model B", "hifi-bridge-rpi4"),
)


def by_id(board_id: str) -> Board | None:
    return next((b for b in BOARDS if b.id == board_id), None)


def by_compatible(compatible: str) -> Board | None:
    return next((b for b in BOARDS if b.compatible == compatible), None)
