from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal


DEFAULT_PLATFORM = "google_ads"
DEFAULT_CAMPAIGN_TYPE = "search"
DEFAULT_TEMPLATE_FAMILY = "standard"

_SUPPORTED_TEMPLATE_FAMILIES = frozenset({DEFAULT_TEMPLATE_FAMILY, "greystar"})

RouteOutcome = Literal[
    "matched",
    "no_route_match",
    "ambiguous_route_match",
    "unsupported_route_dimensions",
]


def is_supported_active_route_dimensions(
    *,
    platform: str,
    campaign_type: str,
    template_family: str,
) -> bool:
    return (
        _normalize_dimension(platform, default=DEFAULT_PLATFORM) == DEFAULT_PLATFORM
        and _normalize_dimension(campaign_type, default=DEFAULT_CAMPAIGN_TYPE)
        == DEFAULT_CAMPAIGN_TYPE
        and _normalize_dimension(template_family, default=DEFAULT_TEMPLATE_FAMILY)
        in _SUPPORTED_TEMPLATE_FAMILIES
    )


def _normalize_customer_id(value: str) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _normalize_dimension(value: str, *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or default


@dataclass(frozen=True)
class MccRouteConfig:
    route_id: str
    login_customer_id: str
    customer_ids: tuple[str, ...]
    platform: str = DEFAULT_PLATFORM
    campaign_type: str = DEFAULT_CAMPAIGN_TYPE
    template_family: str = DEFAULT_TEMPLATE_FAMILY
    tab_name: str = ""


@dataclass(frozen=True)
class MccRouteResolution:
    outcome: RouteOutcome
    customer_id: str
    platform: str
    campaign_type: str
    template_family: str
    route_id: str = ""
    login_customer_id: str = ""
    matched_route_ids: tuple[str, ...] = ()
    resolved_tab_name: str = ""


def parse_mcc_routes_json(raw: str) -> tuple[list[MccRouteConfig], list[str]]:
    text = (raw or "").strip()
    if not text:
        return [], []

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"QA_MCC_ROUTES_JSON must be valid JSON: {exc.msg}."]

    if not isinstance(payload, list):
        return [], ["QA_MCC_ROUTES_JSON must be a JSON array."]

    routes: list[MccRouteConfig] = []
    errors: list[str] = []
    seen_route_ids: set[str] = set()
    seen_match_keys: set[tuple[str, str, str, str]] = set()

    for index, item in enumerate(payload):
        item_label = f"QA_MCC_ROUTES_JSON[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object.")
            continue

        route_id = str(item.get("route_id") or "").strip()
        if not route_id:
            errors.append(f"{item_label}.route_id is required.")
            continue

        if route_id in seen_route_ids:
            errors.append(f"{item_label}.route_id '{route_id}' must be unique.")
            continue
        seen_route_ids.add(route_id)

        login_customer_id = _normalize_customer_id(item.get("login_customer_id") or "")
        if not login_customer_id:
            errors.append(f"{item_label}.login_customer_id is required.")
            continue

        raw_customer_ids = item.get("customer_ids")
        if raw_customer_ids is not None and not isinstance(raw_customer_ids, list):
            errors.append(f"{item_label}.customer_ids must be an array.")
            continue

        if raw_customer_ids:
            customer_ids = tuple(
                customer_id
                for customer_id in {
                    _normalize_customer_id(value)
                    for value in raw_customer_ids
                }
                if customer_id
            )
            if not customer_ids:
                errors.append(
                    f"{item_label}.customer_ids must include at least one valid customer_id."
                )
                continue
        else:
            customer_ids = ()

        platform = _normalize_dimension(
            item.get("platform") or "", default=DEFAULT_PLATFORM
        )
        campaign_type = _normalize_dimension(
            item.get("campaign_type") or "", default=DEFAULT_CAMPAIGN_TYPE
        )
        template_family = _normalize_dimension(
            item.get("template_family") or "", default=DEFAULT_TEMPLATE_FAMILY
        )

        if customer_ids:
            for customer_id in customer_ids:
                match_key = (customer_id, platform, campaign_type, template_family)
                if match_key in seen_match_keys:
                    errors.append(
                        f"{item_label} duplicates another route match for customer_id={customer_id}, "
                        f"platform={platform}, campaign_type={campaign_type}, "
                        f"template_family={template_family}."
                    )
                    break
                seen_match_keys.add(match_key)
            else:
                routes.append(
                    MccRouteConfig(
                        route_id=route_id,
                        login_customer_id=login_customer_id,
                        customer_ids=customer_ids,
                        platform=platform,
                        campaign_type=campaign_type,
                        template_family=template_family,
                        tab_name=(item.get("tab_name") or "").strip(),
                    )
                )
        else:
            catchall_key = ("", platform, campaign_type, template_family)
            if catchall_key in seen_match_keys:
                errors.append(
                    f"{item_label} duplicates another catch-all route for "
                    f"platform={platform}, campaign_type={campaign_type}, "
                    f"template_family={template_family}."
                )
            else:
                seen_match_keys.add(catchall_key)
                routes.append(
                    MccRouteConfig(
                        route_id=route_id,
                        login_customer_id=login_customer_id,
                        customer_ids=(),
                        platform=platform,
                        campaign_type=campaign_type,
                        template_family=template_family,
                        tab_name=(item.get("tab_name") or "").strip(),
                    )
                )

    routes.sort(key=lambda route: route.route_id)
    return routes, errors


