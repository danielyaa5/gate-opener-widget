#!/usr/bin/env python3
"""Generate a fictional Lock Screen illustration; macOS supplies the real SF Symbol.
Run: python3 scripts/generate_lockscreen.py
No device, account, network, or gate commands are used.
"""
import tempfile
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = r'''
import AppKit
let image = NSImage(systemSymbolName: "door.garage.open", accessibilityDescription: nil)!.withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 80, weight: .regular))!
let canvas = NSImage(size: NSSize(width: 120, height: 120))
canvas.lockFocus()
NSColor.white.set()
image.draw(in: NSRect(x: 16, y: 16, width: 88, height: 88))
canvas.unlockFocus()
let bitmap = NSBitmapImageRep(data: canvas.tiffRepresentation!)!
print(bitmap.representation(using: .png, properties: [:])!.base64EncodedString())
'''
icon = subprocess.check_output(["swift", "-module-cache-path", str(Path(tempfile.gettempdir()) / "gate-demo-swift-cache"), "-e", swift], text=True).strip()
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="720" height="900" viewBox="0 0 720 900" role="img" aria-labelledby="title desc">
<title id="title">Gate control on an iPhone Lock Screen</title>
<desc id="desc">Illustrative Lock Screen with neutral mountain wallpaper. An arrow points to the gate control at the bottom right.</desc>
<defs>
 <filter id="white"><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 1 0"/></filter>
 <linearGradient id="sky" x2="0" y2="1"><stop stop-color="#284c53"/><stop offset="1" stop-color="#a6bab0"/></linearGradient>
 <linearGradient id="hill" x2="0" y2="1"><stop stop-color="#3f6565"/><stop offset="1" stop-color="#173d43"/></linearGradient>
 <clipPath id="screen"><rect x="152" y="47" width="416" height="796" rx="57"/></clipPath>
 <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0 0 6 3 0 6" fill="none" stroke="#f1ba62" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></marker>
</defs>
<rect width="720" height="900" fill="#e7ebe6"/>
<ellipse cx="360" cy="851" rx="207" ry="16" fill="#213d3b" opacity=".12"/>
<rect x="141" y="36" width="438" height="818" rx="68" fill="#151d1d"/>
<g clip-path="url(#screen)">
<rect x="152" y="47" width="416" height="796" fill="url(#sky)"/>
<circle cx="466" cy="370" r="41" fill="#e1e7ce" opacity=".8"/>
<path d="M140 590 245 420 318 514 389 459 588 659V850H140Z" fill="#70928a"/>
<path d="M140 710 298 531 364 607 429 554 589 720V850H140Z" fill="url(#hill)"/>
<path d="M140 796Q330 645 590 752V850H140Z" fill="#1c4247"/>
<g fill="#f4f5e7" font-family="Helvetica Neue,Arial,sans-serif" text-anchor="middle">
<text x="360" y="157" font-size="24" font-weight="500">Tuesday, October 20</text>
<text x="360" y="253" font-size="100" font-weight="300" letter-spacing="-4">9:41</text>
</g>
<rect x="292" y="63" width="136" height="35" rx="18" fill="#111919"/>
<g fill="#edf3e9" opacity=".95"><rect x="502" y="77" width="26" height="12" rx="3" fill="none" stroke="#edf3e9" stroke-width="2"/><rect x="505" y="80" width="18" height="6" rx="1"/><rect x="530" y="81" width="2" height="4" rx="1"/></g>
<circle cx="495" cy="771" r="34" fill="#f0f5e9" fill-opacity=".16" stroke="#ebf3df" stroke-opacity=".5"/>
<image filter="url(#white)" x="469" y="745" width="52" height="52" xlink:href="data:image/png;base64,{icon}"/>
<rect x="290" y="822" width="140" height="5" rx="3" fill="#f4f7ee"/>
</g>
<path d="M197 732 C290 700 374 700 451 758" fill="none" stroke="#f1ba62" stroke-width="5" stroke-linecap="round" marker-end="url(#arrow)"/>
<g fill="#fff9eb" font-family="Helvetica Neue,Arial,sans-serif" font-size="25" font-weight="600"><text x="184" y="666">Open or close</text><text x="184" y="699">the gate</text></g>
<text x="360" y="885" text-anchor="middle" font-family="Helvetica Neue,Arial,sans-serif" font-size="16" fill="#53625d">Lock Screen illustration</text>
</svg>'''
output = ROOT / "docs/images/lock-screen-control.svg"
output.write_text(svg)
print(output.relative_to(ROOT))
