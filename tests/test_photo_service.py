from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from src.services.photo_service import MAX_PHOTO_BYTES, accepted_photo, sanitize_photo


def test_attachment_byte_boundary_is_inclusive():
    assert accepted_photo(SimpleNamespace(content_type="image/png", size=MAX_PHOTO_BYTES))
    assert not accepted_photo(SimpleNamespace(content_type="image/png", size=MAX_PHOTO_BYTES + 1))


@pytest.mark.parametrize("width,accepted", [(4000, True), (4001, False)])
def test_real_decoded_pixel_boundary_is_16_million(width, accepted):
    image = BytesIO()
    Image.new("RGB", (width, 4000), "white").save(image, format="PNG")
    if accepted:
        normalized = sanitize_photo(image.getvalue())
        with Image.open(BytesIO(normalized)) as output:
            assert output.size == (4000, 4000) and output.format == "PNG"
    else:
        with pytest.raises(ValueError, match="16 megapixels"):
            sanitize_photo(image.getvalue())
