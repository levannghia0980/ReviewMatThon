"""
Re-export models for compatibility with older imports expecting app.models.schema
"""
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.models.dictionary import PhraseDictionary, NamesDictionary, UnblockDictionary

__all__ = [
    "ProjectTask",
    "DialogueSegmentModel",
    "PhraseDictionary",
    "NamesDictionary",
    "UnblockDictionary",
]
