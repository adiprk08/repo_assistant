"""Reasoning pipeline: grounded answering with verified citations."""

from repo_assistant.reasoning.citations import VerifiedCitation, verify_citations
from repo_assistant.reasoning.pipeline import RoutedAnswer, answer_routed
from repo_assistant.reasoning.service import Answer, answer_question, generate_answer

__all__ = [
    "Answer",
    "RoutedAnswer",
    "VerifiedCitation",
    "answer_question",
    "answer_routed",
    "generate_answer",
    "verify_citations",
]
