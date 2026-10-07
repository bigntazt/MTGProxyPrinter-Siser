"""Stateless registration rendering profiles over ready shared page geometry."""

from collections.abc import Sequence
from typing import Protocol

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem

from mtg_proxy_printer.model.page_geometry import PageGeometry
from mtg_proxy_printer.registration_profile_ids import RegistrationProfileId
from mtg_proxy_printer.page_scene.items import BullseyeMarkItem, CutMarkSquareItem, CutMarkAngleItem


class RegistrationProfile(Protocol):
    profile_id: RegistrationProfileId

    def create_items(self) -> list[QGraphicsItem]: ...

    def place_items(self, items: Sequence[QGraphicsItem], geometry: PageGeometry,
                    *, legacy_x_offset_px: int = 0) -> None: ...


def _place_three_corners(items: Sequence[QGraphicsItem], geometry: PageGeometry,
                         legacy_x_offset_px: int) -> None:
    frame = geometry.margin_frame_px
    positions = (
        QPointF(frame.x + legacy_x_offset_px, frame.y),
        QPointF(frame.right + legacy_x_offset_px, frame.y),
        QPointF(frame.x + legacy_x_offset_px, frame.bottom),
    )
    for item, position in zip(items, positions):
        item.setPos(position)


class _DisabledProfile:
    profile_id = RegistrationProfileId.NONE

    def create_items(self) -> list[QGraphicsItem]:
        return []

    def place_items(self, items: Sequence[QGraphicsItem], geometry: PageGeometry,
                    *, legacy_x_offset_px: int = 0) -> None:
        pass


class _BullseyeProfile:
    profile_id = RegistrationProfileId.BULLSEYE

    def create_items(self) -> list[QGraphicsItem]:
        return [BullseyeMarkItem(False, False), BullseyeMarkItem(True, False), BullseyeMarkItem(False, True)]

    def place_items(self, items: Sequence[QGraphicsItem], geometry: PageGeometry,
                    *, legacy_x_offset_px: int = 0) -> None:
        _place_three_corners(items, geometry, legacy_x_offset_px)


class _SilhouetteProfile:
    profile_id = RegistrationProfileId.SILHOUETTE

    def create_items(self) -> list[QGraphicsItem]:
        return [CutMarkSquareItem(), CutMarkAngleItem(False), CutMarkAngleItem(True)]

    def place_items(self, items: Sequence[QGraphicsItem], geometry: PageGeometry,
                    *, legacy_x_offset_px: int = 0) -> None:
        _place_three_corners(items, geometry, legacy_x_offset_px)


_PROFILES: dict[str, RegistrationProfile] = {
    profile.profile_id.value: profile
    for profile in (_DisabledProfile(), _BullseyeProfile(), _SilhouetteProfile())
}


def get_registration_profile(style: str) -> RegistrationProfile:
    return _PROFILES.get(style, _PROFILES[RegistrationProfileId.NONE.value])
