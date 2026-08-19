from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from deathtg.panel.clean_actions import MAX_AVATAR_EDGE, _normalize_avatar


class AvatarSecurityTests(unittest.TestCase):
    def test_non_image_payload_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            _normalize_avatar(b"not an image")

    def test_valid_image_is_canonicalized_to_bounded_png(self) -> None:
        source = BytesIO()
        Image.new("RGB", (3000, 1200), "red").save(source, format="JPEG")

        normalized = _normalize_avatar(source.getvalue())

        self.assertTrue(normalized.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(normalized)) as image:
            self.assertLessEqual(max(image.size), MAX_AVATAR_EDGE)


if __name__ == "__main__":
    unittest.main()
