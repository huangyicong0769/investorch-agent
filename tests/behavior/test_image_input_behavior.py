import base64
from dataclasses import FrozenInstanceError

import pytest

from investorch.images import ImageContent, UserInput, normalize_user_input, user_input_to_response_item
from tests.support.config import make_test_config

PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


def attachment(data=PNG, mime="image/png", **metadata):
    return {"image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}", **metadata}


def test_image_only_input_preserves_bytes_and_provider_fields(tmp_path):
    config = make_test_config(tmp_path)
    raw = attachment(filename="wrong.jpg")
    value = normalize_user_input("", [raw], config)
    assert value.images[0].media_type == "image/png"
    assert user_input_to_response_item(value) == {
        "role": "user",
        "content": [{"type": "input_image", "image_url": raw["image_url"], "detail": "auto"}],
    }
    assert "payload" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.text = "changed"


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"image_url": "data:image/png;base64,%%%secret"}, "invalid_image_base64"),
        (attachment(b"\xff\xd8\xffabc"), "image_media_type_mismatch"),
        (attachment(b"<svg/>", "image/svg+xml"), "unsupported_image_media_type"),
        ({"image_url": "https://example.com/test.png"}, "invalid_image_data_url"),
    ],
)
def test_invalid_model_attachments_have_payload_free_errors(tmp_path, raw, error):
    with pytest.raises(ValueError, match=error) as caught:
        normalize_user_input("", [raw], make_test_config(tmp_path))
    assert raw["image_url"] not in str(caught.value)


def test_decoded_byte_limits_and_image_count(tmp_path):
    config = make_test_config(
        tmp_path,
        {"images": {"max_image_bytes": len(PNG), "max_total_image_bytes": len(PNG) * 2, "max_images_per_input": 2}},
    )
    assert len(normalize_user_input("", [attachment(), attachment()], config).images) == 2
    for raws, error in [([attachment(PNG + b"x")], "image_too_large"), ([attachment()] * 3, "too_many_images")]:
        with pytest.raises(ValueError, match=error):
            normalize_user_input("", raws, config)
    config2 = make_test_config(
        tmp_path / "total", {"images": {"max_image_bytes": len(PNG), "max_total_image_bytes": len(PNG) * 2 - 1}}
    )
    with pytest.raises(ValueError, match="image_total_too_large"):
        normalize_user_input("", [attachment(), attachment()], config2)


def test_text_and_presentation_contract():
    assert user_input_to_response_item(UserInput("hello")) == {
        "role": "user",
        "content": [{"type": "input_text", "text": "hello"}],
    }
    with pytest.raises(ValueError):
        UserInput("  ")
    assert ImageContent("https://example.com/a.svg").image_url == "https://example.com/a.svg"
    for url in ("http://example.com/a.png", "javascript:alert(1)", "file:///a.png", "blob:abc"):
        with pytest.raises(ValueError):
            ImageContent(url)
