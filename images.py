"""Make an uploaded photo safe to store: no hidden location data, a sensible size, and always a plain JPEG.

A phone photo carries hidden metadata (EXIF), including GPS coordinates that reveal where the photo was taken,
often a person's home. We re-draw the picture from its pixels only, so none of that metadata survives.
"""
import io

from PIL import Image, ImageOps, UnidentifiedImageError

try:                                   # iPhone photos (HEIC)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:                    # still works for JPEG/PNG/WebP
    pass

MAX_BYTES = 20 * 1024 * 1024           # largest upload we accept
MAX_SIDE = 1400                        # longest edge after resizing
MAX_PIXELS = 60_000_000                # refuse "decompression bombs"
ALLOWED = {"JPEG", "MPO", "PNG", "WEBP", "HEIF", "HEIC"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class BadImage(ValueError):
    """The upload is not a usable photo. The message is safe to show to the user."""


def clean_photo(data):
    """bytes in -> bytes out (a clean JPEG). Raises BadImage with a friendly message."""
    if not data:
        raise BadImage("No photo was received.")
    if len(data) > MAX_BYTES:
        raise BadImage("That photo is too large (20 MB at most).")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED:
            raise BadImage("Please upload a JPG, PNG, WebP or iPhone (HEIC) photo.")
        img.load()                                      # fully decode now, so broken files fail here
    except BadImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise BadImage("That file couldn't be read as a photo.") from None
    img = ImageOps.exif_transpose(img)                  # turn it upright BEFORE the orientation tag is thrown away
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    clean = Image.new("RGB", img.size)                  # a brand-new image: pixels only, no metadata can ride along
    clean.paste(img)
    out = io.BytesIO()
    clean.save(out, "JPEG", quality=82, optimize=True)  # no exif=, no icc_profile=: nothing is written
    return out.getvalue()
