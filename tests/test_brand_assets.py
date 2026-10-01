"""Checks the delivered brand assets, not just their source SVG text."""
from pathlib import Path
import json
import zipfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'desktop/electron/assets'


def test_desktop_asset_formats_and_brand_pixels():
    for platform in ('macos', 'windows'):
        with Image.open(ASSETS / f'icon-{platform}-1024.png') as image:
            assert image.size == (1024, 1024)
            pixels = set(image.convert('RGBA').getdata())
            assert (36, 72, 184, 255) in pixels
            assert (247, 248, 250, 255) in pixels
            assert any(pixel[3] == 0 for pixel in pixels)
    with Image.open(ASSETS / 'Win.ico') as image:
        assert image.ico.sizes() == {(s, s) for s in (16, 24, 32, 48, 64, 128, 256)}
        assert image.ico.getimage((256, 256)).size == (256, 256)
    with Image.open(ASSETS / 'Mac.icns') as image:
        image.load()
        assert image.size == (1024, 1024)
    with Image.open(ASSETS / 'dmg-background.png') as image:
        assert image.size == (540, 380)
    package = json.loads((ASSETS.parent / 'package.json').read_text())
    assert package['build']['dmg']['background'] == 'assets/dmg-background.png'


def test_only_bundled_brand_theme_uses_current_accent():
    retired = bytes.fromhex('303036383338')  # Historical six-digit brand value.
    paths = list((ROOT / 'src/briefloop/template_assets').glob('*-t1.docx'))
    assert len(paths) == 9
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            xml = b'\n'.join(archive.read(n) for n in archive.namelist() if n.endswith('.xml'))
            assert retired not in xml.upper()
            assert b'2448B8' in xml


def test_launcher_uses_exact_authoritative_tokens_and_mark():
    static = ROOT / 'src/briefloop/static'
    assert (static / 'tokens.css').read_bytes() == (ASSETS / 'ui-tokens.css').read_bytes()
    assert (static / 'runtime-briefloop.svg').read_bytes() == (ASSETS / 'briefloop-mark.svg').read_bytes()
