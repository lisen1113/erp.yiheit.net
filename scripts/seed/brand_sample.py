"""Generate a synthetic logo for development; never activate it in production."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    image = Image.new("RGB", (160, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((1, 1, 158, 38), outline="#20514e")
    draw.text((12, 12), "ERP TEST LOGO", fill="#20514e")
    image.save(args.output, format="PNG")


if __name__ == "__main__":
    main()
