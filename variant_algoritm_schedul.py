"""
variant_algoritm_schedul.py

Chunk-baserad styrlogik för att välja vilken textvariant som ska användas i artefakten.

Den här versionen väljer variant per CHUNK i stället för per segment.

Preset-idéer som stöds:
- bara original
- bara critical
- bara hallucinated
- bara authoritative_ai
- slumpmässig variant per chunk
- samma variant i grupper om 2 chunks
- tidsstyrd blandning per chunk
- realistisk jämn fördelning (med regler för att undvika för mycket avvikelse i rad)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from anyio import current_time


@dataclass
class SchedulerConfig:
    strategy: str = "fixed"
    fixed_variant: str = "original"
    seed: Optional[int] = None
    chunk_group_size: int = 2

    allow_variants: list[str] = field(default_factory=lambda: [
        "original",
        "critical",
        "hallucinated",
        "authoritative_ai",
    ])

    weights_global: dict[str, float] = field(default_factory=lambda: {
        "original": 0.25,
        "critical": 0.25,
        "hallucinated": 0.20,
        "authoritative_ai": 0.30,
    })


def normalize_weights(weights: dict[str, float], allowed_variants: list[str]) -> dict[str, float]:
    filtered = {
        name: float(value)
        for name, value in weights.items()
        if name in allowed_variants and value > 0
    }

    if not filtered:
        raise ValueError("Inga giltiga vikter kvar efter filtrering.")

    total = sum(filtered.values())
    if total <= 0:
        raise ValueError("Vikterna måste summera till mer än 0.")

    return {name: value / total for name, value in filtered.items()}


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    roll = rng.random()
    cumulative = 0.0

    for name, weight in weights.items():
        cumulative += weight
        if roll <= cumulative:
            return name

    return list(weights.keys())[-1]


class VariantScheduler:
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self._chunk_variant_cache: dict[tuple[int, int], str] = {}
        self._chunk_group_variant_cache: dict[int, str] = {}

    def choose_variant(self, segment: dict, chunk: dict, current_time: float) -> str:
        strategy = self.config.strategy

        if strategy == "fixed":
            return self._choose_fixed(segment, chunk, current_time)

        if strategy == "random_per_chunk":
            return self._choose_random_per_chunk(segment, chunk, current_time)

        if strategy == "random_every_two_chunks":
            return self._choose_random_every_n_chunks(segment, chunk, current_time, group_size=2)

        if strategy == "timeline_mixed_per_chunk":
            return self._choose_timeline_mixed_per_chunk(segment, chunk, current_time)
        
        if strategy == "realistic_even_flow":
            return self._choose_realistic_even_flow(segment, chunk, current_time)

        raise ValueError(f"Okänd strategy: {strategy}")

    def _chunk_key(self, segment: dict, chunk: dict) -> tuple[int, int]:
        return (int(segment["id"]), int(chunk["chunk_id"]))

    def _global_chunk_index(self, segment: dict, chunk: dict) -> int:
        return (int(segment["id"]) * 1000) + int(chunk["chunk_id"])

    def _get_available_variants(self, segment: dict) -> list[str]:
        variants = segment.get("variants", {})
        if not variants:
            raise ValueError(f"Segment {segment.get('id')} saknar 'variants'.")

        available = [name for name in variants.keys() if name in self.config.allow_variants]

        if not available:
            raise ValueError(
                f"Segment {segment.get('id')} har inga tillåtna varianter. "
                f"Tillgängliga i segmentet: {list(variants.keys())}. "
                f"Tillåtna i config: {self.config.allow_variants}"
            )

        return available

    def _ensure_allowed_and_available(self, segment: dict, variant_name: str) -> str:
        available = self._get_available_variants(segment)
        if variant_name not in available:
            raise ValueError(
                f"Variant '{variant_name}' är inte tillgänglig i segment {segment.get('id')}. "
                f"Tillgängliga: {available}"
            )
        return variant_name

    def _choose_from_available_variants(self, segment: dict, preferred_variants: list[str]) -> str:
        available = self._get_available_variants(segment)
        candidates = [name for name in preferred_variants if name in available]

        if not candidates:
            raise ValueError(
                f"Inga kandidater kvar för segment {segment.get('id')}. "
                f"Tillgängliga: {available}"
            )

        return self.rng.choice(candidates)

    def _choose_weighted_for_chunk(self, segment: dict, chunk: dict, weights: dict[str, float]) -> str:
        key = self._chunk_key(segment, chunk)

        if key in self._chunk_variant_cache:
            return self._chunk_variant_cache[key]

        available = self._get_available_variants(segment)
        normalized = normalize_weights(weights, available)
        chosen = weighted_choice(self.rng, normalized)
        self._chunk_variant_cache[key] = chosen
        return chosen

    def _choose_fixed(self, segment: dict, chunk: dict, current_time: float) -> str:
        return self._ensure_allowed_and_available(segment, self.config.fixed_variant)

    def _choose_random_per_chunk(self, segment: dict, chunk: dict, current_time: float) -> str:
        key = self._chunk_key(segment, chunk)

        if key not in self._chunk_variant_cache:
            chosen = self._choose_from_available_variants(
                segment=segment,
                preferred_variants=self.config.allow_variants
            )
            self._chunk_variant_cache[key] = chosen

        return self._chunk_variant_cache[key]

    def _choose_random_every_n_chunks(self, segment: dict, chunk: dict, current_time: float, group_size: int) -> str:
        global_chunk_index = self._global_chunk_index(segment, chunk)
        group_index = global_chunk_index // max(1, group_size)

        if group_index not in self._chunk_group_variant_cache:
            chosen = self._choose_from_available_variants(
                segment=segment,
                preferred_variants=self.config.allow_variants
            )
            self._chunk_group_variant_cache[group_index] = chosen

        return self._chunk_group_variant_cache[group_index]

    def _choose_timeline_mixed_per_chunk(self, segment: dict, chunk: dict, current_time: float) -> str:
        if current_time < 300:
            return self._ensure_allowed_and_available(segment, "original")

        if current_time < 600:
            weights = {
                "original": 0.55,
                "critical": 0.20,
                "hallucinated": 0.05,
                "authoritative_ai": 0.20,
            }
            return self._choose_weighted_for_chunk(segment, chunk, weights)

        if current_time < 1200:
            weights = {
                "original": 0.20,
                "critical": 0.25,
                "hallucinated": 0.10,
                "authoritative_ai": 0.45,
            }
            return self._choose_weighted_for_chunk(segment, chunk, weights)

        weights = {
            "original": 0.10,
            "critical": 0.35,
            "hallucinated": 0.15,
            "authoritative_ai": 0.40,
        }
        return self._choose_weighted_for_chunk(segment, chunk, weights)


    def _choose_realistic_even_flow(self, segment: dict, chunk: dict, current_time: float) -> str:
        key = self._chunk_key(segment, chunk)

        # Om redan valt → returnera (stabilitet)
        if key in self._chunk_variant_cache:
            return self._chunk_variant_cache[key]

        # Initiera minne om det saknas
        if not hasattr(self, "_recent_variants"):
            self._recent_variants = []

        # ===== REGEL 1: Första två chunks = original =====
        if len(self._recent_variants) < 2:
            chosen = "original"
            self._chunk_variant_cache[key] = chosen
            self._recent_variants.append(chosen)
            return chosen

        # ===== REGEL 2: Max 1 avvikelse i rad =====
        last_variant = self._recent_variants[-1]

        if last_variant != "original":
            chosen = "original"
            self._chunk_variant_cache[key] = chosen
            self._recent_variants.append(chosen)
            return chosen

        # ===== REGEL 3: Vikter =====
        weights = {
            "original": 0.64,       # mer kaos → sänk original  eller  mer subtil → höj original
            "critical": 0.12,
            "hallucinated": 0.12,
            "authoritative_ai": 0.12,
        }

        available = self._get_available_variants(segment)
        normalized = normalize_weights(weights, available)

        candidate = weighted_choice(self.rng, normalized)

        # ===== REGEL 4: Hallucinated spacing =====
        if candidate == "hallucinated":
            if len(self._recent_variants) >= 2:
                if self._recent_variants[-2:] == ["hallucinated", "original"]:
                    candidate = "original"

        # ===== REGEL 5: Authoritative spacing =====
        if candidate == "authoritative_ai":
            if len(self._recent_variants) >= 2:
                if self._recent_variants[-2] == "authoritative_ai":
                    candidate = "original"

        chosen = candidate

        self._chunk_variant_cache[key] = chosen
        self._recent_variants.append(chosen)

        # Begränsa minne (så det inte växer okontrollerat)
        if len(self._recent_variants) > 10:
            self._recent_variants.pop(0)

        return chosen 

def preset_only_original() -> SchedulerConfig:
    return SchedulerConfig(strategy="fixed", fixed_variant="original", seed=42)


def preset_only_critical() -> SchedulerConfig:
    return SchedulerConfig(strategy="fixed", fixed_variant="critical", seed=42)


def preset_only_hallucinated() -> SchedulerConfig:
    return SchedulerConfig(strategy="fixed", fixed_variant="hallucinated", seed=42)


def preset_only_authoritative() -> SchedulerConfig:
    return SchedulerConfig(strategy="fixed", fixed_variant="authoritative_ai", seed=42)


def preset_random_per_chunk() -> SchedulerConfig:
    return SchedulerConfig(strategy="random_per_chunk", seed=42)


def preset_random_every_two_chunks() -> SchedulerConfig:
    return SchedulerConfig(strategy="random_every_two_chunks", seed=42, chunk_group_size=2)


def preset_original_then_mixed_per_chunk() -> SchedulerConfig:
    return SchedulerConfig(strategy="timeline_mixed_per_chunk", seed=42)

def preset_realistic_even_flow() -> SchedulerConfig:
    return SchedulerConfig(strategy="realistic_even_flow",seed=42)