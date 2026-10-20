"""Fictional camera images for demos. Never reads photos, settings or accounts."""
from __future__ import annotations

import base64
import io


def camera_image(state: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (960, 540), "#dce9ee")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 110, 960, 270), fill="#8fa98b")
    for x, y, size in [(40, 140, 120), (180, 110, 100), (820, 100, 145), (940, 140, 110)]:
        draw.rectangle((x - 8, y, x + 8, 360), fill="#6b7864")
        draw.ellipse((x - size, y - size, x + size, y + size), fill="#7b9975")
    draw.polygon([(0, 280), (960, 260), (960, 540), (0, 540)], fill="#e9e3d5")
    draw.polygon([(290, 250), (680, 250), (890, 540), (60, 540)], fill="#c8c4ba")
    for y in range(320, 540, 65):
        draw.line((100, y, 890, y - 15), fill="#b7b4ab", width=3)
    draw.rectangle((0, 235, 240, 340), fill="#f4f1e7")
    draw.rectangle((730, 235, 960, 340), fill="#f4f1e7")
    draw.rectangle((236, 205, 280, 365), fill="#d8d5ca")
    draw.rectangle((695, 205, 739, 365), fill="#d8d5ca")
    if state == "closed":
        draw.rectangle((280, 260, 695, 337), fill="#f9f8f1", outline="#b5b9b4", width=3)
        for x in range(290, 695, 23):
            draw.line((x, 263, x, 333), fill="#d2d6cf", width=3)
        draw.line((486, 260, 486, 337), fill="#9ca79f", width=4)
    else:
        for points in [[(280, 260), (325, 359), (325, 429), (280, 337)],
                       [(695, 260), (650, 359), (650, 429), (695, 337)]]:
            draw.polygon(points, fill="#f9f8f1", outline="#aab6aa", width=3)
    draw.rounded_rectangle((28, 28, 294, 70), radius=12, fill="#344e46")
    draw.text((43, 39), "SIMULATED CAMERA", fill="white", font_size=18)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def preview(state: str, captured_at: float) -> dict:
    return {"camera_name": "Demo camera", "ring_url": "https://account.ring.com/account/dashboard",
            "captured_at": captured_at, "image_base64": base64.b64encode(camera_image(state)).decode("ascii")}
