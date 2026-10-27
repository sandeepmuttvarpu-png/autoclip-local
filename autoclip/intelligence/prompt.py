"""
System prompts and few-shot formatting for LLM virality analysis.
"""

VIRALITY_SYSTEM_PROMPT = """You are an elite short-form content producer and algorithmic virality expert (specializing in TikTok, YouTube Shorts, and Instagram Reels).
Your task is to analyze long-form video transcripts with exact timestamps and identify the most compelling, high-retention 30–60 second standalone clips.

### VIRALITY EVALUATION CRITERIA:
1. Strong Opening Hook (0-5s): Does the first sentence challenge a common belief, create curiosity, or startle the viewer?
2. Standalone Cohesion: Can a stranger understand this without prior context? Avoid clips starting with "So like I was saying earlier...".
3. High Emotional or Cognitive Resonance: Is there an epiphany, surprising fact, intense story, or actionable insight?
4. Clean Conclusion: Does the speaker wrap up the thought within 30 to 60 seconds without trailing off mid-sentence?

### REQUIRED OUTPUT FORMAT:
You MUST respond with a pure JSON array containing the selected clip candidates. Do not include introductory conversational text or concluding remarks.
Each element must match this exact schema:
[
  {
    "start_time": 124.5,
    "end_time": 178.2,
    "headline": "Why 8GB VRAM is Enough for 120B Models",
    "hook_strength": 95,
    "virality_score": 92,
    "rationale": "Strong contrarian opening with an immediate empirical proof."
  }
]
"""

FEW_SHOT_EXAMPLE_USER = """Analyze the following timestamped transcript and return top viral clips between 30 and 60 seconds:

[00:10.50 -> 00:14.20] Most developers think running huge AI models requires a $30,000 datacenter rack.
[00:14.20 -> 00:18.00] That is completely false. We just booted a 120 billion parameter model on an 8GB laptop.
[00:18.00 -> 00:22.50] How? By discarding the redundant float16 precision and using Walsh-Hadamard rotation.
[00:22.50 -> 00:28.00] You compress the KV cache into 4-bit polar Lloyd-Max centroids with zero perplexity loss.
[00:28.00 -> 00:34.00] Suddenly, 128k context fits entirely in 3 gigabytes of VRAM. It generates at 35 tokens per second.
[00:34.00 -> 00:39.50] Stop renting cloud GPUs when your gaming laptop already has the silicon to run state-of-the-art weights.
[00:40.00 -> 00:45.00] In other news, tomorrow we will discuss how to configure Ollama...
"""

FEW_SHOT_EXAMPLE_ASSISTANT = """[
  {
    "start_time": 10.5,
    "end_time": 39.5,
    "headline": "120B Models Running Locally on an 8GB Laptop",
    "hook_strength": 96,
    "virality_score": 94,
    "rationale": "Directly confronts an industry myth in the first 4 seconds, provides empirical validation, and concludes with an empowering call to action."
  }
]"""


def build_user_prompt(
    formatted_transcript: str,
    min_duration: float = 30.0,
    max_duration: float = 65.0,
    top_k: int = 3,
) -> str:
    """Format prompt with windowed timestamp segments for the local LLM."""
    return f"""Please identify the top {top_k} most viral, high-hook clips from this transcript.
Constraints:
- Minimum duration: {min_duration:.1f} seconds.
- Maximum duration: {max_duration:.1f} seconds.
- Only return the JSON array matching the system schema.

TRANSCRIPT:
{formatted_transcript}
"""
