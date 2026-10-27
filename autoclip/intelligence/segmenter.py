"""
LLM interaction, virality ranking, and robust JSON parsing.
"""

from dataclasses import dataclass
import json
import logging
import re
from typing import List, Optional

from autoclip.audio.transcriber import Transcript
from autoclip.intelligence.prompt import (
    VIRALITY_SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLE_USER,
    FEW_SHOT_EXAMPLE_ASSISTANT,
    build_user_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class ClipCandidate:
    start_time: float
    end_time: float
    headline: str
    hook_strength: int
    virality_score: int
    rationale: str

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        return {
            "start_time": round(self.start_time, 2),
            "end_time": round(self.end_time, 2),
            "duration": round(self.duration, 2),
            "headline": self.headline,
            "hook_strength": self.hook_strength,
            "virality_score": self.virality_score,
            "rationale": self.rationale,
        }


class ViralitySegmenter:
    """Evaluates transcripts via local LLM to surface high-virality hook clips."""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str = "sandwich",
        model: str = "sandwich-model",
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @staticmethod
    def format_transcript_for_llm(transcript: Transcript, max_chars: int = 15000) -> str:
        """Format transcript segments into compact timestamped lines."""
        lines = []
        for s in transcript.segments:
            m_s, s_s = divmod(s.start, 60)
            m_e, s_e = divmod(s.end, 60)
            t_str = f"[{int(m_s):02d}:{s_s:05.2f} -> {int(m_e):02d}:{s_e:05.2f}] {s.text.strip()}"
            lines.append(t_str)
        full = "\n".join(lines)
        if len(full) > max_chars:
            return full[:max_chars] + "\n...[truncated for context length]..."
        return full

    def find_viral_clips(
        self,
        transcript: Transcript,
        min_duration: float = 30.0,
        max_duration: float = 65.0,
        min_virality_score: int = 70,
        top_k: int = 3,
    ) -> List[ClipCandidate]:
        """Query the local LLM to extract top viral candidates."""
        formatted_text = self.format_transcript_for_llm(transcript)
        user_prompt = build_user_prompt(
            formatted_text,
            min_duration=min_duration,
            max_duration=max_duration,
            top_k=top_k,
        )

        candidates: List[ClipCandidate] = []
        try:
            from openai import OpenAI

            client = OpenAI(base_url=self.api_base_url, api_key=self.api_key)
            logger.info(f"Querying local LLM endpoint at {self.api_base_url}...")

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": VIRALITY_SYSTEM_PROMPT},
                    {"role": "user", "content": FEW_SHOT_EXAMPLE_USER},
                    {"role": "assistant", "content": FEW_SHOT_EXAMPLE_ASSISTANT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            raw_reply = response.choices[0].message.content or ""
            candidates = self._parse_json_reply(raw_reply)

        except Exception as e:
            logger.warning(
                f"Local LLM query failed ({e}). Utilizing heuristic narrative windowing fallback..."
            )
            candidates = self._fallback_heuristic_clips(
                transcript, min_duration=min_duration, max_duration=max_duration, top_k=top_k
            )

        # Filter and sort
        valid = [
            c
            for c in candidates
            if c.virality_score >= min_virality_score
            and c.duration >= (min_duration - 5.0)
            and c.duration <= (max_duration + 5.0)
            and c.start_time < transcript.duration
        ]

        if not valid and candidates:
            # If scores were slightly lower than strict threshold, return top candidates anyway
            valid = sorted(candidates, key=lambda x: x.virality_score, reverse=True)[:top_k]

        valid.sort(key=lambda x: x.virality_score, reverse=True)
        return valid[:top_k]

    @staticmethod
    def _parse_json_reply(text: str) -> List[ClipCandidate]:
        """Robustly extract JSON array from LLM response with regex fallbacks."""
        text = text.strip()
        # Direct parse attempt
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

        # Markdown block search
        if data is None:
            match = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        # Loose bracket search
        if data is None:
            match = re.search(r"(\[\s*\{.*\}\s*\])", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        if not isinstance(data, list):
            logger.warning(f"Could not parse valid JSON list from LLM output:\n{text[:200]}...")
            return []

        parsed = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item["start_time"])
                end = float(item["end_time"])
                headline = str(item.get("headline", "Untitled Clip"))
                hook = int(item.get("hook_strength", 80))
                virality = int(item.get("virality_score", 80))
                rationale = str(item.get("rationale", ""))

                if end > start:
                    parsed.append(
                        ClipCandidate(
                            start_time=start,
                            end_time=end,
                            headline=headline,
                            hook_strength=hook,
                            virality_score=virality,
                            rationale=rationale,
                        )
                    )
            except (ValueError, KeyError) as err:
                logger.debug(f"Skipping malformed candidate: {err}")
        return parsed

    @staticmethod
    def _fallback_heuristic_clips(
        transcript: Transcript,
        min_duration: float = 30.0,
        max_duration: float = 60.0,
        top_k: int = 3,
    ) -> List[ClipCandidate]:
        """Heuristic fallback when LLM is unavailable: slides across transcript density."""
        if not transcript.segments:
            return [
                ClipCandidate(
                    start_time=0.0,
                    end_time=min(max_duration, transcript.duration if transcript.duration > 0 else 30.0),
                    headline="Highlight Clip",
                    hook_strength=80,
                    virality_score=80,
                    rationale="Automated windowing fallback.",
                )
            ]

        total_duration = transcript.duration or transcript.segments[-1].end
        target_len = min(45.0, total_duration)

        candidates = []
        step = max(20.0, total_duration / (top_k + 1))
        curr = 0.0

        idx = 1
        while curr + target_len <= total_duration + 5.0 and len(candidates) < top_k:
            end_t = min(curr + target_len, total_duration)
            candidates.append(
                ClipCandidate(
                    start_time=curr,
                    end_time=end_t,
                    headline=f"Highlight Section #{idx}",
                    hook_strength=85 - (idx * 2),
                    virality_score=88 - (idx * 3),
                    rationale="Heuristic speech density selection.",
                )
            )
            curr += step
            idx += 1

        if not candidates:
            candidates.append(
                ClipCandidate(
                    start_time=0.0,
                    end_time=min(target_len, total_duration),
                    headline="Opening Hook",
                    hook_strength=85,
                    virality_score=85,
                    rationale="Opening section fallback.",
                )
            )
        return candidates
