from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Shape(Enum):
    A = "A"  # Single campaign (legacy)
    B = "B"  # Multi-campaign
    C = "C"  # Single campaign + single ad_group
    D = "D"  # Single campaign + multi ad_groups
    E = "E"  # Ad-only (auto-resolve parents)
    F = "F"  # Single campaign + multi ads


_SOFT_CAP = 100


def _parse_ids(raw: str) -> tuple[str, ...]:
    """Split comma-separated string, trim, drop empty, dedup preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return tuple(result)


@dataclass(frozen=True)
class EntityFilter:
    campaign_ids: tuple[str, ...]
    campaign_names: tuple[str, ...]
    ad_group_ids: tuple[str, ...]
    ad_ids: tuple[str, ...]

    @classmethod
    def from_audit_dict(cls, d: dict) -> EntityFilter:
        """Reconstruct from a dict produced by to_audit_dict(). Skips soft-cap."""
        return cls(
            campaign_ids=tuple(d.get("campaign_ids") or []),
            campaign_names=tuple(d.get("campaign_names") or []),
            ad_group_ids=tuple(d.get("ad_group_ids") or []),
            ad_ids=tuple(d.get("ad_ids") or []),
        )

    @classmethod
    def from_raw_strings(
        cls,
        campaign_id: str,
        campaign_name: str,
        ad_group_id: str,
        ad_id: str,
    ) -> EntityFilter:
        campaign_ids = _parse_ids(campaign_id)
        campaign_names = _parse_ids(campaign_name)
        ad_group_ids = _parse_ids(ad_group_id)
        ad_ids = _parse_ids(ad_id)

        total = len(campaign_ids) + len(ad_group_ids) + len(ad_ids)
        if total > _SOFT_CAP:
            raise ValueError(
                f"Total entity ids ({total}) exceeds soft cap of {_SOFT_CAP}"
            )

        ef = cls(
            campaign_ids=campaign_ids,
            campaign_names=campaign_names,
            ad_group_ids=ad_group_ids,
            ad_ids=ad_ids,
        )
        ef.validate_positional_alignment()
        return ef

    def validate_shape(self) -> Shape:
        c = len(self.campaign_ids)
        ag = len(self.ad_group_ids)
        ad = len(self.ad_ids)

        # Not allowed: ad_group without campaign
        if ag > 0 and c == 0:
            raise ValueError(
                "invalid shape: ad_group_id without campaign_id is ambiguous"
            )

        # Not allowed: multi-campaign + multi-ad_group
        if c > 1 and ag > 1:
            raise ValueError(
                "invalid shape: multi-campaign + multi-ad_group not allowed"
            )

        # Not allowed: empty filter
        if c == 0 and ag == 0 and ad == 0:
            raise ValueError("invalid shape: empty filter (no ids provided)")

        # Shape E: ad-only
        if c == 0 and ag == 0 and ad > 0:
            return Shape.E

        # Shape F: single campaign + multi ads (no ad_groups)
        if c == 1 and ag == 0 and ad >= 2:
            return Shape.F

        # Shape D: single campaign + multi ad_groups
        if c == 1 and ag >= 2 and ad == 0:
            return Shape.D

        # Shape C: single campaign + single ad_group
        if c == 1 and ag == 1 and ad == 0:
            return Shape.C

        # Shape B: multi-campaign (no ad_groups, no ads)
        if c >= 2 and ag == 0 and ad == 0:
            return Shape.B

        # Shape A: single campaign (no ad_groups, no ads)
        if c == 1 and ag == 0 and ad <= 1:
            return Shape.A

        raise ValueError(
            f"invalid shape: unrecognized combination "
            f"(campaigns={c}, ad_groups={ag}, ads={ad})"
        )

    def validate_positional_alignment(self) -> None:
        if (
            self.campaign_names
            and self.campaign_ids
            and len(self.campaign_names) != len(self.campaign_ids)
        ):
            raise ValueError(
                f"Positional alignment error: {len(self.campaign_names)} campaign_names "
                f"vs {len(self.campaign_ids)} campaign_ids"
            )

    def to_audit_dict(self) -> dict[str, list[str]]:
        return {
            "campaign_ids": list(self.campaign_ids),
            "campaign_names": list(self.campaign_names),
            "ad_group_ids": list(self.ad_group_ids),
            "ad_ids": list(self.ad_ids),
        }

    def is_legacy_single_campaign(self) -> bool:
        return (
            len(self.campaign_ids) == 1
            and len(self.ad_group_ids) == 0
            and len(self.ad_ids) == 0
        )
