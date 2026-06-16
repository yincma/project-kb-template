from __future__ import annotations

VISION_CAPTION_PROMPT_VERSION = "vision-caption-v1"

VISION_CAPTION_PROMPT = """You are generating a Project KB visual asset summary.

Extract only evidence visible in the image or clearly supported by surrounding text.
Preserve original Chinese, English, and Japanese technical terms when they appear.
For architecture diagrams, identify:
- system or business purpose
- main components
- AWS service names
- network boundaries
- data flow
- authentication and authorization
- monitoring and logging
- uncertain items

Do not guess. Put uncertain or low-confidence items in Uncertain Items.
"""

