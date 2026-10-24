import io

import pytest
from PIL import Image

from images import BadImage, MAX_SIDE, clean_photo


def photo_with_gps(size=(3000, 2000), orientation=6):
    """A JPEG with GPS coordinates and a rotation tag, the way a phone writes it."""
    img = Image.new("RGB", size, (200, 120, 40))
    exif = Image.Exif()
    exif[0x0112] = orientation                                  # Orientation: needs rotating
    exif[0x010F] = "SecretPhoneMaker"                           # camera make
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (12.0, 58.0, 30.0)                    # latitude
    gps[3], gps[4] = "E", (77.0, 35.0, 12.0)                    # longitude
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_the_test_photo_really_has_gps():
    raw = Image.open(io.BytesIO(photo_with_gps()))
    assert raw.getexif().get_ifd(0x8825)                        # sanity check: GPS is present before cleaning


def test_gps_and_all_metadata_are_removed():
    cleaned = clean_photo(photo_with_gps())
    img = Image.open(io.BytesIO(cleaned))
    assert dict(img.getexif()) == {}
    assert not img.getexif().get_ifd(0x8825)
    assert b"Exif" not in cleaned and b"SecretPhoneMaker" not in cleaned
    assert "icc_profile" not in img.info and "exif" not in img.info


def test_photo_is_turned_upright_and_resized():
    cleaned = clean_photo(photo_with_gps(size=(3000, 2000), orientation=6))   # tag 6 = rotate 90 degrees
    img = Image.open(io.BytesIO(cleaned))
    assert max(img.size) <= MAX_SIDE
    assert img.size[1] > img.size[0]                                          # became portrait, as the tag said


def test_output_is_always_jpeg_even_for_png():
    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (1, 2, 3, 128)).save(buf, "PNG")
    out = Image.open(io.BytesIO(clean_photo(buf.getvalue())))
    assert out.format == "JPEG" and out.mode == "RGB"


@pytest.mark.parametrize("data", [b"", b"not an image at all", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a" + b"0" * 20])
def test_non_photos_are_refused(data):
    with pytest.raises(BadImage):
        clean_photo(data)


def test_truncated_photo_is_refused():
    good = photo_with_gps(size=(300, 200))
    with pytest.raises(BadImage):
        clean_photo(good[: len(good) // 2])


def test_huge_upload_is_refused():
    with pytest.raises(BadImage):
        clean_photo(b"\xff\xd8" + b"0" * (21 * 1024 * 1024))


def test_gif_is_not_allowed():
    buf = io.BytesIO()
    Image.new("P", (10, 10)).save(buf, "GIF")
    with pytest.raises(BadImage):
        clean_photo(buf.getvalue())
