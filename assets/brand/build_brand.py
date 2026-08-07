#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Generate the brand SVGs from one template.

GitHub proxies README images and strips <style> blocks, so a single
theme-aware SVG does not survive. The reliable pattern is two files with
literal fills plus a <picture> element in the README. Generating both from
one template here keeps them from drifting apart.

Run:  python assets/brand/build_brand.py
"""

import pathlib

OUT = pathlib.Path(__file__).parent

THEMES = {
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "line": "#30363d",
        "word": "#e6edf3", "tag": "#8b949e", "dim": "#6e7681",
        "row": "#8b949e", "pass": "#3fb950", "fail": "#f85149",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "line": "#d0d7de",
        "word": "#1f2328", "tag": "#59636e", "dim": "#818b98",
        "row": "#59636e", "pass": "#1a7f37", "fail": "#cf222e",
    },
}

SANS = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, &#34;Segoe UI&#34;, Roboto, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Adminwright. Agents build the admin console; the gate decides if it is real.">
  <title>Adminwright</title>
  <defs>
    <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1f6feb"/>
      <stop offset="1" stop-color="#8957e5"/>
    </linearGradient>
  </defs>

  <rect width="{W}" height="{H}" fill="{bg}"/>

  <g transform="translate({mark_x} {mark_y})">
    <rect x="0" y="0" width="128" height="104" rx="14" fill="url(#sweep)" opacity="0.14"/>
    <rect x="0.75" y="0.75" width="126.5" height="102.5" rx="13.25" fill="none" stroke="url(#sweep)" stroke-width="1.5"/>
    <line x1="0" y1="28" x2="128" y2="28" stroke="url(#sweep)" stroke-width="1.5" opacity="0.55"/>
    <circle cx="16" cy="14" r="4" fill="url(#sweep)"/>
    <circle cx="30" cy="14" r="4" fill="url(#sweep)" opacity="0.55"/>
    <circle cx="44" cy="14" r="4" fill="url(#sweep)" opacity="0.3"/>
    <path d="M36 66 l16 17 l34 -37" fill="none" stroke="url(#sweep)" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
  </g>

  <text x="{text_x}" y="{word_y}" fill="{word}" font-family="{SANS}" font-size="62" font-weight="700" letter-spacing="-1.5">Adminwright</text>
  <text x="{text_x}" y="{tag_y}" fill="{tag}" font-family="{SANS}" font-size="23">Agents build the admin console. The gate decides if it is real.</text>

  <g transform="translate({panel_x} {panel_y})">
    <rect x="0" y="0" width="946" height="112" rx="10" fill="{panel}" stroke="{line}" stroke-width="1"/>
    <text x="22" y="30" fill="{dim}" font-family="{MONO}" font-size="17" font-weight="500">$ adminwright validate --phase release</text>
    <line x1="22" y1="46" x2="924" y2="46" stroke="{line}" stroke-width="1"/>
    <text x="22" y="72" font-family="{MONO}" font-size="17" font-weight="500"><tspan fill="{fail}">ERROR</tspan><tspan fill="{row}" xml:space="preserve">  placeholder-scan     dataBinding: 'mock database'</tspan></text>
    <text x="22" y="96" font-family="{MONO}" font-size="17" font-weight="500"><tspan fill="{fail}">ERROR</tspan><tspan fill="{row}" xml:space="preserve">  lifecycle-reachable  state 'closed' has no command</tspan></text>
    <text x="646" y="72" fill="{pass}" font-family="{MONO}" font-size="17" font-weight="500">no mock data</text>
    <text x="646" y="96" fill="{pass}" font-family="{MONO}" font-size="17" font-weight="500">no orphan controls</text>
  </g>
</svg>
"""

# Banner for the README, and a taller variant for GitHub's 1280x640 social card.
LAYOUTS = {
    "banner": dict(W=1280, H=400, mark_x=84, mark_y=116, text_x=248,
                   word_y=168, tag_y=208, panel_x=250, panel_y=246),
    "social": dict(W=1280, H=640, mark_x=84, mark_y=214, text_x=248,
                   word_y=266, tag_y=306, panel_x=166, panel_y=372),
}


def main():
    written = []
    for layout_name, layout in LAYOUTS.items():
        for theme_name, colors in THEMES.items():
            svg = TEMPLATE.format(SANS=SANS, MONO=MONO, **layout, **colors)
            path = OUT / f"{layout_name}-{theme_name}.svg"
            path.write_text(svg, encoding="utf-8", newline="\n")
            written.append(path.name)
    print("wrote: " + ", ".join(written))


if __name__ == "__main__":
    main()
