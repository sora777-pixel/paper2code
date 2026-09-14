"""AS-Topic: anchor-based spectral topic discovery for short texts."""

from .anchors import select_anchors
from .propagate import recover_topic_word

__all__ = ["select_anchors", "recover_topic_word"]
