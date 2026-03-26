#!/usr/bin/env python3
"""
Generate CodeFission icon candidates using gpt-image-1 with transparent backgrounds.
Output saved to assets/icon-candidates/
"""

import base64
import os
import sys
from pathlib import Path

FISSION_BOLD_COLORS = [
    {
        "filename": "icon_fission_bold_amber.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Rich amber-to-burnt-orange gradient fill, with a warm golden glow. "
            "Feels like fire and energy, not tech-purple. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_crimson.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Deep crimson-to-bright-red gradient, with a hot white glow at the branch tips. "
            "Powerful, nuclear, dangerous energy. Bold and distinctive. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_teal.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Teal-to-emerald gradient, like deep ocean water or terminal green. "
            "Clean, technical, developer-tool aesthetic without being generic. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_gold.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Metallic gold-to-champagne gradient with a polished, premium sheen. "
            "Like a trophy or premium tier badge. Confident and distinctive. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_cyan.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Bright ice-cyan to white gradient, with a cold electric glow — "
            "like plasma or a laser beam. Crisp, high-tech, sharp. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_coral.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Coral-to-magenta gradient, warm and vivid — stands out from every other dev tool. "
            "Energetic, friendly, but still technical. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
    {
        "filename": "icon_fission_bold_white.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Solid bright white with very subtle cool-grey shadow and depth. "
            "Monochrome, timeless, works on any dark background. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
]

ICONS = [
    {
        "filename": "icon_fission_atom.png",
        "prompt": (
            "App icon: a stylized atom splitting into two halves, each half "
            "trailing electric blue light streaks. The split line glows white-hot "
            "at the center. Clean vector aesthetic, flat with subtle glow, "
            "electric blue and indigo color palette. Transparent background. "
            "No text. Centered square composition."
        ),
    },
    {
        "filename": "icon_branch_fork.png",
        "prompt": (
            "App icon: a minimalist git branch fork — a single line splits into "
            "two diverging paths, each ending in a glowing circle node. "
            "Gradient from cyan to violet along the lines. Ultra-clean, "
            "geometric, like a modern developer tool logo. "
            "Transparent background. No text."
        ),
    },
    {
        "filename": "icon_lightning_tree.png",
        "prompt": (
            "App icon: a lightning bolt that branches into a tree at the bottom — "
            "the bolt hits a node and splits into multiple glowing branches. "
            "Bright electric yellow-white bolt, branches fade to blue-purple. "
            "Bold, energetic, high contrast. Transparent background. No text."
        ),
    },
    {
        "filename": "icon_code_split.png",
        "prompt": (
            "App icon: two overlapping code bracket symbols < > with a vertical "
            "split crack of light down the center, as if the code is being "
            "fissioned apart. Glowing neon green and cyan on the edges of the split. "
            "Dark icon with glow effects. Transparent background. No text."
        ),
    },
    {
        "filename": "icon_nucleus_branch.png",
        "prompt": (
            "App icon: a central glowing nucleus with 3 branching paths radiating "
            "outward diagonally, each branch ending in a smaller glowing dot — "
            "like a simplified particle physics decay diagram, but also resembling "
            "a git branch graph rotated 45 degrees. "
            "Deep purple to electric blue gradient. Minimalist. "
            "Transparent background. No text."
        ),
    },
    {
        "filename": "icon_diamond_split.png",
        "prompt": (
            "App icon: a diamond shape split cleanly down the middle, the two halves "
            "drifting slightly apart with a glowing seam between them. "
            "Each half has a crystalline, faceted interior with blue and violet hues. "
            "Elegant, gem-like, premium app icon aesthetic. "
            "Transparent background. No text."
        ),
    },
    {
        "filename": "icon_tree_minimal.png",
        "prompt": (
            "App icon: an extremely minimal binary tree — just three circles connected "
            "by two lines forming a Y shape. The top circle is largest and brightest, "
            "bottom two are smaller. All circles glow with a soft blue-white light. "
            "Lines are thin and precise. Lots of negative space. "
            "Swiss design, ultra-minimal. Transparent background. No text."
        ),
    },
    {
        "filename": "icon_fission_bold.png",
        "prompt": (
            "App icon: bold, chunky letter F stylized as a branching path — "
            "the horizontal strokes of the F become branches splitting off a central stem. "
            "Bright gradient fill from electric blue at top to violet at bottom. "
            "Modern wordmark-icon hybrid, strong and legible at small sizes. "
            "Transparent background."
        ),
    },
]


def generate_all(icons=None):
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not found. Install: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set.")
        sys.exit(1)

    if icons is None:
        icons = ICONS

    out_dir = Path(__file__).parent.parent / "assets" / "icon-candidates"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = OpenAI(api_key=api_key)

    for i, cfg in enumerate(icons, 1):
        out_path = out_dir / cfg["filename"]
        print(f"[{i}/{len(ICONS)}] Generating {cfg['filename']}...")
        try:
            response = client.images.generate(
                model="gpt-image-1",
                prompt=cfg["prompt"],
                size="1024x1024",
                quality="high",
                n=1,
            )
            image_b64 = response.data[0].b64_json
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(image_b64))
            print(f"    Saved: {out_path}")
        except Exception as e:
            print(f"    ERROR: {e}")

    print(f"\nDone. Icons saved to: {out_dir}")


if __name__ == "__main__":
    generate_all(FISSION_BOLD_COLORS)
