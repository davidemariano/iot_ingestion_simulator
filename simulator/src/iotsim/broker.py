"""Broker MQTT emulato in-process (sostituisce Mosquitto nel simulatore).

Riproduce i comportamenti che contano per i KPI del §14:
- credenziali per gateway create e revocate a runtime (plugin *dynamic security*);
- ACL: un gateway pubblica solo su ``agrivalor/{proprio gateway_id}/#``;
- sessione persistente dell'ingestion con QoS 1: i messaggi consegnati restano
  *in-flight* finché non arriva il PUBACK, e vengono riconsegnati (flag DUP)
  se la sessione si riapre senza ack (riavvio dell'ingestion, scenario S3);
- ``max_queued_messages``: oltre il limite il broker scarta (perdita misurabile);
- limite sulla dimensione del payload (§12).
"""

from __future__ import annotations

import secrets
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

TOPIC_PREFIX = "agrivalor"


def telemetry_topic(gateway_id: str, device_uid: str) -> str:
    return f"{TOPIC_PREFIX}/{gateway_id}/{device_uid}/telemetry"


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


@dataclass(slots=True)
class BrokerMessage:
    mid: int
    topic: str
    payload: bytes
    sent_at_real: float  # epoch reale della PUBLISH (per la latenza)
    dup: bool = False


class Rifiuto(StrEnum):
    NON_AUTORIZZATO = "non_autorizzato"
    ACL_NEGATA = "acl_negata"
    TROPPO_GRANDE = "troppo_grande"
    CODA_PIENA = "coda_piena"


class EmbeddedBroker:
    def __init__(self, max_queued_messages: int = 100_000, max_payload_bytes: int = 4096) -> None:
        self.max_queued = max_queued_messages
        self.max_payload = max_payload_bytes
        self._users: dict[str, tuple[str, str]] = {}  # username -> (password, gateway_id)
        self._queue: deque[BrokerMessage] = deque()
        self._inflight: dict[int, BrokerMessage] = {}
        self._next_mid = 1
        self.rifiutati: dict[str, int] = {r.value: 0 for r in Rifiuto}
        self.pubblicati = 0
        self.riconsegnati = 0

    # --- dynamic security -------------------------------------------------
    def create_credentials(self, gateway_id: str) -> Credentials:
        cred = Credentials(username=gateway_id, password=secrets.token_urlsafe(18))
        self._users[cred.username] = (cred.password, gateway_id)
        return cred

    def revoke(self, gateway_id: str) -> None:
        self._users.pop(gateway_id, None)

    def is_enabled(self, gateway_id: str) -> bool:
        return gateway_id in self._users

    # --- lato client (stazioni) --------------------------------------------
    def publish(self, cred: Credentials, topic: str, payload: bytes, sent_at_real: float) -> Rifiuto | None:
        entry = self._users.get(cred.username)
        if entry is None or entry[0] != cred.password:
            self.rifiutati[Rifiuto.NON_AUTORIZZATO] += 1
            return Rifiuto.NON_AUTORIZZATO
        if not topic.startswith(f"{TOPIC_PREFIX}/{entry[1]}/"):
            self.rifiutati[Rifiuto.ACL_NEGATA] += 1
            return Rifiuto.ACL_NEGATA
        if len(payload) > self.max_payload:
            self.rifiutati[Rifiuto.TROPPO_GRANDE] += 1
            return Rifiuto.TROPPO_GRANDE
        if len(self._queue) >= self.max_queued:
            self.rifiutati[Rifiuto.CODA_PIENA] += 1
            return Rifiuto.CODA_PIENA
        self._queue.append(BrokerMessage(self._next_mid, topic, payload, sent_at_real))
        self._next_mid += 1
        self.pubblicati += 1
        return None

    # --- lato sottoscrittore (ingestion, sessione persistente) --------------
    def fetch(self, max_n: int) -> list[BrokerMessage]:
        out: list[BrokerMessage] = []
        while self._queue and len(out) < max_n:
            m = self._queue.popleft()
            self._inflight[m.mid] = m
            out.append(m)
        return out

    def puback(self, mids: list[int]) -> None:
        for mid in mids:
            self._inflight.pop(mid, None)

    def reconnect_session(self) -> int:
        """Riapertura della sessione persistente: i messaggi senza PUBACK tornano in testa con DUP."""
        pending = sorted(self._inflight.values(), key=lambda m: m.mid)
        for m in reversed(pending):
            m.dup = True
            self._queue.appendleft(m)
        self._inflight.clear()
        self.riconsegnati += len(pending)
        return len(pending)

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def inflight(self) -> int:
        return len(self._inflight)
