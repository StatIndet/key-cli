from __future__ import annotations

import base64

from key_cli.clipboard.backend import image_info, inspect_payload, lightweight


def test_png_metadata_and_safe_preview_shape() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (16).to_bytes(4, "big") + (8).to_bytes(4, "big")
    info = image_info(data)
    assert info == ("image/png", 16, 8)
    payload, error = inspect_payload("1", data, False)
    assert error is None
    assert payload["payloadKind"] == "image"
    assert payload["width"] == 16


def test_cliphist_binary_marker_is_image() -> None:
    entry = lightweight("7", "[[ binary data 12 KB png 32x20 ]]")
    assert entry["id"] == "7"
    assert entry["payloadKind"] == "image"


def test_html_embedded_image_is_inspectable() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (16).to_bytes(4, "big") + (8).to_bytes(4, "big")
    html = (
        "<html><body><img src=\"data:image/png;base64,"
        + base64.b64encode(data).decode()
        + "\"></body></html>"
    ).encode()
    payload, error = inspect_payload("8", html, False)
    assert error is None
    assert payload["payloadKind"] == "image"
    assert payload["htmlImageFallback"] is True
