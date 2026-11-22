"""Item-local raster preparation; physical dimensions remain existing card metadata."""
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPixmap

from mtg_proxy_printer.model.card import AnyCardType, CardCorner


@dataclass(frozen=True)
class PreparedCardRaster:
    """Read-only field bindings; contained Qt values/dictionary are not deeply immutable."""

    width: int
    height: int
    artwork: QPixmap
    style: QPixmap
    corner_colors: dict[CardCorner, QColor]

    @classmethod
    def from_card(cls, card: AnyCardType, raster: QPixmap | None = None) -> "PreparedCardRaster":
        explicit = raster is not None
        selected = raster if explicit else card.image_file
        if selected is None or selected.isNull():
            if explicit:
                raise ValueError("An explicitly supplied card raster must not be null")
            raise RuntimeError("Cannot prepare a card without an available image")
        canonical = card.size.as_qsize_px()
        # QPixmap value copy: detach DPR metadata locally, never alter the source value.
        artwork = QPixmap(selected)
        artwork.setDevicePixelRatio(1.0)
        style = artwork if artwork.size() == canonical else artwork.scaled(
            canonical, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if explicit:
            colors = {}
            for corner in CardCorner:
                sample = style.copy(QRect(
                    QPoint(round(style.width()*corner.value[0]), round(style.height()*corner.value[1])),
                    QSize(10, 10)))
                colors[corner] = sample.scaledToWidth(
                    1, Qt.TransformationMode.SmoothTransformation).toImage().pixelColor(0, 0)
        else:
            # CheckCard deliberately maps component-face corners, not its combined pixels.
            colors = {corner: card.corner_color(corner) for corner in CardCorner}
        return cls(canonical.width(), canonical.height(), artwork, style, colors)
