"""Compare the stored upcoming card with a freshly fetched one. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class CardBout:
    bout_id: str
    event_id: str
    a_id: str
    b_id: str
    a_name: str
    b_name: str
    weight_class: str | None
    card_order: int | None

    @property
    def fighter_ids(self) -> frozenset[str]:
        return frozenset((self.a_id, self.b_id))

    def name_of(self, fighter_id: str) -> str:
        return self.a_name if fighter_id == self.a_id else self.b_name

    def opponent_of(self, fighter_id: str) -> str:
        return self.b_name if fighter_id == self.a_id else self.a_name


@dataclass(frozen=True)
class CardEvent:
    event_id: str
    name: str
    date: date | None


@dataclass(frozen=True)
class CardChange:
    change_type: str
    summary: str
    event_id: str | None = None
    bout_id: str | None = None
    old_value: dict[str, object] | None = None
    new_value: dict[str, object] | None = None


@dataclass
class DiffResult:
    changes: list[CardChange] = field(default_factory=list)
    cancelled_bout_ids: list[str] = field(default_factory=list)
    replaces: dict[str, str] = field(default_factory=dict)  # new bout_id -> old bout_id
    cancelled_event_ids: list[str] = field(default_factory=list)


def _vs(b: CardBout) -> str:
    return f"{b.a_name} vs. {b.b_name}"


def diff_cards(
    stored_events: list[CardEvent],
    stored_bouts: list[CardBout],
    new_events: list[CardEvent],
    new_bouts: list[CardBout],
    today: date,
) -> DiffResult:
    res = DiffResult()
    old_ev = {e.event_id: e for e in stored_events}
    new_ev = {e.event_id: e for e in new_events}
    ev_name = {**{k: v.name for k, v in old_ev.items()}, **{k: v.name for k, v in new_ev.items()}}

    for eid, e in new_ev.items():
        if eid not in old_ev:
            res.changes.append(
                CardChange(
                    "event_added",
                    f"New event: {e.name} ({e.date})",
                    eid,
                    new_value={"date": str(e.date)},
                )
            )
        elif old_ev[eid].date != e.date:
            res.changes.append(
                CardChange(
                    "event_date_changed",
                    f"{e.name} moved from {old_ev[eid].date} to {e.date}",
                    eid,
                    old_value={"date": str(old_ev[eid].date)},
                    new_value={"date": str(e.date)},
                )
            )
    for eid, e in old_ev.items():
        if eid not in new_ev and (e.date is None or e.date >= today):
            res.cancelled_event_ids.append(eid)
            res.changes.append(
                CardChange("event_cancelled", f"Event no longer listed: {e.name}", eid)
            )

    # Bouts of cancelled events are reported once, as the event; bouts of events that have
    # simply happened (date passed, no longer listed) are not changes at all.
    gone = {eid for eid in old_ev if eid not in new_ev}
    old_b = {b.bout_id: b for b in stored_bouts if b.event_id not in gone}
    new_b = {b.bout_id: b for b in new_bouts}
    old_by_pair = {b.fighter_ids: b for b in old_b.values()}

    added = [b for bid, b in new_b.items() if bid not in old_b]
    removed = {bid: b for bid, b in old_b.items() if bid not in new_b}

    # Same fighter pair under a new bout ID: a move between events, not add + cancel.
    for b in list(added):
        prev = old_by_pair.get(b.fighter_ids)
        if prev and prev.bout_id in removed:
            added.remove(b)
            del removed[prev.bout_id]
            res.cancelled_bout_ids.append(prev.bout_id)
            res.replaces[b.bout_id] = prev.bout_id
            if prev.event_id != b.event_id:
                res.changes.append(
                    CardChange(
                        "bout_moved",
                        f"{_vs(b)} moved from {ev_name.get(prev.event_id)} to {ev_name.get(b.event_id)}",
                        b.event_id,
                        b.bout_id,
                        {"event_id": prev.event_id},
                        {"event_id": b.event_id},
                    )
                )

    # One fighter kept, opponent changed: a replacement.
    for b in list(added):
        for old_id, prev in list(removed.items()):
            shared = b.fighter_ids & prev.fighter_ids
            if prev.event_id == b.event_id and len(shared) == 1:
                kept = next(iter(shared))
                res.changes.append(
                    CardChange(
                        "fighter_replaced",
                        f"{b.name_of(kept)}: opponent changed from {prev.opponent_of(kept)} to {b.opponent_of(kept)}",
                        b.event_id,
                        b.bout_id,
                        {"bout": _vs(prev)},
                        {"bout": _vs(b)},
                    )
                )
                res.replaces[b.bout_id] = old_id
                res.cancelled_bout_ids.append(old_id)
                added.remove(b)
                del removed[old_id]
                break

    for b in added:
        res.changes.append(
            CardChange(
                "bout_added", f"Added: {_vs(b)} ({ev_name.get(b.event_id)})", b.event_id, b.bout_id
            )
        )
    for old_id, prev in removed.items():
        res.cancelled_bout_ids.append(old_id)
        res.changes.append(
            CardChange(
                "bout_cancelled",
                f"Cancelled: {_vs(prev)} ({ev_name.get(prev.event_id)})",
                prev.event_id,
                old_id,
            )
        )

    for bid, b in new_b.items():
        prev = old_b.get(bid)
        if not prev:
            continue
        if prev.weight_class != b.weight_class:
            res.changes.append(
                CardChange(
                    "weight_class_changed",
                    f"{_vs(b)}: {prev.weight_class} -> {b.weight_class}",
                    b.event_id,
                    bid,
                    {"weight_class": prev.weight_class},
                    {"weight_class": b.weight_class},
                )
            )
        if prev.card_order != b.card_order and (prev.card_order == 0 or b.card_order == 0):
            res.changes.append(
                CardChange(
                    "main_event_changed" if b.card_order == 0 else "card_order_changed",
                    f"{_vs(b)} is {'now' if b.card_order == 0 else 'no longer'} the main event",
                    b.event_id,
                    bid,
                    {"card_order": prev.card_order},
                    {"card_order": b.card_order},
                )
            )
    return res
