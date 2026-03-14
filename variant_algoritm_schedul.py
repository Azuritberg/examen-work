"""
variant-algoritm-schedul.py

Styrlogik för att välja vilken textvariant som ska användas i artefakten.

Tanken är att denna fil ska vara helt separat från:
- radio-to-receipt-block.py  -> runtime / ljud / synk / utskrift
- pdf_printer.py             -> PDF / skrivare

Den här filen ska bara avgöra:
vilken variant ska användas för ett givet segment vid en given tid?

Du kan testa flera strategier, till exempel:
- bara original
- original först, sedan blandning
- hallucinationer sällan
- authoritative ofta
- critical i vissa delar
- random per segment
- random per tidsfönster

Användningsexempel:

    from variant_algoritm_schedul import VariantScheduler, SchedulerConfig

    config = SchedulerConfig(
        strategy="timeline_mixed",
        seed=42
    )

    scheduler = VariantScheduler(config)

    variant_name = scheduler.choose_variant(
        segment=segment,
        current_time=123.4
    )

"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


# =========================
# KONFIG DATAMODELLER
# =========================
@dataclass
class SchedulerConfig:
    """
    Grundkonfiguration för schemaläggaren.

    strategy:
        - "fixed"
        - "random_per_segment"
        - "random_per_time_window"
        - "timeline_mixed"
        - "weighted_global"

    fixed_variant:
        används när strategy == "fixed"

    seed:
        gör slumpningen reproducerbar om du vill kunna upprepa samma körning

    time_window_seconds:
        används när strategy == "random_per_time_window"
        Exempel: 60 = nytt variantval varje minut

    allow_variants:
        vilka varianter som får användas
        bra om du ibland vill utesluta någon variant

    weights_global:
        används när strategy == "weighted_global"
        eller som fallback i andra strategier
    """
    strategy: str = "fixed"
    fixed_variant: str = "original"
    seed: Optional[int] = None
    time_window_seconds: int = 60

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


# =========================
# HJÄLPFUNKTIONER
# =========================
def normalize_weights(weights: dict[str, float], allowed_variants: list[str]) -> dict[str, float]:
    """
    Tar bara med tillåtna varianter och normaliserar vikterna till summa 1.0.
    """
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
    """
    Väljer en nyckel utifrån vikt.
    """
    roll = rng.random()
    cumulative = 0.0

    for name, weight in weights.items():
        cumulative += weight
        if roll <= cumulative:
            return name

    # fallback p.g.a. flyttalsavrundning
    return list(weights.keys())[-1]


# =========================
# SCHEMALÄGGARE
# =========================
class VariantScheduler:
    """
    Huvudklass för variantval.

    Viktigt:
    - segment förväntas vara ett dict från din JSON
    - segment["id"] används för caching i vissa strategier
    - current_time är aktuell ljudtid i sekunder
    """

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.rng = random.Random(config.seed)

        # Cache för stabila val per segment
        self._segment_variant_cache: dict[int, str] = {}

        # Cache för val per tidsfönster
        self._window_variant_cache: dict[int, str] = {}

    # =========================
    # PUBLIK METOD
    # =========================
    def choose_variant(self, segment: dict, current_time: float) -> str:
        """
        Välj variant för ett segment vid en viss tidpunkt.
        """
        strategy = self.config.strategy

        if strategy == "fixed":
            return self._choose_fixed(segment, current_time)

        if strategy == "random_per_segment":
            return self._choose_random_per_segment(segment, current_time)

        if strategy == "random_per_time_window":
            return self._choose_random_per_time_window(segment, current_time)

        if strategy == "timeline_mixed":
            return self._choose_timeline_mixed(segment, current_time)

        if strategy == "weighted_global":
            return self._choose_weighted_global(segment, current_time)

        raise ValueError(f"Okänd strategy: {strategy}")

    # =========================
    # STRATEGIER
    # =========================
    def _choose_fixed(self, segment: dict, current_time: float) -> str:
        """
        Bara en variant hela tiden.
        Bra för test.
        """
        return self._ensure_allowed_and_available(segment, self.config.fixed_variant)

    def _choose_random_per_segment(self, segment: dict, current_time: float) -> str:
        """
        Varje segment får en slumpad variant,
        men samma segment behåller sitt val under hela körningen.
        """
        segment_id = int(segment["id"])

        if segment_id not in self._segment_variant_cache:
            chosen = self._choose_from_available_variants(
                segment=segment,
                preferred_variants=self.config.allow_variants
            )
            self._segment_variant_cache[segment_id] = chosen

        return self._segment_variant_cache[segment_id]

    def _choose_random_per_time_window(self, segment: dict, current_time: float) -> str:
        """
        Alla segment inom samma tidsfönster delar variant.
        Exempel: nytt läge varje 60 sekunder.
        """
        window_size = max(1, int(self.config.time_window_seconds))
        window_index = int(current_time // window_size)

        if window_index not in self._window_variant_cache:
            chosen = self._choose_from_available_variants(
                segment=segment,
                preferred_variants=self.config.allow_variants
            )
            self._window_variant_cache[window_index] = chosen

        return self._window_variant_cache[window_index]

    def _choose_weighted_global(self, segment: dict, current_time: float) -> str:
        """
        Slump med globala vikter varje gång choose_variant anropas.
        Om du använder denna i runtime bör du helst cacha valet per segment
        i huvudprogrammet om du vill att segmentet ska vara stabilt.
        """
        available = self._get_available_variants(segment)
        weights = normalize_weights(self.config.weights_global, available)
        return weighted_choice(self.rng, weights)

    def _choose_timeline_mixed(self, segment: dict, current_time: float) -> str:
        """
        En kuraterad strategi som ofta passar bra för artefakten.

        Förslag:
        - 0–5 min: bara original
        - 5–10 min: original först, men med lite critical/authoritative
        - 10–20 min: blandning, hallucinationer sällan
        - 20–30 min: mer destabiliserat, authoritative och critical tydligare

        Du kan ändra logiken här hur mycket du vill.
        """

        # 0–5 min = bara original
        if current_time < 300:
            return self._ensure_allowed_and_available(segment, "original")

        # 5–10 min = original först, sedan blandning
        if current_time < 600:
            weights = {
                "original": 0.50,
                "critical": 0.20,
                "hallucinated": 0.10,
                "authoritative_ai": 0.20,
            }
            return self._choose_weighted_for_segment(segment, weights)

        # 10–20 min = hallucinationer sällan, authoritative ofta
        if current_time < 1200:
            weights = {
                "original": 0.20,
                "critical": 0.25,
                "hallucinated": 0.10,
                "authoritative_ai": 0.45,
            }
            return self._choose_weighted_for_segment(segment, weights)

        # 20–30 min = critical i vissa delar, authoritative ofta, original mer sällan
        weights = {
            "original": 0.10,
            "critical": 0.35,
            "hallucinated": 0.15,
            "authoritative_ai": 0.40,
        }
        return self._choose_weighted_for_segment(segment, weights)

    # =========================
    # INTERNA HJÄLPARE
    # =========================
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

    def _choose_weighted_for_segment(self, segment: dict, weights: dict[str, float]) -> str:
        """
        Viktad slumpning men stabil per segment inom körningen.
        Bra för att ett segment inte ska byta variant mitt i.
        """
        segment_id = int(segment["id"])

        if segment_id in self._segment_variant_cache:
            return self._segment_variant_cache[segment_id]

        available = self._get_available_variants(segment)
        normalized = normalize_weights(weights, available)
        chosen = weighted_choice(self.rng, normalized)

        self._segment_variant_cache[segment_id] = chosen
        return chosen


# =========================
# FÄRDIGA TESTPRESETS
# =========================
def preset_only_original() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="fixed",
        fixed_variant="original",
        seed=42
    )


def preset_only_critical() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="fixed",
        fixed_variant="critical",
        seed=42
    )


def preset_only_hallucinated() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="fixed",
        fixed_variant="hallucinated",
        seed=42
    )


def preset_only_authoritative() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="fixed",
        fixed_variant="authoritative_ai",
        seed=42
    )


def preset_random_per_segment() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="random_per_segment",
        seed=42
    )


def preset_random_per_minute() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="random_per_time_window",
        time_window_seconds=60,
        seed=42
    )


def preset_original_then_mixed() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="timeline_mixed",
        seed=42
    )


def preset_authoritative_often() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="weighted_global",
        seed=42,
        weights_global={
            "original": 0.15,
            "critical": 0.20,
            "hallucinated": 0.10,
            "authoritative_ai": 0.55,
        }
    )


def preset_hallucinations_rare() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="weighted_global",
        seed=42,
        weights_global={
            "original": 0.30,
            "critical": 0.30,
            "hallucinated": 0.05,
            "authoritative_ai": 0.35,
        }
    )


def preset_critical_in_later_parts() -> SchedulerConfig:
    return SchedulerConfig(
        strategy="timeline_mixed",
        seed=99
    )


# =========================
# ENKEL DEMO / TEST I TERMINAL
# =========================
if __name__ == "__main__":
    # Minimal test utan hela runtime-programmet.
    fake_segment = {
        "id": 7,
        "variants": {
            "original": {},
            "critical": {},
            "hallucinated": {},
            "authoritative_ai": {},
        }
    }

    presets = {
        "only_original": preset_only_original(),
        "random_per_segment": preset_random_per_segment(),
        "random_per_minute": preset_random_per_minute(),
        "original_then_mixed": preset_original_then_mixed(),
        "authoritative_often": preset_authoritative_often(),
        "hallucinations_rare": preset_hallucinations_rare(),
    }

    print("Demo av variantval:\n")

    for name, config in presets.items():
        scheduler = VariantScheduler(config)
        print(f"PRESET: {name}")

        for t in [10, 120, 320, 700, 1300, 1700]:
            chosen = scheduler.choose_variant(fake_segment, current_time=t)
            print(f"  tid {t:>4}s -> {chosen}")

        print()
