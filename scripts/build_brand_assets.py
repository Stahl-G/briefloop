"""Rebuild v3.1 desktop assets from the supplied mark and platform silhouettes.

Requires Inkscape (SVG rasterization) and Pillow (ICO/ICNS packaging).
Only repository-owned assets are modified; saved workspace artwork is untouched.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'desktop/electron/assets'
SVG = '{http://www.w3.org/2000/svg}'

def main():
    mark_path = ROOT / 'src/briefloop/static/runtime-briefloop.svg'
    mark = ET.parse(mark_path).getroot().find(f'{SVG}path')
    tokens = (ROOT / 'src/briefloop/static/tokens.css').read_text()
    brand = re.search(r'--c-blue-600:\s*(#[\da-fA-F]+)', tokens)[1]
    paper = re.search(r'--c-paper:\s*(#[\da-fA-F]+)', tokens)[1]
    for platform in ('macos', 'windows'):
        path = ASSETS / f'icon-{platform}.svg'
        old = ET.parse(path).getroot()
        silhouette = old.find(f'{SVG}path').attrib['d']
        transform = old.find(f'{SVG}g').attrib['transform']
        # Keep platform silhouette/padding, apply supplied geometry as reversed mark.
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024" role="img" aria-label="BriefLoop 应用图标"><path d="{silhouette}" fill="{brand}"/><g transform="{transform}"><path d="{mark.attrib["d"]}" fill="{paper}" fill-rule="evenodd"/></g></svg>\n')
        png = ASSETS / f'icon-{platform}-1024.png'
        subprocess.run(['inkscape', str(path), '-o', str(png), '-w', '1024', '-h', '1024'], check=True, capture_output=True)
    Image.open(ASSETS / 'icon-macos-1024.png').save(ASSETS / 'Mac.icns', format='ICNS')
    Image.open(ASSETS / 'icon-windows-1024.png').save(ASSETS / 'Win.ico', format='ICO', sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
    # The installer is a quiet neutral surface. The two actual Finder icons
    # occupy x=150 / 390, y=210; decorative arrow stays between them.
    dmg = ASSETS / 'dmg-background.svg'
    dmg.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="540" height="380" viewBox="0 0 540 380"><rect width="540" height="380" fill="{paper}"/><g transform="translate(36 28) scale(.32)"><path d="{mark.attrib['d']}" fill="{brand}" fill-rule="evenodd"/></g><text x="82" y="52" fill="#1A1F2B" font-family="sans-serif" font-size="22" font-weight="600">BriefLoop</text><path d="M242 210h56m-10-10 10 10-10 10" fill="none" stroke="{brand}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><text x="270" y="320" text-anchor="middle" fill="#5F6675" font-family="sans-serif" font-size="14">Drag BriefLoop to Applications</text></svg>''')
    subprocess.run(['inkscape', str(dmg), '-o', str(ASSETS/'dmg-background.png'), '-w','540','-h','380'],check=True,capture_output=True)
    files = ['icon-macos.svg','icon-windows.svg','icon-macos-1024.png','icon-windows-1024.png','Mac.icns','Win.ico','dmg-background.svg','dmg-background.png']
    info = {'design':'3.1','source_mark':'src/briefloop/static/runtime-briefloop.svg','source_mark_sha256':hashlib.sha256(mark_path.read_bytes()).hexdigest(),'derivation':'Supplied mark geometry; retained platform silhouettes and padding; token-based brand and paper colors. Rasterized with Inkscape; packaged with Pillow.','files':[{'path':name,'bytes':(ASSETS/name).stat().st_size,'sha256':hashlib.sha256((ASSETS/name).read_bytes()).hexdigest()} for name in files]}
    (ASSETS/'source-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')

if __name__ == '__main__':
    main()
