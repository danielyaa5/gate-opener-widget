#!/usr/bin/env python3
"""Write the editable architecture illustration used by the README and blog."""
from pathlib import Path
from html import escape
import argparse


def box(x, y, title, lines, *, color, fill, dashed=False, height=112):
    dash = ' stroke-dasharray="7 5"' if dashed else ''
    texts = ''.join(f'<text x="{x+20}" y="{y+64+i*23}" class="detail">{escape(line)}</text>'
                    for i, line in enumerate(lines))
    return (f'<rect x="{x}" y="{y}" width="300" height="{height}" rx="16" fill="{fill}" stroke="{color}"{dash}/>'
            f'<text x="{x+20}" y="{y+34}" class="node">{escape(title)}</text>{texts}')


def diagram():
    parts = ['''<svg xmlns="http://www.w3.org/2000/svg" width="840" height="1180" viewBox="0 0 840 1180" role="img" aria-labelledby="title desc">
<title id="title">Gate Opener architecture</title>
<desc id="desc">Signed app and widget requests reach a bridge on an always-on Mac. TinyTuya sends a local relay pulse to the Aubric controller and existing gate board. Separately, Ring supplies snapshots to a local comparison against private labeled photos. Tuya cloud is used only to retrieve the local key during setup.</desc>
<defs>
 <marker id="blue" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="#2353b6"/></marker>
 <marker id="green" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#256f56"/></marker>
 <marker id="purple" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#8055a8"/></marker>
 <marker id="amber" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#93631d"/></marker>
</defs>
<style>
 text { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif; fill:#263344; }
 .node { font-size:22px; font-weight:650; }
 .detail { font-size:16px; fill:#465469; }
 .label { font-size:16px; font-weight:550; }
 .route { fill:none; stroke-width:2.5; stroke-linejoin:round; }
</style>
<rect width="840" height="1180" rx="22" fill="#fff"/>
<text x="40" y="48" font-size="28" font-weight="650">Taps, snapshots and the old gate</text>
<circle cx="48" cy="78" r="5" fill="#2353b6"/><text x="62" y="84" class="detail">Commands</text>
<circle cx="196" cy="78" r="5" fill="#256f56"/><text x="210" y="84" class="detail">Camera observations</text>
<circle cx="418" cy="78" r="5" fill="#8055a8"/><text x="432" y="84" class="detail">Reference photos</text>
<rect x="40" y="300" width="760" height="500" rx="22" fill="#f6f8fb" stroke="#c7d0dc"/>
<text x="420" y="340" text-anchor="middle" font-size="22" font-weight="650">Always-on bridge Mac</text>
''']
    parts += [box(80, 110, 'Mac / iPhone', ['Apps, widgets and controls', 'Mac-only learning popup'], color='#2353b6', fill='#edf3ff'),
              box(460, 110, 'Driveway camera + Ring', ['Pictures uploaded to Ring', 'Authenticated snapshot access'], color='#256f56', fill='#edf7f2'),
              box(80, 370, 'Python bridge', ['Verifies signed requests', 'Returns status / sends a pulse'], color='#2353b6', fill='#fff'),
              box(460, 370, 'Node.js snapshot reader', ['ring-client-api + private token', 'JPEG and original capture time'], color='#256f56', fill='#fff'),
              box(80, 580, 'Private reference library', ['Open / Closed examples', 'Labels from the Mac popup'], color='#8055a8', fill='#f6f0fc'),
              box(460, 580, 'Local image comparison', ['Open / Closed / Unclear', 'Rejects old or uncertain views'], color='#256f56', fill='#fff'),
              box(80, 900, 'Aubric Wi-Fi controller', ['TinyTuya over the home LAN', 'Momentary relay contact'], color='#2353b6', fill='#edf3ff', height=100),
              box(460, 900, 'Tuya IoT cloud: setup', ['Retrieve the device local key', 'Key stays on the bridge Mac'], color='#93631d', fill='#fff7e8', dashed=True, height=100),
              box(80, 1060, 'Existing gate board', ['Motor, gearbox and safety inputs'], color='#2353b6', fill='#edf3ff', height=88)]
    parts += ['''
<path d="M230 222V370" class="route" stroke="#2353b6" marker-start="url(#blue)" marker-end="url(#blue)"/>
<text x="250" y="260" class="label">Signed HTTPS</text>
<text x="250" y="282" class="detail">via Tailscale Funnel</text>
<path d="M610 222V370" class="route" stroke="#256f56" marker-end="url(#green)"/>
<text x="630" y="260" class="label">Snapshot JPEGs</text>
<path d="M610 482V580" class="route" stroke="#256f56" marker-end="url(#green)"/>
<text x="630" y="537" class="detail">Fresh frame</text>
<path d="M380 650H460" class="route" stroke="#8055a8" marker-end="url(#purple)"/>
<path d="M460 610H414V438H380" class="route" stroke="#256f56" marker-end="url(#green)"/>
<text x="427" y="559" transform="rotate(-90 427 559)" class="detail">Camera status</text>
<path d="M80 426H60V946H80" class="route" stroke="#2353b6" marker-end="url(#blue)"/>
<text x="80" y="860" class="label">Direct local control</text>
<text x="80" y="883" class="detail">No Tuya cloud call per tap</text>
<text x="80" y="749" class="detail">Optional auto-close rules require fresh Open observations.</text>
<text x="80" y="774" class="detail">Labeling a photo does not send a gate command.</text>
<path d="M610 900V800" class="route" stroke="#93631d" stroke-dasharray="7 5" marker-end="url(#amber)"/>
<text x="630" y="858" class="detail">Setup only</text>
<path d="M230 1000V1060" class="route" stroke="#2353b6" marker-end="url(#blue)"/>
<text x="246" y="1038" class="detail">Half-second dry contact</text>
<text x="480" y="1068" class="detail"><tspan x="480">Gate position comes from the camera,</tspan><tspan x="480" dy="25">not the relay acknowledgement.</tspan></text>
</svg>''']
    return '\n'.join(parts) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'docs/images/architecture.svg')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(diagram())
    print(f'Wrote {args.output}')
