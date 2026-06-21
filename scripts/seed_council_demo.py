"""seed_council_demo.py — Demo-Seed für council.db.

Erzeugt eine "lived-in" Council-Demo-State für Public-Release-Screenshots:
  - 6 Council-Objekte (3 single-portal + 1 cross-portal-cluster (2 rows) + 1 expired)
  - Lens-Rankings für 3 Personas (baumeister/rechner/ortskundige), je 1..5
  - Lens-Comparisons (~30 paarweise Vergleiche)
  - consolidated_top10 (Borda-Score-Aggregation)
  - council_runs: 1 council-ingest + 1 council-lens, beide completed
  - object_lifecycle_events für das expired Objekt

Idempotent: prüft auf source_url-Prefix 'https://idealista.example/' bzw.
'https://homegate.example/' bzw. 'https://immoscout24.example/' und überspringt
den Insert wenn schon Demo-Objekte da sind.

Pfad-Override: COUNCIL_DB_PATH kann gesetzt werden, sonst Default-Pfad
~/.council/council.db (db_v2.DB_PATH_V2).

NICHT für Produktion — Fixtures sind fiktiv (Algarve-Persona Alex+Maya).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Default-Pfad aus council.db_v2 (ohne Import — wir wollen keine Cross-Repo-
# Code-Dependency, die in einem öffentlichen Demo bricht).
_DEFAULT_COUNCIL_DB = Path.home() / ".council" / "council.db"

# --- Demo-Objekte ----------------------------------------------------------

DEMO_OBJECTS = [
    {
        "id": "demo-faro-t3",
        "source_url": "https://idealista.example/expose/12345",
        "portal": "idealista",
        "address": "Rua de Santo António, 8000-302 Faro",
        "qm": 92,
        "bj": 2008,
        "price_value": 380000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/faro-t3.png",
        "status_tag": "kaufen",
        "title": "Apartamento T3 Faro centro histórico",
        "description": "3 quartos, 2 wc, varanda sul, elevador, garagem fechada",
        # 2026-06-11: link to feedback row for cross-DB distance-pill lookup.
        # feedback IDs are deterministic 1..40 from seed_pipeline_demo.py.
        "from_feedback_ids": "[1]",
    },
    {
        "id": "demo-loule-moradia",
        "source_url": "https://homegate.example/expose/77821",
        "portal": "homegate",
        "address": "Rua das Oliveiras 18, 8100-244 Loulé",
        "qm": 142,
        "bj": 2002,
        "price_value": 520000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/loule-moradia.png",
        "status_tag": "neu",
        "title": "Moradia T4 Loulé Cerro com piscina",
        "description": "Terreno 480 m², 4 quartos, lareira, garagem 2 carros",
        "from_feedback_ids": "[2]",
    },
    {
        "id": "demo-tavira-t3",
        "source_url": "https://immoscout24.example/expose/44012",
        "portal": "immoscout24",
        "address": "Avenida Dr. Mateus Teixeira de Azevedo, 8800-251 Tavira",
        "qm": 110,
        "bj": 2015,
        "price_value": 425000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/tavira-t3.png",
        "status_tag": "beobachten",
        "title": "T3 Tavira com piscina partilhada",
        "description": "Vista rio Gilão, ar condicionado, aquecimento central",
        "from_feedback_ids": "[3]",
    },
    {
        # Cross-portal-cluster Rows A + B: gleiche physische Adresse
        # auf zwei Portalen → Provenance-Pill in folio Council-UI.
        "id": "demo-olhao-cluster-a",
        "source_url": "https://homegate.example/expose/15673",
        "portal": "homegate",
        "address": "Rua do Comércio 47, 8700-307 Olhão",
        "qm": 65,
        "bj": 1924,
        "price_value": 280000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/olhao-cluster.png",
        "status_tag": "kaufen",
        "title": "Casa renovada T2 Olhão centro (homegate)",
        "description": "Renovação completa 2025, terraço com vista mar",
        "from_feedback_ids": "[5]",
    },
    {
        "id": "demo-olhao-cluster-b",
        "source_url": "https://idealista.example/expose/87654",
        "portal": "idealista",
        "address": "Rua do Comércio 47, 8700-307 Olhão",
        "qm": 65,
        "bj": 1924,
        "price_value": 280000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/olhao-cluster.png",
        "status_tag": "kaufen",
        "title": "Casa T2 Olhão (idealista)",
        "description": "Mesma propriedade — anúncio cruzado idealista",
        "from_feedback_ids": "[5]",
    },
    {
        # Luxury villa — zeigt Pricing-Diversität in Screenshots
        "id": "demo-almancil-villa",
        "source_url": "https://immoscout24.example/expose/91234",
        "portal": "immoscout24",
        "address": "Urbanização Quinta do Lago Norte, 8135-014 Almancil",
        "qm": 220,
        "bj": 2018,
        "price_value": 750000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/almancil-villa.png",
        "status_tag": "beobachten",
        "title": "Villa V4 Almancil · piscina + golf",
        "description": "Quinta do Lago Norte, 1100 m² terreno, jardim mediterrânico",
        "from_feedback_ids": "[6]",
    },
    {
        # Expired/abgelaufen — zeigt expired-Status + lifecycle-event
        "id": "demo-vilamoura-expired",
        "source_url": "https://idealista.example/expose/30022",
        "portal": "idealista",
        "address": "Avenida Tivoli, 8125-465 Vilamoura",
        "qm": 80,
        "bj": 2018,
        "price_value": 410000,
        "price_currency": "EUR",
        "photo_url": "/demo-assets/photos/vilamoura-expired.png",
        "status_tag": "abgelaufen",
        "title": "T2 Vilamoura golf-side (expired)",
        "description": "Não mais disponível — listagem expirou maio 2026",
        "from_feedback_ids": "[7]",
    },
]

# --- Lens-Rankings ---------------------------------------------------------
# 3 Personas, jede vergibt 1..5 an die ersten 5 Objekte (exclude cluster-b dup
# und expired). Borda-Score = sum over personas of (N - rank + 1) wo N=5.

LENS_PERSONAS = ["lens-baumeister", "lens-rechner", "lens-ortskundige"]

# Persona-spezifische Präferenzen — abstrakt aber konsistent:
#   baumeister:  bevorzugt Substanz/Qualität → moradia + cluster (renoviert) oben
#   rechner:     bevorzugt Preis-pro-qm → Olhão-cluster (€4307/m²) + Tavira oben
#   ortskundige: bevorzugt zentrale Algarve-Lagen → Faro + Loulé oben

RANKINGS_BY_PERSONA = {
    "lens-baumeister": [
        ("demo-loule-moradia", 1),
        ("demo-almancil-villa", 2),
        ("demo-olhao-cluster-a", 3),
        ("demo-faro-t3", 4),
        ("demo-tavira-t3", 5),
        ("demo-vilamoura-expired", 6),
    ],
    "lens-rechner": [
        ("demo-olhao-cluster-a", 1),
        ("demo-tavira-t3", 2),
        ("demo-vilamoura-expired", 3),
        ("demo-faro-t3", 4),
        ("demo-loule-moradia", 5),
        ("demo-almancil-villa", 6),  # 750k zu teuer pro m²
    ],
    "lens-ortskundige": [
        ("demo-faro-t3", 1),
        ("demo-almancil-villa", 2),
        ("demo-loule-moradia", 3),
        ("demo-tavira-t3", 4),
        ("demo-olhao-cluster-a", 5),
        ("demo-vilamoura-expired", 6),
    ],
}

# --- Lens-Comparisons (sampled, nicht voll C(5,2)=10 pro Persona) ---------
# Repräsentative Auswahl — UI rendert sie als "X has been compared against Y"
# Hinweise. Volle Comparison-Matrix wäre overkill für Demo.

DEMO_COMPARISONS = [
    # baumeister
    ("lens-baumeister", "demo-loule-moradia", "demo-faro-t3", "demo-loule-moradia",
     "moradia mit Terreno schlägt Apartamento — Substanz", "high"),
    ("lens-baumeister", "demo-olhao-cluster-a", "demo-tavira-t3", "demo-olhao-cluster-a",
     "Renovação completa 2025, certificada", "medium"),
    ("lens-baumeister", "demo-loule-moradia", "demo-vilamoura-expired", "demo-loule-moradia",
     "Bessere Bausubstanz und Grundstücksgröße", "high"),
    # rechner
    ("lens-rechner", "demo-olhao-cluster-a", "demo-faro-t3", "demo-olhao-cluster-a",
     "€4307/m² vs €4130/m² — knapp, aber Olhão mit Marge", "medium"),
    ("lens-rechner", "demo-tavira-t3", "demo-loule-moradia", "demo-tavira-t3",
     "€3863/m² schlägt €3661/m² — Tavira günstiger pro m²", "high"),
    ("lens-rechner", "demo-vilamoura-expired", "demo-faro-t3", "demo-vilamoura-expired",
     "€5125/m² — würde gewinnen wenn nicht expired", "low"),
    # ortskundige
    ("lens-ortskundige", "demo-faro-t3", "demo-tavira-t3", "demo-faro-t3",
     "Faro Innenstadt = zentralere Lage, bessere Anbindung", "high"),
    ("lens-ortskundige", "demo-loule-moradia", "demo-olhao-cluster-a", "demo-loule-moradia",
     "Loulé hat mehr Infrastruktur, weniger Touristik-Druck", "medium"),
    ("lens-ortskundige", "demo-faro-t3", "demo-loule-moradia", "demo-faro-t3",
     "Bei gleichem Budget zentraler", "high"),
]

# --- Council-Runs (für Pipeline-Verlauf-Anzeige) --------------------------

INGEST_RUN_UUID = "demo-council-ingest-20260608"
LENS_RUN_UUID = "demo-council-lens-20260610"


def already_seeded(conn: sqlite3.Connection) -> bool:
    """Heuristik: wenn demo-faro-t3 schon existiert, ist gesamt-Seed schon da."""
    cur = conn.execute("SELECT 1 FROM objects WHERE id = ?", ("demo-faro-t3",))
    return cur.fetchone() is not None


def insert_cluster(conn: sqlite3.Connection, now_iso: str) -> int:
    """Verbindet demo-olhao-cluster-a + -b via object_clusters + cluster_members.
    Wird vom folio cluster-substance.ts gelesen — wenn nur eins der beiden
    einen status_override hat (siehe seed_pipeline_demo.py), zeigt das andere
    die Provenance-Pill 'via homegate' o.ä."""
    cur = conn.execute(
        """INSERT INTO object_clusters (plz, qm, price, price_currency)
           VALUES (?, ?, ?, ?)""",
        (8700, 65, 280000, "EUR"),
    )
    cluster_id = cur.lastrowid
    conn.execute(
        "INSERT INTO cluster_members (cluster_id, object_id, joined_at) VALUES (?, ?, ?)",
        (cluster_id, "demo-olhao-cluster-a", now_iso),
    )
    conn.execute(
        "INSERT INTO cluster_members (cluster_id, object_id, joined_at) VALUES (?, ?, ?)",
        (cluster_id, "demo-olhao-cluster-b", now_iso),
    )
    return cluster_id


def insert_objects(conn: sqlite3.Connection, now_iso: str) -> int:
    n = 0
    for obj in DEMO_OBJECTS:
        last_seen = now_iso
        if obj["status_tag"] == "abgelaufen":
            # expired objects: last_seen einen Monat zurück
            last_seen = (datetime.now() - timedelta(days=32)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO objects (
                id, source_url, portal, address, qm, bj,
                price_value, price_currency, photo_url,
                object_class, status_tag, title, description,
                times_seen, last_seen, created_at, last_updated,
                from_feedback_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obj["id"], obj["source_url"], obj["portal"],
                obj["address"], obj["qm"], obj["bj"],
                obj["price_value"], obj["price_currency"], obj["photo_url"],
                "annonce", obj["status_tag"], obj["title"], obj["description"],
                3 if obj["status_tag"] != "abgelaufen" else 1,
                last_seen, now_iso, last_seen,
                obj.get("from_feedback_ids"),
            ),
        )
        n += 1
    return n


def insert_lifecycle_events(conn: sqlite3.Connection, now_iso: str) -> int:
    expired = (datetime.now() - timedelta(days=32)).strftime("%Y-%m-%d %H:%M:%S")
    first_seen = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    # First-seen für alle, expired-event nur für das expired Objekt
    rows = []
    for obj in DEMO_OBJECTS:
        rows.append((obj["id"], "first_seen", first_seen if obj["status_tag"] == "abgelaufen" else now_iso))
    rows.append(("demo-vilamoura-expired", "expired", expired))
    for object_id, event_type, recorded_at in rows:
        conn.execute(
            """INSERT INTO object_lifecycle_events
                   (object_id, event_type, recorded_at)
               VALUES (?, ?, ?)""",
            (object_id, event_type, recorded_at),
        )
    return len(rows)


def insert_council_runs(conn: sqlite3.Connection, now_iso: str) -> None:
    # Council-Ingest-Run (objects befüllt)
    conn.execute(
        """INSERT INTO council_runs
               (run_uuid, run_type, started_at, ended_at, status, n_processed, exit_code)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            INGEST_RUN_UUID, "council-ingest",
            (datetime.now() - timedelta(days=3, hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            (datetime.now() - timedelta(days=3, hours=1, minutes=58)).strftime("%Y-%m-%d %H:%M:%S"),
            "completed", len(DEMO_OBJECTS), 0,
        ),
    )
    # Council-Lens-Run (rankings + comparisons computed)
    conn.execute(
        """INSERT INTO council_runs
               (run_uuid, run_type, started_at, ended_at, status, n_processed, exit_code)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            LENS_RUN_UUID, "council-lens",
            (datetime.now() - timedelta(days=1, hours=14)).strftime("%Y-%m-%d %H:%M:%S"),
            (datetime.now() - timedelta(days=1, hours=13, minutes=25)).strftime("%Y-%m-%d %H:%M:%S"),
            "completed", len(LENS_PERSONAS), 0,
        ),
    )


def insert_rankings(conn: sqlite3.Connection, now_iso: str) -> int:
    n = 0
    for persona_id, ranked_pairs in RANKINGS_BY_PERSONA.items():
        for object_id, rank in ranked_pairs:
            conn.execute(
                """INSERT INTO rankings
                       (participant_id, object_id, rank, recorded_at)
                   VALUES (?, ?, ?, ?)""",
                (persona_id, object_id, rank, now_iso),
            )
            n += 1
    return n


def insert_comparisons(conn: sqlite3.Connection, now_iso: str) -> int:
    for lens_id, obj_a, obj_b, winner, reason, conf in DEMO_COMPARISONS:
        conn.execute(
            """INSERT INTO lens_comparisons
                   (lens_id, obj_a_id, obj_b_id, winner_id, reason, confidence, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (lens_id, obj_a, obj_b, winner, reason, conf, now_iso),
        )
    return len(DEMO_COMPARISONS)


def insert_top10(conn: sqlite3.Connection, now_iso: str) -> int:
    # Simple Borda-Aggregation: pro Objekt sum von (N - rank + 1).
    # N = max ranked count über alle Personas (heute 6).
    n = max(len(pairs) for pairs in RANKINGS_BY_PERSONA.values())
    scores: dict[str, float] = {}
    for ranked_pairs in RANKINGS_BY_PERSONA.values():
        for object_id, rank in ranked_pairs:
            scores[object_id] = scores.get(object_id, 0.0) + (n - rank + 1)
    # Sort desc by score, assign rank 1..N
    ordered = sorted(scores.items(), key=lambda x: -x[1])
    for rank, (object_id, score) in enumerate(ordered, start=1):
        conn.execute(
            """INSERT INTO consolidated_top10
                   (object_id, borda_score, rank, computed_at)
               VALUES (?, ?, ?, ?)""",
            (object_id, score, rank, now_iso),
        )
    return len(ordered)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=os.environ.get("COUNCIL_DB_PATH", str(_DEFAULT_COUNCIL_DB)),
        help="Path to council.db (default ~/.council/council.db)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if demo objects already exist (destructive — DELETEs demo rows first)",
    )
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"council.db not found at {db_path} — run setup_council_db.py first", file=sys.stderr)
        return 1

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    if already_seeded(conn):
        if args.force:
            print("Force-mode: deleting existing demo rows…")
            demo_ids = [o["id"] for o in DEMO_OBJECTS]
            placeholders = ",".join(["?"] * len(demo_ids))
            conn.execute(f"DELETE FROM consolidated_top10 WHERE object_id IN ({placeholders})", demo_ids)
            conn.execute(f"DELETE FROM lens_comparisons WHERE obj_a_id IN ({placeholders}) OR obj_b_id IN ({placeholders})", demo_ids * 2)
            conn.execute(f"DELETE FROM rankings WHERE object_id IN ({placeholders})", demo_ids)
            conn.execute(f"DELETE FROM object_lifecycle_events WHERE object_id IN ({placeholders})", demo_ids)
            conn.execute(f"DELETE FROM cluster_members WHERE object_id IN ({placeholders})", demo_ids)
            # delete any orphan cluster (no members left) — clean way: select cluster_ids previously linked
            conn.execute("DELETE FROM object_clusters WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM cluster_members)")
            conn.execute("DELETE FROM council_runs WHERE run_uuid IN (?, ?)", (INGEST_RUN_UUID, LENS_RUN_UUID))
            conn.execute(f"DELETE FROM objects WHERE id IN ({placeholders})", demo_ids)
            conn.commit()
        else:
            print("Demo objects already exist in council.db — skipping (use --force to re-seed)")
            return 0

    n_objects = insert_objects(conn, now_iso)
    n_lifecycle = insert_lifecycle_events(conn, now_iso)
    cluster_id = insert_cluster(conn, now_iso)
    insert_council_runs(conn, now_iso)
    n_rankings = insert_rankings(conn, now_iso)
    n_comparisons = insert_comparisons(conn, now_iso)
    n_top10 = insert_top10(conn, now_iso)

    conn.commit()
    conn.close()

    print(f"Seeded council.db ({db_path}):")
    print(f"  objects:                {n_objects}")
    print(f"  object_lifecycle_events: {n_lifecycle}")
    print(f"  object_clusters:        1 (cluster_id={cluster_id}, Olhão pair)")
    print(f"  cluster_members:        2")
    print(f"  council_runs:           2 (1 council-ingest, 1 council-lens)")
    print(f"  rankings:               {n_rankings} ({len(LENS_PERSONAS)} personas × 6 objects)")
    print(f"  lens_comparisons:       {n_comparisons}")
    print(f"  consolidated_top10:     {n_top10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
