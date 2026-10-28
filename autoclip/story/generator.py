"""
Viral Story Generation Engine for YouTube Shorts & TikTok Repurposing.
Generates gripping, high-retention 1.5–2.5 minute first-person narratives
(AITA, relationship drama, malicious compliance, unbelievable true stories)
via local LLM (Sandwich.cpp / llama-server) with curated offline fallbacks.
"""

from dataclasses import dataclass
import json
import logging
import random
import re
from typing import Optional

logger = logging.getLogger(__name__)


STORY_SYSTEM_PROMPT = """You are a master viral storyteller for YouTube Shorts and TikTok.
Your specialty is writing addictive, high-retention first-person drama stories (similar to r/AmItheAsshole, r/ProRevenge, and relationship confessions).

CRITICAL CONSTRAINTS:
1. Hook: The very first sentence MUST grab attention within 3 seconds. No boring introductions.
2. Pace: High suspense, emotional stakes, fast-moving plot with rising tension.
3. Length: Exactly 250 to 350 words (ideal for 110–130 seconds spoken at ~140 words per minute).
4. Tone: First-person ("I", "my husband", "my boss"), raw, conversational, relatable, emotional.
5. Ending: End with an open-ended question inviting intense viewer debate in the comments.
6. Headline: Generate an eye-catching, provocative title (6-12 words) in ALL CAPS for the top hook banner.

OUTPUT FORMAT:
You MUST respond ONLY with a raw JSON object with two fields:
{
  "headline": "AITA FOR RUINING MY SISTER'S WEDDING AFTER SHE REPLACED MY SERVICE DOG?",
  "story": "I am completely shaking as I write this..."
}
Do not include markdown codeblocks, explanation, or any other text.
"""

# Curated high-retention offline fallbacks when local LLM is unavailable
FALLBACK_STORIES = [
    {
        "category": "aita",
        "headline": "AITA FOR EXPOSING MY FIANCÉE'S SECRET BANK ACCOUNT AT OUR ENGAGEMENT DINNER?",
        "story": (
            "I found out my fiancée had been hiding a secret bank account with over eighty thousand dollars, "
            "while letting me work eighty-hour weeks to pay off our wedding venue. Last night, we had both "
            "our families over for our formal engagement dinner. My father-in-law gave a speech praising how "
            "responsible and modest his daughter is, and how lucky I am to support her. That was when I snapped. "
            "I pulled out the bank statements I discovered and quietly handed copies to everyone at the table. "
            "The room went completely silent. My fiancée turned pale as a ghost and started crying, calling me "
            "a vindictive monster for violating her privacy in front of her grandparents. Her mother screamed at me "
            "and threw her napkin, saying a real man never counts money when it comes to family. My own brother, "
            "however, pulled me aside and told me I just dodged a lifelong financial bullet. Now her entire family "
            "is blowing up my phone demanding an apology before they call off the wedding. Was I wrong for confronting "
            "her publicly, or did she have this coming?"
        ),
    },
    {
        "category": "revenge",
        "headline": "MY BOSS FIRED ME TO HIRE HIS NEPHEW, SO I TOOK MY PROPRIETARY SYSTEM WITH ME",
        "story": (
            "My boss summoned me into his office on a random Tuesday and told me my position was being eliminated "
            "due to corporate downsizing. Ten minutes later, I saw his twenty-year-old nephew sitting at my desk. "
            "What my boss forgot was that over the last four years, I built the entire inventory automation system "
            "from scratch on my personal cloud server using custom code that I held the intellectual property rights to. "
            "The employment contract specifically stated that proprietary software developed independently remained mine. "
            "The moment my termination was finalized, I revoked the server API keys and deleted the local database mirrors. "
            "By Thursday morning, their entire dispatch warehouse was completely paralyzed. Not a single order could ship. "
            "My former boss called me six times in thirty minutes, screaming that he was going to sue me for sabotage. "
            "I sent him section four of my contract and told him my consulting fee was four hundred dollars an hour, "
            "with a forty-hour minimum retainer upfront. He paid it within ten minutes. Was this petty, or did he get "
            "exactly what he deserved?"
        ),
    },
    {
        "category": "aita",
        "headline": "AITA FOR EVICTING MY SISTER AFTER SHE GAVE AWAY MY DOG TO HER FRIEND?",
        "story": (
            "I let my younger sister live in my guest apartment rent-free while she finished her nursing degree. "
            "The only rule I had was to take care of my five-year-old golden retriever, Buster, whenever I was traveling "
            "for work. Last week, I returned home from a four-day business trip to find Buster's bed and toys completely gone. "
            "When I demanded to know where he was, my sister calmly told me that her coworker had three young kids who "
            "needed a dog more than I did, so she gave Buster away as a surprise gift. I couldn't even breathe. I called "
            "the police immediately, drove to her coworker's house with the adoption papers and microchip records, and got "
            "Buster back within three hours. When I returned home, I changed the door locks and placed all of my sister's "
            "suitcases on the curb. She is now staying in a motel and my parents are furious, claiming blood is thicker "
            "than water and that an animal is never worth kicking out your own sister. Am I the bad guy here?"
        ),
    },
    {
        "category": "drama",
        "headline": "I CAUGHT MY BUSINESS PARTNER SELLING OUR CLIENTS TO HIS SECRET COMPETITOR",
        "story": (
            "For three years, my business partner and I ran a successful digital marketing agency. But over the last "
            "six months, four of our biggest enterprise contracts mysteriously cancelled and moved to an unknown boutique "
            "firm across town. My partner claimed the economy was rough and urged me to cut our staff salaries to stay afloat. "
            "Something felt wrong. I hired a private forensic investigator to audit our server access logs. What we discovered "
            "was unbelievable: that rival agency was registered under his wife's maiden name, and he had been systematically "
            "funneling our confidential pitch decks and client contracts straight to her inbox. Instead of confronting him, "
            "I called our corporate attorney. We filed an emergency injunction and froze every corporate account before "
            "he could siphon the remaining capital. When he arrived at the office Monday morning, two process servers were "
            "waiting with fraud subpoenas. Was I too ruthless, or would you have done the exact same thing?"
        ),
    },
]


