#!/usr/bin/env python3
"""
Generate the CodeFission logo/hero image using DALL-E 3.

Usage:
    python scripts/gen_logo.py
    python scripts/gen_logo.py --variant dark
    python scripts/gen_logo.py --variant icon

Requires OPENAI_API_KEY in environment.
Output saved to assets/ directory.
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

VARIANTS = {
    "hero": {
        "filename": "codefission-hero.png",
        "size": "1792x1024",
        "prompt": (
            "A dramatic digital art logo for 'CodeFission', a software tool. "
            "Dark background (#0d1117). A glowing tree structure made of light — "
            "the trunk splits into branches, each branch splits again, like a "
            "git branch tree but also like nuclear fission splitting atoms. "
            "Nodes at each split point glow electric blue and soft purple. "
            "Fine lines of light connect the nodes like neural pathways. "
            "Code fragments and symbols float faintly in the background. "
            "The overall shape is wide and cinematic. Ultra sharp, high contrast, "
            "photorealistic lighting. No text, no letters, no words."
        ),
    },
    "icon": {
        "filename": "codefission-icon.png",
        "size": "1024x1024",
        "prompt": (
            "A minimal app icon for 'CodeFission'. Dark navy background. "
            "A single glowing symbol: a forking branch shape — like a Y or tree fork — "
            "but also resembling an atom splitting. Electric blue and purple gradient glow. "
            "The lines are thin, precise, and luminous. "
            "Perfectly centered, square composition, no text, no letters. "
            "Style: clean, modern, iOS/macOS app icon aesthetic."
        ),
    },
    "dark": {
        "filename": "codefission-dark.png",
        "size": "1792x1024",
        "prompt": (
            "Wide cinematic hero image for a developer tool called CodeFission. "
            "Pitch black background. In the center, a luminous branching tree grows upward — "
            "each node is a glowing orb of electric blue light, connected by thin neon lines. "
            "The tree branches outward symmetrically, like both a git history graph and "
            "a particle physics collision diagram. Faint code text scrolls in the deep background. "
            "Dramatic, dark, beautiful. Like a still from a sci-fi film about AI. "
            "No text. No letters. Photorealistic digital art."
        ),
    },
}


def generate(variant: str, quality: str = "hd"):
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not found. Install it: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set in environment.")
        sys.exit(1)

    cfg = VARIANTS[variant]
    out_dir = Path(__file__).parent.parent / "assets"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / cfg["filename"]

    client = OpenAI(api_key=api_key)

    print(f"Generating {variant} image with DALL-E 3...")
    print(f"Size: {cfg['size']}, Quality: {quality}")

    response = client.images.generate(
        model="dall-e-3",
        prompt=cfg["prompt"],
        size=cfg["size"],
        quality=quality,
        n=1,
    )

    image_url = response.data[0].url
    revised_prompt = response.data[0].revised_prompt

    print(f"\nRevised prompt: {revised_prompt}\n")
    print(f"Downloading image...")

    urllib.request.urlretrieve(image_url, out_path)
    print(f"Saved to: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate CodeFission logo via DALL-E 3")
    parser.add_argument(
        "--variant",
        choices=list(VARIANTS.keys()),
        default="hero",
        help="Image variant to generate (default: hero)",
    )
    parser.add_argument(
        "--quality",
        choices=["standard", "hd"],
        default="hd",
        help="Image quality (default: hd)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all variants",
    )
    args = parser.parse_args()

    if args.all:
        for v in VARIANTS:
            generate(v, args.quality)
    else:
        generate(args.variant, args.quality)
