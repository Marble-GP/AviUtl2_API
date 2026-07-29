"""Catalog-verified high-level helpers for common native effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from aviutl2_api.models import AnimatedValue, StaticValue

from .commands import ItemUpdate
from .snapshot import SnapshotObject

if TYPE_CHECKING:
    from .client import LiveClient

EffectValue = str | int | float | bool | StaticValue | AnimatedValue
EffectSemantic = Literal[
    "transition",
    "mask",
    "crop",
    "chroma_key",
    "volume",
    "pan",
    "fade",
    "ducking",
]

_CANDIDATES: dict[EffectSemantic, tuple[str, ...]] = {
    "transition": ("シーンチェンジ", "フェード", "ワイプ"),
    "mask": ("マスク",),
    "crop": ("クリッピング", "斜めクリッピング"),
    "chroma_key": ("クロマキー", "カラーキー"),
    "volume": ("音量調整", "音量フェード"),
    "pan": ("音声定位", "パン"),
    "fade": ("フェード", "音量フェード"),
    "ducking": ("音量調整", "コンプレッサー"),
}


@dataclass(frozen=True, slots=True)
class EffectApplication:
    semantic: EffectSemantic
    effect_name: str
    selector: str | None
    added: bool
    result: dict[str, Any]


def _all_catalog_effects(client: LiveClient) -> dict[str, set[str]]:
    effects: dict[str, set[str]] = {}
    start = 0
    while True:
        page = client.get_effect_catalog(start=start, count=128)
        for effect in page.effects:
            effects[effect.name] = {
                item.name for item in effect.items
            }
        if page.next_start is None:
            return effects
        start = page.next_start


def apply_common_effect(
    client: LiveClient,
    obj: SnapshotObject,
    semantic: EffectSemantic,
    values: Mapping[str, EffectValue],
    *,
    effect_name: str | None = None,
    timeout: float | None = None,
) -> EffectApplication:
    """Apply only after the live catalog and object schema agree exactly.

    No localized item name or raw value is guessed. Callers supply the item
    names shown by ``effect.catalog``/``object.inspect``.
    """
    if not values:
        raise ValueError("at least one effect item value is required")
    catalog = _all_catalog_effects(client)
    candidates = (
        (effect_name,)
        if effect_name is not None
        else _CANDIDATES[semantic]
    )
    available = [name for name in candidates if name in catalog]
    if len(available) != 1:
        raise ValueError(
            f"{semantic} must resolve to exactly one live catalog effect; "
            f"available candidates: {available!r}"
        )
    selected = available[0]
    requested_items = set(values)
    if not requested_items.issubset(catalog[selected]):
        missing = sorted(requested_items - catalog[selected])
        raise ValueError(
            "requested items are absent from the live effect catalog: "
            + ", ".join(missing)
        )

    inspection = client.inspect_object(obj, timeout=timeout)
    existing = [
        effect
        for effect in inspection.effects
        if effect.name == selected
        and requested_items.issubset(
            {item.name for item in effect.items}
        )
    ]
    if len(existing) > 1:
        raise ValueError(
            "effect_name is ambiguous; use object.inspect selectors directly"
        )
    updates = tuple(
        ItemUpdate(selected, item, value)
        for item, value in values.items()
    )
    if existing:
        selector = existing[0].selector
        result = client.set_items(
            obj,
            tuple(
                ItemUpdate(selector, update.item, update.value)
                for update in updates
            ),
            timeout=timeout,
        )
        return EffectApplication(
            semantic,
            selected,
            selector,
            False,
            result,
        )
    raw_items = {
        update.item: update.to_wire()["value"]
        for update in updates
    }
    result = client.add_effect(
        obj,
        selected,
        items=raw_items,
        timeout=timeout,
    )
    return EffectApplication(
        semantic,
        selected,
        None,
        True,
        result,
    )


__all__ = [
    "EffectApplication",
    "EffectSemantic",
    "EffectValue",
    "apply_common_effect",
]
