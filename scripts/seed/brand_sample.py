"""Generate a synthetic logo for development; never activate it in production."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--from-spec", type=Path, help="Extract the approved horizontal logo")
    args = parser.parse_args()
    if args.from_spec:
        with ZipFile(args.from_spec) as archive:
            entry = archive.getinfo("word/media/image1.png")
            if entry.file_size != 16011:
                parser.error("Unexpected logo asset size")
            content = archive.read(entry)
        approved = "36f5c90246d157db16db387aa924bd8d3070a5581b8eb7b3cd1d458d59874f78"
        if hashlib.sha256(content).hexdigest() != approved:
            parser.error("The image does not match the frozen horizontal logo")
        args.output.write_bytes(content)
        return
    image = Image.new("RGB", (160, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((1, 1, 158, 38), outline="#20514e")
    draw.text((12, 12), "ERP TEST LOGO", fill="#20514e")
    image.save(args.output, format="PNG")


if __name__ == "__main__":
    main()
