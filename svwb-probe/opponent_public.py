"""Read only game UI cards explicitly marked as public opponent hand cards.

The game sets IsOpenOpponentHand only when creating an opponent card with
isDummy=False. Hidden cards are rejected before reading their card IDs.
No card-effect inference or opponent deck/hand scanning is used.
"""
import struct

import readonly_reader as memory

_FIELDS = {}


def class_name(handle, address):
    klass = memory.read_ptr(handle, address) if address else None
    name = memory.read_ptr(handle, klass + 16) if klass else None
    raw = memory.read_mem(handle, name, 96) if name else None
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace") if raw else ""


def fields_for(handle, klass):
    key = (handle, klass)
    if key in _FIELDS:
        return _FIELDS[key]
    fields, visited = {}, set()
    current = klass
    for _ in range(12):
        if not current or current in visited:
            break
        visited.add(current)
        table = memory.read_ptr(handle, current + 128)
        if table:
            for index in range(512):
                raw = memory.read_mem(handle, table + index * 32, 32)
                if not raw:
                    break
                name_ptr, _, owner, offset, _ = struct.unpack("<QQQii", raw)
                if owner != current:
                    break
                if not 16 <= offset < 65536:
                    continue
                name = memory.read_mem(handle, name_ptr, 128) if name_ptr else None
                if name:
                    name = name.split(b"\0", 1)[0].decode("ascii", errors="replace")
                    fields.setdefault(name, offset)
        current = memory.read_ptr(handle, current + 88)
    if fields:
        _FIELDS[key] = fields
    return fields


def member(handle, address, name):
    klass = memory.read_ptr(handle, address) if address else None
    offset = fields_for(handle, klass).get(name) if klass else None
    return address + offset if offset is not None else None


def read_public_hand(handle, view):
    """Return current public instances, [] for none, None for an unstable UI."""
    if not handle or class_name(handle, view) != "BattleView":
        return None
    owner_slot = member(handle, view, "_oppoPlayer")
    owner = memory.read_ptr(handle, owner_slot) if owner_slot else None
    if not owner or class_name(handle, owner) != "BattlePlayerViewOppo":
        return None
    list_slot = member(handle, owner, "_handCards")
    collection = memory.read_ptr(handle, list_slot) if list_slot else None
    if not collection:
        return None
    head = memory.read_mem(handle, collection, 32)
    if not head:
        return None
    array, count = struct.unpack_from("<Qi", head, 16)
    capacity = memory.read_i32(handle, array + 24) if array else None
    if not 0 <= count <= 10 or capacity is None or not count <= capacity <= 64:
        return None
    slots = memory.read_mem(handle, array + 32, count * 8) if count else b""
    if slots is None:
        return None
    instances, checks = {}, []
    for (card,) in struct.iter_unpack("<Q", slots):
        if not card or class_name(handle, card) != "HandCardView":
            return None
        public_slot = member(handle, card, "<IsOpenOpponentHand>k__BackingField")
        flag = memory.read_mem(handle, public_slot, 1) if public_slot else None
        if flag not in (b"\0", b"\1"):
            return None
        checks.append((public_slot, flag))
        if flag != b"\1":
            # Deliberately do not read an unknown opponent card's ID/UID.
            continue
        uid_slot = member(handle, card, "<UniqueId>k__BackingField")
        id_slot = member(handle, card, "<CardId>k__BackingField")
        uid = memory.read_i32(handle, uid_slot) if uid_slot else None
        cid = memory.read_i32(handle, id_slot) if id_slot else None
        if uid is None or uid <= 0 or cid is None or not 10000000 <= cid <= 99999999:
            return None
        if uid in instances and instances[uid] != cid:
            return None
        instances[uid] = cid
        checks.extend(((uid_slot, struct.pack("<i", uid)),
                       (id_slot, struct.pack("<i", cid))))
    if (head != memory.read_mem(handle, collection, 32)
            or (count and slots != memory.read_mem(handle, array + 32, count * 8))
            or owner != memory.read_ptr(handle, owner_slot)
            or collection != memory.read_ptr(handle, list_slot)):
        return None
    if any(expected != memory.read_mem(handle, address, len(expected))
           for address, expected in checks):
        return None
    return [{"uid": uid, "cardId": cid} for uid, cid in sorted(instances.items())]


def read_snapshot(handle, view):
    try:
        cards = read_public_hand(handle, view)
    except (OSError, ValueError, TypeError, struct.error):
        cards = None
    return {"opponentPublicHand": cards or [],
            "opponentPublicHandReady": cards is not None}


def public_counts(snapshot):
    """Validate public instances before grouping; one UID is counted once."""
    if not snapshot.get("opponentPublicHandReady"):
        return {}
    instances = {}
    for card in snapshot.get("opponentPublicHand") or []:
        try:
            uid, cid = int(card["uid"]), int(card["cardId"])
        except (KeyError, ValueError, TypeError):
            continue
        if uid > 0 and 10000000 <= cid <= 99999999:
            instances[uid] = cid
    from collections import Counter
    return dict(Counter(instances.values()))