@dataclass
class StoryRequest:
    category: str = "aita"  # "aita", "revenge", "drama", "custom"
    custom_prompt: Optional[str] = None
    target_duration_sec: float = 120.0


@dataclass
class GeneratedStory:
    headline: str
    story_text: str
    word_count: int
    estimated_duration_sec: float
    category: str

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "story_text": self.story_text,
            "word_count": self.word_count,
            "estimated_duration_sec": round(self.estimated_duration_sec, 1),
            "category": self.category,
        }


class StoryGenerator:
    """Generates viral drama stories using local LLM or curated templates."""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str = "sandwich",
        model: str = "sandwich-model",
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _estimate_duration(self, text: str) -> float:
        """Estimate spoken duration assuming ~145 words per minute."""
        words = len(text.split())
        return (words / 145.0) * 60.0

    def generate_story(self, request: Optional[StoryRequest] = None) -> GeneratedStory:
        """Generate a viral story either via local LLM or curated fallback."""
        if request is None:
            request = StoryRequest()

        # If user supplied custom story text in custom_prompt and it's long enough, use it directly
        if request.custom_prompt and len(request.custom_prompt.strip().split()) >= 30:
            raw_text = request.custom_prompt.strip()
            # If user has a headline on first line
            lines = raw_text.splitlines()
            if len(lines) > 1 and len(lines[0].split()) <= 15:
                headline = lines[0].strip().upper()
                story_body = "\n".join(lines[1:]).strip()
            else:
                headline = "TRUE STORY CONFESSION"
                story_body = raw_text

            wc = len(story_body.split())
            return GeneratedStory(
                headline=headline,
                story_text=story_body,
                word_count=wc,
                estimated_duration_sec=self._estimate_duration(story_body),
                category="custom",
            )

        # Try local LLM
        prompt_topic = request.custom_prompt or f"A dramatic, intense {request.category.upper()} story"
        user_message = (
            f"Write a viral {request.category.upper()} story about: {prompt_topic}. "
            "Must be between 250 and 320 words, with an attention-grabbing ALL-CAPS headline. "
            "Respond strictly in the JSON format requested."
        )

        try:
            from openai import OpenAI

            client = OpenAI(base_url=self.api_base_url, api_key=self.api_key)
            logger.info(f"Generating viral story via local LLM at {self.api_base_url}...")
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": STORY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.75,
                max_tokens=800,
                timeout=12.0,
            )
            raw_reply = response.choices[0].message.content or ""
            parsed = self._parse_llm_json(raw_reply)
            if parsed and "story" in parsed and len(parsed["story"].split()) >= 50:
                headline = parsed.get("headline", "AITA FOR THIS UNBELIEVABLE SITUATION?").strip().upper()
                story_text = parsed["story"].strip()
                wc = len(story_text.split())
                return GeneratedStory(
                    headline=headline,
                    story_text=story_text,
                    word_count=wc,
                    estimated_duration_sec=self._estimate_duration(story_text),
                    category=request.category,
                )
        except Exception as e:
            logger.warning(f"Local LLM call failed or timed out ({e}). Using curated fallback story.")

        # Fallback to curated templates
        matching = [s for s in FALLBACK_STORIES if s["category"] == request.category]
        picked = random.choice(matching if matching else FALLBACK_STORIES)
        wc = len(picked["story"].split())
        return GeneratedStory(
            headline=picked["headline"],
            story_text=picked["story"],
            word_count=wc,
            estimated_duration_sec=self._estimate_duration(picked["story"]),
            category=picked["category"],
        )

    def _parse_llm_json(self, raw_text: str) -> Optional[dict]:
        """Robustly extract JSON object from LLM response."""
        # Check direct json
        raw_text = raw_text.strip()
        try:
            return json.loads(raw_text)
        except Exception:
            pass

        # Check code fence
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except Exception:
                pass

        # Check outer curly braces
        curly_match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
        if curly_match:
            try:
                return json.loads(curly_match.group(1))
            except Exception:
                pass

        return None
