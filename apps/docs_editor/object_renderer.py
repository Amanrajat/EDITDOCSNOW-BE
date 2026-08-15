"""
Renders editor-added DocumentObjects (text/image/shapes/freehand strokes)
into an already-open PyMuPDF document. Deliberately separate from
pdf_regenerator.py's redact-and-reinsert text-block editing path - these
objects are purely additive (nothing to diff, they either exist or they
don't), so they're inserted directly rather than through any redaction
step. See apps/docs_editor/models.py's DocumentObject docstring for why
this is one polymorphic model/renderer instead of one per object type.

Two real PyMuPDF rotation/opacity mechanisms are used, and they are NOT
interchangeable:
  - Shape.insert_text/insert_textbox (TEXT objects) take `morph` and
    `fill_opacity`/`stroke_opacity` directly as call arguments - passing
    them to a later Shape.finish() call instead has NO effect on text
    already inserted (verified empirically; this is not documented
    clearly enough to assume).
  - Shape.draw_rect/draw_oval/draw_line/draw_polyline (shapes, freehand
    paths) take no style arguments at all - morph/opacity/color are set
    once via Shape.finish() and apply to everything drawn since shape
    creation.
Images have no arbitrary-rotation support in PyMuPDF's insert_image
(only 0/90/180/270) - rotation is instead baked into the pixels
themselves via Pillow (rotate with expand=True) before embedding, and
opacity is baked into an alpha channel the same way, since insert_image
has no opacity parameter either.
"""

import io
import math

import fitz
from PIL import Image

from .pdf_regenerator import get_font_spec, hex_to_rgb

_ALIGN_MAP = {"left": 0, "center": 1, "right": 2}
_MIN_ARROWHEAD_LENGTH = 8.0
_ARROWHEAD_ANGLE_DEGREES = 25.0


def _bbox_rect(obj):
    x0, y0, x1, y1 = obj["bbox"]
    return fitz.Rect(x0, y0, x1, y1)


def _rotation_morph(center, rotation_degrees):
    if not rotation_degrees:
        return None
    matrix = fitz.Matrix(1, 1).prerotate(rotation_degrees)
    return (center, matrix)


_MIN_TEXT_FONT_SIZE = 4.0


def _draw_text(page, obj):
    text = obj.get("text_content", "")
    if not text:
        return

    rect = _bbox_rect(obj)
    font_size = float(obj.get("font_size", 14))
    color = hex_to_rgb(obj.get("stroke_color") or "#000000")
    align = _ALIGN_MAP.get(obj.get("text_align", "left"), 0)
    opacity = float(obj.get("opacity", 1.0))
    rotation = float(obj.get("rotation", 0) or 0)
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)

    font_name, font_file = get_font_spec({
        "font_name": obj.get("font_family", "sans"),
        "is_bold": obj.get("is_bold", False),
        "is_italic": obj.get("is_italic", False),
    })

    # insert_textbox() returns a negative number - and inserts NOTHING - if
    # the text doesn't fit the box at the given font size, e.g. a user
    # dragging a small text box then typing more than it can hold at its
    # default size. Shrink in small steps until it fits (same recovery
    # pdf_regenerator._insert_text_safely uses for edited blocks), rather
    # than silently dropping the object's text from the output PDF.
    current_size = font_size
    while current_size >= _MIN_TEXT_FONT_SIZE:
        shape = page.new_shape()
        result = shape.insert_textbox(
            rect, text,
            fontsize=current_size, fontname=font_name, fontfile=font_file,
            color=color, align=align,
            fill_opacity=opacity, stroke_opacity=opacity,
            morph=_rotation_morph(center, rotation),
        )
        if result >= 0:
            shape.commit()
            return
        current_size -= 0.5

    # Even the minimum size didn't fit (e.g. an extremely long string in a
    # tiny box) - insert at the floor size anyway so at least a truncated
    # excerpt is visible rather than nothing at all.
    shape = page.new_shape()
    shape.insert_textbox(
        rect, text,
        fontsize=_MIN_TEXT_FONT_SIZE, fontname=font_name, fontfile=font_file,
        color=color, align=align,
        fill_opacity=opacity, stroke_opacity=opacity,
        morph=_rotation_morph(center, rotation),
    )
    shape.commit()


def _arrowhead_points(start, end, stroke_width):
    angle = math.atan2(end.y - start.y, end.x - start.x)
    arrow_len = max(_MIN_ARROWHEAD_LENGTH, stroke_width * 4)
    spread = math.radians(_ARROWHEAD_ANGLE_DEGREES)
    p1 = fitz.Point(
        end.x - arrow_len * math.cos(angle - spread),
        end.y - arrow_len * math.sin(angle - spread),
    )
    p2 = fitz.Point(
        end.x - arrow_len * math.cos(angle + spread),
        end.y - arrow_len * math.sin(angle + spread),
    )
    return p1, p2