def resolve_mcc_route(
    *,
    routes: list[MccRouteConfig],
    customer_id: str,
    platform: str = "",
    campaign_type: str = "",
    template_family: str = "",
) -> MccRouteResolution:
    normalized_customer_id = _normalize_customer_id(customer_id)
    normalized_platform = _normalize_dimension(platform, default=DEFAULT_PLATFORM)
    normalized_campaign_type = _normalize_dimension(
        campaign_type, default=DEFAULT_CAMPAIGN_TYPE
    )
    normalized_template_family = _normalize_dimension(
        template_family, default=DEFAULT_TEMPLATE_FAMILY
    )

    specific_routes = [
        route for route in routes
        if route.customer_ids and normalized_customer_id in route.customer_ids
    ]

    matches = [
        route
        for route in specific_routes
        if route.platform == normalized_platform
        and route.campaign_type == normalized_campaign_type
        and route.template_family == normalized_template_family
    ]

    if len(matches) == 1:
        return _resolved_match(matches[0], normalized_customer_id, normalized_platform, normalized_campaign_type, normalized_template_family)

    if len(matches) > 1:
        return MccRouteResolution(
            outcome="ambiguous_route_match",
            customer_id=normalized_customer_id,
            platform=normalized_platform,
            campaign_type=normalized_campaign_type,
            template_family=normalized_template_family,
            matched_route_ids=tuple(sorted(route.route_id for route in matches)),
        )

    # No specific match — try catch-all routes (empty customer_ids).
    catchall_routes = [route for route in routes if not route.customer_ids]
    catchall_matches = [
        route
        for route in catchall_routes
        if route.platform == normalized_platform
        and route.campaign_type == normalized_campaign_type
        and route.template_family == normalized_template_family
    ]

    if len(catchall_matches) == 1:
        return _resolved_match(catchall_matches[0], normalized_customer_id, normalized_platform, normalized_campaign_type, normalized_template_family)

    if len(catchall_matches) > 1:
        return MccRouteResolution(
            outcome="ambiguous_route_match",
            customer_id=normalized_customer_id,
            platform=normalized_platform,
            campaign_type=normalized_campaign_type,
            template_family=normalized_template_family,
            matched_route_ids=tuple(sorted(route.route_id for route in catchall_matches)),
        )

    # No match at all — check whether any candidate route had unsupported dimensions.
    all_candidate_routes = specific_routes + catchall_routes
    unsupported_route_ids = tuple(
        sorted(
            route.route_id
            for route in all_candidate_routes
            if not is_supported_active_route_dimensions(
                platform=route.platform,
                campaign_type=route.campaign_type,
                template_family=route.template_family,
            )
        )
    )
    if unsupported_route_ids:
        return MccRouteResolution(
            outcome="unsupported_route_dimensions",
            customer_id=normalized_customer_id,
            platform=normalized_platform,
            campaign_type=normalized_campaign_type,
            template_family=normalized_template_family,
            matched_route_ids=unsupported_route_ids,
        )
    return MccRouteResolution(
        outcome="no_route_match",
        customer_id=normalized_customer_id,
        platform=normalized_platform,
        campaign_type=normalized_campaign_type,
        template_family=normalized_template_family,
    )


def _resolved_match(
    match: MccRouteConfig,
    normalized_customer_id: str,
    normalized_platform: str,
    normalized_campaign_type: str,
    normalized_template_family: str,
) -> MccRouteResolution:
    if not is_supported_active_route_dimensions(
        platform=match.platform,
        campaign_type=match.campaign_type,
        template_family=match.template_family,
    ):
        return MccRouteResolution(
            outcome="unsupported_route_dimensions",
            customer_id=normalized_customer_id,
            platform=normalized_platform,
            campaign_type=normalized_campaign_type,
            template_family=normalized_template_family,
            matched_route_ids=(match.route_id,),
        )
    return MccRouteResolution(
        outcome="matched",
        customer_id=normalized_customer_id,
        platform=normalized_platform,
        campaign_type=normalized_campaign_type,
        template_family=normalized_template_family,
        route_id=match.route_id,
        login_customer_id=match.login_customer_id,
        matched_route_ids=(match.route_id,),
        resolved_tab_name=match.tab_name,
    )
