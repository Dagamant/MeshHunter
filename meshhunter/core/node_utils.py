"""Small pure helpers for working with MeshCore contact dicts / known-node rows."""

from .constants import NODE_ID_HEX_LEN


def has_coords(contact):
    """True if a contact's advertised lat/lon aren't both the GPS-less 0,0 default."""
    return bool(contact.get("adv_lat")) or bool(contact.get("adv_lon"))


def derive_node_id(public_key):
    """WDGWars node_id: first 8 bytes (16 lowercase hex chars) of the node's public key."""
    return public_key[:NODE_ID_HEX_LEN].lower()


def node_names(known_nodes):
    return sorted(row.get("name") or row["node_id"] for row in known_nodes.values())
