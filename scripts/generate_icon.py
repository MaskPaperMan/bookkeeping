"""Render the geometric ledger icon to PNG and Windows 7 compatible ICO.

Only Python's standard library is needed; assets/taizhang.svg is the vector source.
"""
import math
import struct
import zlib
from pathlib import Path


def render(size):
    scale = size * 4 / 256
    width = size * 4
    pixels = bytearray(width * width * 4)

    def rect(x0, y0, x1, y1, radius, color):
        left, top, right, bottom, radius = [value * scale for value in (x0, y0, x1, y1, radius)]
        rgba = bytes.fromhex(color) + b'\xff'
        for y in range(max(0, int(top)), min(width, math.ceil(bottom))):
            for x in range(max(0, int(left)), min(width, math.ceil(right))):
                dx = max(left + radius - x - .5, 0, x + .5 - right + radius)
                dy = max(top + radius - y - .5, 0, y + .5 - bottom + radius)
                if dx * dx + dy * dy <= radius * radius:
                    offset = (y * width + x) * 4
                    pixels[offset:offset + 4] = rgba

    rect(8, 8, 248, 248, 48, '245bd6')
    rect(61, 43, 205, 220, 18, '1846a9')
    rect(53, 35, 195, 211, 16, 'ffffff')
    rect(53, 35, 82, 211, 14, 'dbe7ff')
    rect(70, 35, 84, 211, 0, 'dbe7ff')
    rect(155, 35, 174, 85, 0, 'ffbf47')
    # White triangular cutout at the bottom of the bookmark.
    for y in range(int(76 * scale), math.ceil(85 * scale)):
        half = (y / scale - 76) / 9 * 10
        rect(164 - half, y / scale, 164 + half, (y + 1) / scale, 0, 'ffffff')
    rect(99, 73, 140, 81, 4, '245bd6')
    rect(91, 107, 113, 129, 11, 'e2f6ec')
    rect(100, 111, 104, 125, 0, '238756')
    rect(95, 116, 109, 120, 0, '238756')
    rect(126, 115, 170, 121, 3, '647b9f')
    rect(91, 149, 113, 171, 11, 'fff0e4')
    rect(95, 158, 109, 162, 0, 'c76b28')
    rect(126, 157, 170, 163, 3, '647b9f')
    rect(98, 188, 170, 192, 2, 'd2ddec')
    result = bytearray()
    for y in range(size):
        for x in range(size):
            samples = [(yy * width + xx) * 4
                       for yy in range(y * 4, y * 4 + 4) for xx in range(x * 4, x * 4 + 4)]
            alpha = sum(pixels[index + 3] for index in samples)
            result.extend([round(sum(pixels[index + channel] * pixels[index + 3]
                                     for index in samples) / alpha) if alpha else 0
                           for channel in range(3)])
            result.append(round(alpha / 16))
    return bytes(result)


def png(size, rgba):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def dib(size, rgba):
    colors, masks = bytearray(), bytearray()
    for y in reversed(range(size)):
        mask = bytearray(((size + 31) // 32) * 4)
        for x in range(size):
            offset = (y * size + x) * 4
            r, g, b, a = rgba[offset:offset + 4]
            colors.extend((b, g, r, a))
            if a < 128:
                mask[x // 8] |= 128 >> (x % 8)
        masks.extend(mask)
    header = struct.pack('<IIIHHIIIIII', 40, size, size * 2, 1, 32, 0,
                         len(colors) + len(masks), 0, 0, 0, 0)
    return header + colors + masks


def main():
    folder = Path(__file__).resolve().parent.parent / 'assets'
    sizes = (16, 24, 32, 48, 64, 128, 256)
    directory, images = bytearray(), bytearray()
    for size in sizes:
        rgba = render(size)
        image = png(size, rgba) if size == 256 else dib(size, rgba)
        directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32,
                                     len(image), 6 + len(sizes) * 16 + len(images)))
        images.extend(image)
        if size == 256:
            (folder / 'taizhang.png').write_bytes(image)
    (folder / 'taizhang.ico').write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + directory + images)
    print('Generated assets/taizhang.png and assets/taizhang.ico')


if __name__ == '__main__':
    main()
