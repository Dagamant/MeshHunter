# MeshCore Node Ingest API

Lets an external script (e.g. a meshcore packet monitor) push repeater, chat,
and room node sightings straight into the dashboard's database, without
going through the CSV import flow.

## Setup

1. Set a secret key on the server host, in `.env` next to `docker-compose.yml`:
   ```
   INGEST_API_KEY=<a long random string>
   ```
   Generate one with `openssl rand -hex 24` if you don't already have one.
2. Restart the stack so the server picks it up: `docker compose up -d --build`.

If `INGEST_API_KEY` isn't set, the endpoint is disabled and returns `503`.

## Endpoint

```
POST /api/mesh_nodes/ingest
```

| Header | Value |
|---|---|
| `X-API-Key` | your `INGEST_API_KEY` |
| `Content-Type` | `application/json` |

**Body:** a single JSON object, or a JSON array of objects to send several
nodes in one request.

| Field | Required | Notes |
|---|---|---|
| `public_key` | yes | unique ID for the node; used as the upsert key |
| `node_type` | yes | `"REPEATER"`, `"CHAT"`, or `"ROOM"` |
| `node_id` | no | short node identifier |
| `name` | no | display name |
| `lat` / `lon` | no | last known position |
| `rssi` | no | signal strength of the most recent packet |
| `first_seen` | no | timestamp first observed |
| `last_heard` | no | timestamp of this sighting; defaults to server "now" if omitted |
| `network` | no | defaults to `"meshcore"` |
| `type` | no | defaults to `"MESHCORE"` |

**Partial updates are safe.** If a ping only carries `public_key` /
`node_type` / `rssi` (a plain signal reading, no name or fix), the
previously known name, position, and first-seen timestamp are preserved
rather than overwritten with blanks.

### Response

```json
{"ok": true, "ingested": 2, "errors": []}
```

`errors` lists any records in the batch that were skipped (e.g. missing
`public_key` or an invalid `node_type`) — the rest of the batch still gets
ingested. `401` means the API key is missing or wrong; `503` means ingest
isn't enabled on the server.

## Examples

**Single node (curl):**
```bash
curl -X POST http://localhost:8765/api/mesh_nodes/ingest \
  -H "X-API-Key: $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "public_key": "247df3d929289893f4b07507e6cb24870b1a581b36226a08d86791f24f5c1b3a",
    "node_id": "247df3d929289893",
    "node_type": "REPEATER",
    "name": "Porcupine Proof",
    "lat": 33.9247,
    "lon": -81.51595,
    "rssi": -72,
    "network": "meshcore",
    "type": "MESHCORE"
  }'
```

**Batch (Python):**
```python
import os
import requests

resp = requests.post(
    "http://localhost:8765/api/mesh_nodes/ingest",
    headers={"X-API-Key": os.environ["INGEST_API_KEY"]},
    json=[
        {"public_key": "...", "node_type": "CHAT", "name": "Aaron T", "rssi": -60},
        {"public_key": "...", "node_type": "ROOM", "rssi": -80},
    ],
)
print(resp.json())  # {"ok": true, "ingested": 2, "errors": []}
```

## Related read endpoints

- `GET /api/mesh_nodes` — all repeater/chat/room nodes currently on the map.
- `POST /api/mesh_nodes/import` — re-reads `repeaters.csv`, `chat_nodes.csv`,
  and `room_nodes.csv` from disk and upserts them (same as at server startup).
