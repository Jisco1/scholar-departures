"""Pull the JSON answer out of a model reply that may also contain prose, citations
("[1]", "[source]") or a ```json fence. Standard library only, so the tests can import it."""

import json
import re

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def no_em_dashes(value):
    """The owner does not want em dashes anywhere on the site, so model replies get commas instead."""
    if isinstance(value, str):
        return re.sub(r"\s*—\s*", ", ", value)
    if isinstance(value, list):
        return [no_em_dashes(v) for v in value]
    if isinstance(value, dict):
        return {k: no_em_dashes(v) for k, v in value.items()}
    return value


def extract_json(text, opener="{", closer="}"):
    """The last complete JSON value that starts with `opener`, fenced blocks first, without em dashes."""
    for block in reversed(FENCE.findall(text)):
        block = block.strip()
        if block.startswith(opener):
            try:
                return no_em_dashes(json.loads(block))
            except json.JSONDecodeError:
                pass
    end = text.rfind(closer)
    while end != -1:
        start = text.rfind(opener, 0, end)
        while start != -1:
            try:
                return no_em_dashes(json.loads(text[start:end + 1]))
            except json.JSONDecodeError:
                start = text.rfind(opener, 0, start)
        end = text.rfind(closer, 0, end)
    raise ValueError("no JSON found in model output")