def _draw_shape(page, obj):
    rect = _bbox_rect(obj)
    object_type = obj["object_type"]
    stroke_width = float(obj.get("stroke_width", 1.0))
    stroke_color = hex_to_rgb(obj.get("stroke_color") or "#000000")
    fill_hex = obj.get("fill_color") or ""
    fill_color = hex_to_rgb(fill_hex) if fill_hex else None
    opacity = float(obj.get("opacity", 1.0))
    rotation = float(obj.get("rotation", 0) or 0)
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)

    shape = page.new_shape()

    if object_type == "rectangle":
        shape.draw_rect(rect)
    elif object_type == "ellipse":
        shape.draw_oval(rect)
    elif object_type == "line":
        shape.draw_line(fitz.Point(rect.x0, rect.y0), fitz.Point(rect.x1, rect.y1))
    elif object_type == "arrow":
        start, end = fitz.Point(rect.x0, rect.y0), fitz.Point(rect.x1, rect.y1)
        shape.draw_line(start, end)
        p1, p2 = _arrowhead_points(start, end, stroke_width)
        shape.draw_line(p1, end)
        shape.draw_line(p2, end)
    else:
        return

    finish_kwargs = dict(
        color=stroke_color,
        width=stroke_width,
        fill_opacity=opacity,
        stroke_opacity=opacity,
    )
    if fill_color is not None and object_type in ("rectangle", "ellipse"):
        finish_kwargs["fill"] = fill_color
    morph = _rotation_morph(center, rotation)
    if morph:
        finish_kwargs["morph"] = morph

    shape.finish(**finish_kwargs)
    shape.commit()


def _draw_path(page, obj):
    points = obj.get("points") or []
    if len(points) < 2:
        return

    pts = [fitz.Point(x, y) for x, y in points]
    stroke_width = float(obj.get("stroke_width", 2.0))
    stroke_color = hex_to_rgb(obj.get("stroke_color") or "#000000")
    opacity = float(obj.get("opacity", 1.0))
    rotation = float(obj.get("rotation", 0) or 0)

    shape = page.new_shape()
    shape.draw_polyline(pts)

    finish_kwargs = dict(
        color=stroke_color,
        width=stroke_width,
        stroke_opacity=opacity,
        lineCap=1,
        lineJoin=1,
    )
    if rotation:
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        center = fitz.Point((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        finish_kwargs["morph"] = (center, fitz.Matrix(1, 1).prerotate(rotation))

    shape.finish(**finish_kwargs)
    shape.commit()


def _prepare_image_bytes(image_bytes, rotation, opacity):
    """Bakes rotation and opacity into the raster data itself (PyMuPDF's
    insert_image has no arbitrary-rotation or opacity support), returning
    (processed_png_bytes, pixel_width, pixel_height)."""
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("RGBA")

    if rotation:
        image = image.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    if opacity < 1.0:
        alpha = image.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, opacity))))
        image.putalpha(alpha)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), image.width, image.height


def _draw_image(page, obj, get_image_bytes):
    image_bytes = get_image_bytes(obj)
    if not image_bytes:
        return

    rect = _bbox_rect(obj)
    rotation = float(obj.get("rotation", 0) or 0)
    opacity = float(obj.get("opacity", 1.0))

    original = Image.open(io.BytesIO(image_bytes))
    original_w, original_h = original.size
    if original_w == 0 or original_h == 0:
        return

    scale_x = rect.width / original_w
    scale_y = rect.height / original_h

    processed_bytes, new_w, new_h = _prepare_image_bytes(image_bytes, rotation, opacity)

    display_w = new_w * scale_x
    display_h = new_h * scale_y
    center_x = (rect.x0 + rect.x1) / 2
    center_y = (rect.y0 + rect.y1) / 2
    placed_rect = fitz.Rect(
        center_x - display_w / 2, center_y - display_h / 2,
        center_x + display_w / 2, center_y + display_h / 2,
    )

    page.insert_image(placed_rect, stream=processed_bytes)


def render_objects(document, objects, get_image_bytes=None):
    """
    `objects`: iterable of plain dicts (not model instances - keeps this
    module decoupled from Django/the ORM, same convention as
    pdf_regenerator.regenerate_pdf's `blocks` parameter), each with at
    least `page_number`, `object_type`, and the fields relevant to that
    type (see DocumentObject's docstring).
    `get_image_bytes(obj) -> bytes | None`: required if any object has
    object_type "image" - resolves that object's stored image file to
    raw bytes (kept as an injected callable so this module never touches
    Django storage directly).
    """
    for obj in objects:
        page_number = obj.get("page_number", 0)
        if page_number < 0 or page_number >= len(document):
            continue
        page = document[page_number]
        object_type = obj.get("object_type")

        if object_type == "text":
            _draw_text(page, obj)
        elif object_type == "image":
            if get_image_bytes is not None:
                _draw_image(page, obj, get_image_bytes)
        elif object_type == "path":
            _draw_path(page, obj)
        elif object_type in ("rectangle", "ellipse", "line", "arrow"):
            _draw_shape(page, obj)
