"""Small cached logo artwork for the warning, never a desktop screenshot."""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


LOGO_PATH = Path(__file__).resolve().parent / "assets/icons/beardguard.png"
FRAME_FILL = "#25282e"
FRAME_EDGE = "#686e77"


def render_logo_badge(size: int) -> Image.Image:
    """Render the existing logo inside an antialiased, rounded graphite frame."""
    scale = 3
    extent = size * scale
    badge = Image.new("RGBA", (extent, extent))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle(
        (scale, scale, extent - scale - 1, extent - scale - 1),
        radius=extent * 0.11,
        fill=FRAME_FILL,
        outline=FRAME_EDGE,
        width=2 * scale,
    )
    padding = round(extent * 0.105)
    logo_size = extent - 2 * padding
    with Image.open(LOGO_PATH) as source:
        logo = source.convert("RGBA")
    logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
    # The supplied icon has opaque black outside its rounded silhouette.
    # Clip those corners at display time; leave the source asset untouched.
    mask = Image.new("L", logo.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, logo.width - 1, logo.height - 1), radius=min(logo.size) * 0.27, fill=255
    )
    logo.putalpha(ImageChops.multiply(logo.getchannel("A"), mask))
    badge.alpha_composite(logo, ((extent - logo.width) // 2, (extent - logo.height) // 2))
    return badge.resize((size, size), Image.Resampling.LANCZOS)
