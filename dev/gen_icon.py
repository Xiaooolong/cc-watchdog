# Generate icon candidates with Gemini through an OpenAI-compatible gateway, using style reference images.
# usage: GEMINI_BASE_URL=... GEMINI_API_KEY=... python dev/gen_icon.py OUT_DIR REF_DIR [n per subject]
import base64
import io
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

STYLE = """The reference images show the illustration style of Anthropic's Claude (the rounded-square ones are Claude
app icons). Use them ONLY for style; do not copy their subjects.
Style: black brush line with natural thickness variation, loose and confident, as few strokes as possible; flat color
shapes with no outline, slightly offset from the line work; no gradients, shading or texture; minimal and calm with
generous space; never a cartoon face.
Format: app icon artwork, square 1:1. The entire canvas is flat pale terracotta #EBCBB5 edge to edge (it is cropped
into a rounded square later, so do NOT draw a rounded square, border or frame). The drawing sits on flat warm white
#FAF9F5 shapes, like the gauge icon reference. Centered; the main shape fills about 70% of the canvas. No text.
Meaning the icon must carry: this app watches over Claude Code chat sessions, and when a reply is cut off by a dropped
connection, it nudges the session to keep going.
Subject: """

SUBJECTS = {
    "dog_nudge": "a golden retriever drawn with the brush line, side profile of head and chest, gently pushing forward "
                 "with its nose a small flat white rounded block that carries a black brush-drawn play triangle. "
                 "The dog has a flat pale golden fill (#E8C38C) offset from its line.",
    "dog_watch": "a flat warm white circle; on it a golden retriever's head in side profile drawn with the brush line, "
                 "one ear lifted and alert as if listening, a single dot for the eye and one for the nose, a small flat "
                 "pale golden patch (#E8C38C) on the ear offset from the line.",
    "bubble": "a flat warm white speech bubble; across it runs one horizontal black brush line that breaks off with a "
              "small gap in the middle, then carries on and ends in an arrowhead pointing right: a reply that was cut "
              "and keeps going.",
}


def generate(prompt, references=(), model="gemini-3-pro-image"):
    base, key = os.environ["GEMINI_BASE_URL"].rstrip("/"), os.environ["GEMINI_API_KEY"]
    parts = []
    for ref in references:
        buf = io.BytesIO()
        Image.open(ref).convert("RGB").save(buf, format="PNG")
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}})
    body = {"model": model, "messages": [{"role": "user", "content": [*parts, {"type": "text", "text": prompt}]}]}
    request = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)
    content = data["choices"][0]["message"].get("content") or ""
    match = re.search(r"data:image/[a-z]+;base64,([A-Za-z0-9+/=]+)", content)
    if not match:
        raise RuntimeError(f"no image in response: {content[:300]}")
    return Image.open(io.BytesIO(base64.b64decode(match.group(1)))).convert("RGB")


if __name__ == "__main__":
    out, refs = Path(sys.argv[1]), sorted(Path(sys.argv[2]).glob("*.png"))
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(name, i) for name in SUBJECTS for i in range(n)]

    def one(job):
        name, i = job
        try:
            generate(STYLE + SUBJECTS[name], refs).save(out / f"{name}_{i}.png")
            return f"{name}_{i}: ok"
        except Exception as e:
            return f"{name}_{i}: {e!r}"

    with ThreadPoolExecutor(len(jobs)) as pool:
        for line in pool.map(one, jobs):
            print(line)
