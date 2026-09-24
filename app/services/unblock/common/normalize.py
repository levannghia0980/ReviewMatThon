import re

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def normalize_for_matching(text: str) -> str:
    if not text:
        return ""
    return text.lower().strip()
