"""Small server-side safety gate for creator-supplied storytelling requests."""

import re


_BLOCKED = (
    (re.compile(r"\b(?:child|minor|kid)\b.{0,50}\b(?:sexual|nude|porn)\b", re.I),
     "sexual content involving minors"),
    (re.compile(r"\b(?:how to|instructions? to)\b.{0,60}\b(?:build a bomb|poison|kill|suicide)\b", re.I),
     "instructions for serious harm"),
    (re.compile(r"\b(?:praise|celebrate|promote)\b.{0,50}\b(?:genocide|terrorist attack)\b", re.I),
     "praise for mass violence"),
)


def validate_creator_content(*values: str) -> None:
    text = " ".join(value for value in values if value).strip()
    if len(text) > 20_000:
        raise ValueError("The submitted text is too long.")
    for pattern, reason in _BLOCKED:
        if pattern.search(text):
            raise ValueError(f"This request cannot be processed because it contains {reason}.")
