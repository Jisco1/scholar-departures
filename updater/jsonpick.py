"""Pull the JSON answer out of a model reply that may also contain prose, citations
("[1]", "[source]") or a ```json fence. Standard library only, so the tests can import it."""

import json
import re

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text, opener="{", closer="}"):
    """The last complete JSON value that starts with `opener` — fenced blocks first."""
    for block in reversed(FENCE.findall(text)):
        block = block.strip()
        if block.startswith(opener):
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                pass
    end = text.rfind(closer)
    while end != -1:
        start = text.rfind(opener, 0, end)
        while start != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                start = text.rfind(opener, 0, start)
        end = text.rfind(closer, 0, end)
    raise ValueError("no JSON found in model output")
