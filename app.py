
import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import date, datetime, timezone


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Fencing Diary",
    page_icon="🤺",
    layout="centered",
)

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"],
)

st.title("🤺 Fencing Diary")


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def clean_int(value):
    if value is None or pd.isna(value) or clean_text(value) == "":
        return None
    return int(float(value))


def normalize_name(value):
    return " ".join(clean_text(value).casefold().split())


def canonical_weapon(value):
    raw = normalize_name(value)
    if raw in {"epee", "épée"}:
        return "Épée"
    if raw == "foil":
        return "Foil"
    if raw in {"sabre", "saber"}:
        return "Sabre"
    return clean_text(value) or "Épée"


def competition_event_key(name, competition_date, weapon, event_name):
    """Stable identity for one event inside a tournament."""
    return "|".join([
        normalize_name(name),
        clean_text(competition_date),
        normalize_name(canonical_weapon(weapon)),
        normalize_name(event_name),
    ])


def get_result_icon(my_score, their_score):
    if my_score > their_score:
        return "🟢 W"
    if my_score < their_score:
        return "🔴 L"
    return "⚪ D"


def get_active_session():
    response = (
        supabase
        .table("sessions")
        .select("*")
        .is_("ended_at", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def get_opponents():
    return (
        supabase
        .table("opponents")
        .select("*")
        .order("name")
        .execute()
        .data
    )


def get_me():
    response = (
        supabase
        .table("opponents")
        .select("*")
        .eq("is_me", True)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def opponent_map():
    return {row["id"]: row for row in get_opponents()}


def get_bouts():
    rows = (
        supabase
        .table("bouts")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    people = opponent_map()
    for row in rows:
        row["opponent"] = people.get(row.get("opponent_id"))
    return rows


def get_sessions():
    return (
        supabase
        .table("sessions")
        .select("*")
        .order("session_date", desc=True)
        .execute()
        .data
    )


def get_competitions():
    return (
        supabase
        .table("competitions")
        .select("*")
        .order("competition_date", desc=True)
        .execute()
        .data
    )


def get_competition_fencers(competition_id):
    rows = (
        supabase
        .table("competition_fencers")
        .select("*")
        .eq("competition_id", competition_id)
        .execute()
        .data
    )
    people = opponent_map()
    for row in rows:
        row["person"] = people.get(row.get("opponent_id"))
    return rows


def get_competition_bouts(competition_id):
    rows = (
        supabase
        .table("competition_bouts")
        .select("*")
        .eq("competition_id", competition_id)
        .order("id")
        .execute()
        .data
    )

    rows = dedupe_competition_bout_rows(rows)

    people = opponent_map()
    for row in rows:
        row["fencer_a"] = people.get(row.get("fencer_a_id"))
        row["fencer_b"] = people.get(row.get("fencer_b_id"))

    return rows


def canonical_bout_signature(stage, poule_number, round_name, a_id, score_a, b_id, score_b):
    stage = clean_text(stage)
    poule_number = clean_int(poule_number)
    round_name = clean_text(round_name)

    if a_id <= b_id:
        return (
            stage,
            poule_number,
            round_name,
            int(a_id),
            int(score_a),
            int(b_id),
            int(score_b),
        )

    return (
        stage,
        poule_number,
        round_name,
        int(b_id),
        int(score_b),
        int(a_id),
        int(score_a),
    )


def dedupe_competition_bout_rows(rows):
    """Return one row for each actual competition bout.

    This protects the UI from legacy duplicate rows already stored in
    Supabase, including the same bout entered with the fencers reversed.
    """
    unique_rows = []
    seen = set()

    for row in rows:
        try:
            signature = canonical_bout_signature(
                row.get("stage"),
                row.get("poule_number"),
                row.get("round_name"),
                row.get("fencer_a_id"),
                row.get("score_a"),
                row.get("fencer_b_id"),
                row.get("score_b"),
            )
        except (TypeError, ValueError):
            # Keep malformed legacy rows visible rather than crashing.
            unique_rows.append(row)
            continue

        if signature in seen:
            continue

        seen.add(signature)
        unique_rows.append(row)

    return unique_rows


# ============================================================
# STRENGTH RANKING HELPERS
# ============================================================

STRENGTH_START_RATING = 1500.0
STRENGTH_K_FACTOR = 32.0
STRENGTH_DE_MULTIPLIER = 1.20
STRENGTH_ESTABLISHED_BOUTS = 10

# Recent competitions should describe current strength more strongly
# than old competitions. The effect of a result decays toward a 25%
# floor, with a 180-day half-life.
STRENGTH_RECENCY_HALF_LIFE_DAYS = 180
STRENGTH_RECENCY_FLOOR = 0.25
STRENGTH_RECENT_FORM_DAYS = 180

# Competition-category weighting.
#
# Open/default competition results are the 1.00 baseline.
# Novice-only events deliberately move the main strength rating less,
# while higher-level events carry somewhat more rating information.
STRENGTH_CATEGORY_WEIGHTS = {
    "Novice": 0.55,
    "Club": 0.80,
    "Open": 1.00,
    "State": 1.05,
    "National": 1.15,
    "International": 1.25,
}

DE_ROUND_ORDER = {
    "T512": 0,
    "T256": 1,
    "T128": 2,
    "T64": 3,
    "T32": 4,
    "T16": 5,
    "T8": 6,
    "QF": 6,
    "SF": 7,
    "FINAL": 8,
}


def expected_score(rating_a, rating_b):
    return 1.0 / (
        1.0
        + 10.0 ** ((rating_b - rating_a) / 400.0)
    )


def parse_competition_date(value):
    raw = clean_text(value)

    if not raw:
        return None

    try:
        return datetime.fromisoformat(
            raw[:10]
        ).date()
    except (TypeError, ValueError):
        return None


def competition_recency_weight(
    competition_date,
    reference_date=None,
):
    """Return (weight, age_days) for the current-strength model.

    New results receive full weight. Older results decay exponentially
    toward STRENGTH_RECENCY_FLOOR instead of disappearing completely.
    """
    if reference_date is None:
        reference_date = date.today()

    comp_date = parse_competition_date(
        competition_date
    )

    if comp_date is None:
        return 1.0, 0

    age_days = max(
        0,
        (reference_date - comp_date).days,
    )

    decay = 0.5 ** (
        age_days
        / STRENGTH_RECENCY_HALF_LIFE_DAYS
    )

    weight = (
        STRENGTH_RECENCY_FLOOR
        + (
            1.0
            - STRENGTH_RECENCY_FLOOR
        )
        * decay
    )

    return weight, age_days


def competition_category_weight(competition):
    """Return (label, multiplier) for the strength model.

    The explicit competitions.level field is used first. If it is blank,
    the event/competition name is inspected so imported novice events are
    still safely down-weighted. Unknown/open events use the Open baseline.
    """
    level_raw = clean_text(
        competition.get("level")
    )
    event_raw = clean_text(
        competition.get("event_name")
    )
    name_raw = clean_text(
        competition.get("name")
    )

    combined = normalize_name(
        " ".join(
            value
            for value in (
                level_raw,
                event_raw,
                name_raw,
            )
            if value
        )
    )

    explicit_map = {
        "novice": "Novice",
        "club": "Club",
        "open": "Open",
        "state": "State",
        "national": "National",
        "international": "International",
    }

    explicit_level = normalize_name(level_raw)

    if explicit_level in explicit_map:
        label = explicit_map[explicit_level]
        return (
            label,
            STRENGTH_CATEGORY_WEIGHTS[label],
        )

    if "novice" in combined:
        label = "Novice"
    elif "international" in combined:
        label = "International"
    elif (
        "national" in combined
        or "afc" in combined
    ):
        label = "National"
    elif "state" in combined:
        label = "State"
    elif "club" in combined:
        label = "Club"
    else:
        label = "Open"

    return (
        label,
        STRENGTH_CATEGORY_WEIGHTS[label],
    )


def get_all_competition_bouts():
    rows = (
        supabase
        .table("competition_bouts")
        .select("*")
        .order("id")
        .execute()
        .data
    )

    return dedupe_competition_bout_rows(rows)


def strength_batch_update(
    batch,
    ratings,
    stats,
    history,
    competition,
    stage_label,
    stage_multiplier,
    recency_multiplier=1.0,
    category_multiplier=1.0,
    category_label="Open",
    age_days=0,
):
    """Apply one rating period.

    Poules are treated as one rating period and each DE round is treated
    as one rating period. Rating changes are multiplied by both the stage
    multiplier, a recency multiplier, and a competition-category
    multiplier. This lets current Open/State/National form drive the
    main rating while novice-only results still contribute at reduced weight.
    """
    if not batch:
        return

    participants = set()

    for bout in batch:
        a_id = bout.get("fencer_a_id")
        b_id = bout.get("fencer_b_id")

        if a_id is None or b_id is None or a_id == b_id:
            continue

        participants.add(a_id)
        participants.add(b_id)

    for fencer_id in participants:
        ratings.setdefault(
            fencer_id,
            STRENGTH_START_RATING,
        )

        stats.setdefault(
            fencer_id,
            {
                "bouts": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "effective_bouts": 0.0,
                "recent_bouts": 0,
                "recent_wins": 0,
                "recent_losses": 0,
                "recent_draws": 0,
                "last_competition_date": "",
                "last_competition_name": "",
            },
        )

        history.setdefault(
            fencer_id,
            [],
        )

    snapshot = {
        fencer_id: ratings[fencer_id]
        for fencer_id in participants
    }

    deltas = {
        fencer_id: 0.0
        for fencer_id in participants
    }

    competed = set()

    for bout in batch:
        a_id = bout.get("fencer_a_id")
        b_id = bout.get("fencer_b_id")

        if (
            a_id is None
            or b_id is None
            or a_id == b_id
            or a_id not in snapshot
            or b_id not in snapshot
        ):
            continue

        score_a = bout.get("score_a")
        score_b = bout.get("score_b")

        if score_a is None or score_b is None:
            continue

        try:
            score_a = int(score_a)
            score_b = int(score_b)
        except (TypeError, ValueError):
            continue

        if score_a > score_b:
            actual_a = 1.0
            actual_b = 0.0
        elif score_a < score_b:
            actual_a = 0.0
            actual_b = 1.0
        else:
            actual_a = 0.5
            actual_b = 0.5

        expected_a = expected_score(
            snapshot[a_id],
            snapshot[b_id],
        )

        expected_b = 1.0 - expected_a

        k = (
            STRENGTH_K_FACTOR
            * stage_multiplier
            * recency_multiplier
            * category_multiplier
        )

        deltas[a_id] += (
            k
            * (actual_a - expected_a)
        )

        deltas[b_id] += (
            k
            * (actual_b - expected_b)
        )

        competed.add(a_id)
        competed.add(b_id)

        stats[a_id]["bouts"] += 1
        stats[b_id]["bouts"] += 1

        # Effective bouts are a confidence measure for current strength.
        # One recent bout counts as 1.0; an old bout counts less.
        effective_weight = (
            recency_multiplier
            * category_multiplier
        )

        stats[a_id]["effective_bouts"] += (
            effective_weight
        )
        stats[b_id]["effective_bouts"] += (
            effective_weight
        )

        is_recent = (
            age_days
            <= STRENGTH_RECENT_FORM_DAYS
        )

        if is_recent:
            stats[a_id]["recent_bouts"] += 1
            stats[b_id]["recent_bouts"] += 1

        if actual_a == 1.0:
            stats[a_id]["wins"] += 1
            stats[b_id]["losses"] += 1

            if is_recent:
                stats[a_id]["recent_wins"] += 1
                stats[b_id]["recent_losses"] += 1

        elif actual_a == 0.0:
            stats[a_id]["losses"] += 1
            stats[b_id]["wins"] += 1

            if is_recent:
                stats[a_id]["recent_losses"] += 1
                stats[b_id]["recent_wins"] += 1

        else:
            stats[a_id]["draws"] += 1
            stats[b_id]["draws"] += 1

            if is_recent:
                stats[a_id]["recent_draws"] += 1
                stats[b_id]["recent_draws"] += 1

        for fencer_id in (a_id, b_id):
            stats[fencer_id][
                "last_competition_date"
            ] = clean_text(
                competition.get(
                    "competition_date"
                )
            )

            stats[fencer_id][
                "last_competition_name"
            ] = clean_text(
                competition.get("name")
            )

    for fencer_id in competed:
        ratings[fencer_id] += (
            deltas[fencer_id]
        )

        history[fencer_id].append({
            "date": clean_text(
                competition.get(
                    "competition_date"
                )
            ),
            "competition": clean_text(
                competition.get("name")
            ),
            "stage": stage_label,
            "rating": ratings[fencer_id],
            "change": deltas[fencer_id],
            "recency_weight": recency_multiplier,
            "category": category_label,
            "category_weight": category_multiplier,
            "combined_weight": (
                recency_multiplier
                * category_multiplier
            ),
            "age_days": age_days,
        })


def calculate_strength_rankings():
    """Calculate current competition-only strength rankings by weapon.

    Model:
    - All rated fencers start at 1500.
    - Poule results use Elo K=32.
    - DE results are weighted 20% more strongly.
    - Results are recency weighted: newer competitions move current
      strength more than older competitions.
    - Competition category also matters: novice-only events are
      deliberately down-weighted, while State/National/International
      events carry somewhat more weight than the Open baseline.
    - Old results retain a 25% minimum influence rather than vanishing.
    - Final placings are not separately scored because the individual
      bouts that created those placings are already included.
    - Training/diary bouts are excluded from the strength ranking.
    """
    competitions = get_competitions()

    competition_by_id = {
        row["id"]: row
        for row in competitions
    }

    all_bouts = get_all_competition_bouts()

    bouts_by_competition = {}

    for bout in all_bouts:
        competition_id = bout.get(
            "competition_id"
        )

        if competition_id not in competition_by_id:
            continue

        bouts_by_competition.setdefault(
            competition_id,
            [],
        ).append(bout)

    chronological_competitions = sorted(
        competitions,
        key=lambda row: (
            clean_text(
                row.get(
                    "competition_date"
                )
            ),
            row.get("id", 0),
        ),
    )

    results = {}

    reference_date = date.today()

    for weapon in (
        "Épée",
        "Foil",
        "Sabre",
    ):
        ratings = {}
        stats = {}
        history = {}

        competition_count = 0
        processed_bouts = 0

        for competition in chronological_competitions:
            if canonical_weapon(
                competition.get("weapon")
            ) != weapon:
                continue

            competition_bouts = (
                bouts_by_competition.get(
                    competition["id"],
                    [],
                )
            )

            if not competition_bouts:
                continue

            competition_count += 1

            recency_multiplier, age_days = (
                competition_recency_weight(
                    competition.get(
                        "competition_date"
                    ),
                    reference_date,
                )
            )

            (
                category_label,
                category_multiplier,
            ) = competition_category_weight(
                competition
            )

            poule_bouts = [
                bout
                for bout in competition_bouts
                if normalize_name(
                    bout.get("stage")
                ) == "poule"
            ]

            if poule_bouts:
                strength_batch_update(
                    poule_bouts,
                    ratings,
                    stats,
                    history,
                    competition,
                    "Poules",
                    1.0,
                    recency_multiplier,
                    category_multiplier,
                    category_label,
                    age_days,
                )

                processed_bouts += len(
                    poule_bouts
                )

            de_bouts = [
                bout
                for bout in competition_bouts
                if normalize_name(
                    bout.get("stage")
                ) == "de"
            ]

            de_groups = {}

            for bout in de_bouts:
                raw_round = clean_text(
                    bout.get("round_name")
                )

                round_key = (
                    raw_round.upper()
                    if raw_round
                    else "DE"
                )

                de_groups.setdefault(
                    round_key,
                    [],
                ).append(bout)

            for round_key in sorted(
                de_groups.keys(),
                key=lambda key: (
                    DE_ROUND_ORDER.get(
                        key,
                        999,
                    ),
                    key,
                ),
            ):
                batch = de_groups[
                    round_key
                ]

                strength_batch_update(
                    batch,
                    ratings,
                    stats,
                    history,
                    competition,
                    round_key,
                    STRENGTH_DE_MULTIPLIER,
                    recency_multiplier,
                    category_multiplier,
                    category_label,
                    age_days,
                )

                processed_bouts += len(
                    batch
                )

        people = opponent_map()
        leaderboard = []

        for fencer_id, rating in ratings.items():
            stat = stats[fencer_id]
            person = people.get(fencer_id)

            if not person:
                continue

            bout_count = stat["bouts"]

            win_rate = (
                stat["wins"]
                / bout_count
                * 100
                if bout_count
                else 0
            )

            status = (
                "Established"
                if bout_count
                >= STRENGTH_ESTABLISHED_BOUTS
                else "Provisional"
            )

            effective_bouts = stat[
                "effective_bouts"
            ]

            confidence = (
                100.0
                * (
                    1.0
                    - 0.5 ** (
                        effective_bouts / 8.0
                    )
                )
            )

            confidence = min(
                99.0,
                confidence,
            )

            latest_date = stat[
                "last_competition_date"
            ]

            last_comp_change = sum(
                point["change"]
                for point in history.get(
                    fencer_id,
                    [],
                )
                if point.get("date")
                == latest_date
            )

            recent_bouts = stat[
                "recent_bouts"
            ]

            recent_win_rate = (
                stat["recent_wins"]
                / recent_bouts
                * 100
                if recent_bouts
                else 0
            )

            leaderboard.append({
                "fencer_id": fencer_id,
                "name": person["name"],
                "club": clean_text(
                    person.get("club")
                ),
                "region": clean_text(
                    person.get("region")
                ),
                "official_rank": (
                    person.get(
                        "official_rank"
                    )
                ),
                "rating": rating,
                "bouts": bout_count,
                "wins": stat["wins"],
                "losses": stat["losses"],
                "draws": stat["draws"],
                "win_rate": win_rate,
                "status": status,
                "effective_bouts": effective_bouts,
                "confidence": confidence,
                "recent_bouts": recent_bouts,
                "recent_wins": stat["recent_wins"],
                "recent_losses": stat["recent_losses"],
                "recent_draws": stat["recent_draws"],
                "recent_win_rate": recent_win_rate,
                "last_comp_change": last_comp_change,
                "last_competition_date": stat[
                    "last_competition_date"
                ],
                "last_competition_name": stat[
                    "last_competition_name"
                ],
            })

        leaderboard.sort(
            key=lambda row: (
                -row["rating"],
                -row["bouts"],
                normalize_name(
                    row["name"]
                ),
            )
        )

        for rank, row in enumerate(
            leaderboard,
            start=1,
        ):
            row["rank"] = rank

        by_id = {
            row["fencer_id"]: row
            for row in leaderboard
        }

        results[weapon] = {
            "leaderboard": leaderboard,
            "by_id": by_id,
            "history": history,
            "competition_count": (
                competition_count
            ),
            "bout_count": processed_bouts,
        }

    return results


# ============================================================
# IMPORT HELPERS
# ============================================================

IMPORT_REQUIRED_COLUMNS = {
    "row_type",
    "competition_name",
    "competition_date",
    "weapon",
    "name",
    "club",
    "region",
    "initial_seed",
    "de_seed",
    "final_place",
    "final_place_label",
    "stage",
    "poule_number",
    "round_name",
    "fencer_a",
    "score_a",
    "fencer_b",
    "score_b",
}


def validate_import(df):
    missing = sorted(IMPORT_REQUIRED_COLUMNS - set(df.columns))
    errors = []

    if missing:
        errors.append("Missing columns: " + ", ".join(missing))
        return errors

    row_types = {
        normalize_name(x).upper()
        for x in df["row_type"].dropna().tolist()
    }

    if "COMPETITION" not in row_types:
        errors.append("No COMPETITION row found.")

    if "FENCER" not in row_types:
        errors.append("No FENCER rows found.")

    if "BOUT" not in row_types:
        errors.append("No BOUT rows found.")

    competition_rows = df[
        df["row_type"].astype(str).str.upper() == "COMPETITION"
    ]

    if len(competition_rows) != 1:
        errors.append(
            f"Expected exactly 1 COMPETITION row, found {len(competition_rows)}."
        )

    return errors


def parse_import(df):
    work = df.copy()
    work["row_type"] = work["row_type"].astype(str).str.upper().str.strip()

    competition_row = work[work["row_type"] == "COMPETITION"].iloc[0]

    competition = {
        "name": clean_text(competition_row.get("competition_name")),
        "competition_date": clean_text(competition_row.get("competition_date")),
        "event_name": clean_text(competition_row.get("event_name")),
        "weapon": canonical_weapon(competition_row.get("weapon")),
        "location": clean_text(competition_row.get("location")),
        "level": clean_text(competition_row.get("level")),
        "field_size": clean_int(competition_row.get("field_size")),
    }

    fencer_rows = work[work["row_type"] == "FENCER"]
    fencers = []

    for _, row in fencer_rows.iterrows():
        name = clean_text(row.get("name"))
        if not name:
            continue

        fencers.append({
            "source_name": clean_text(row.get("source_name")),
            "name": name,
            "club": clean_text(row.get("club")),
            "region": clean_text(row.get("region")),
            "initial_seed": clean_int(row.get("initial_seed")),
            "de_seed": clean_int(row.get("de_seed")),
            "final_place": clean_int(row.get("final_place")),
            "final_place_label": clean_text(row.get("final_place_label")),
        })

    bout_rows = work[work["row_type"] == "BOUT"]
    bouts = []

    for _, row in bout_rows.iterrows():
        fencer_a = clean_text(row.get("fencer_a"))
        fencer_b = clean_text(row.get("fencer_b"))

        if not fencer_a or not fencer_b:
            continue

        bouts.append({
            "stage": clean_text(row.get("stage")),
            "poule_number": clean_int(row.get("poule_number")),
            "round_name": clean_text(row.get("round_name")),
            "fencer_a": fencer_a,
            "score_a": clean_int(row.get("score_a")),
            "fencer_b": fencer_b,
            "score_b": clean_int(row.get("score_b")),
        })

    return competition, fencers, bouts


def find_existing_competition(comp):
    """Find one exact fencing event.

    Tournament name/date/weapon are not enough because men's and women's
    events can share all three. v0.5.4 therefore uses event_name as part
    of a stable event key.
    """
    event_key = competition_event_key(
        comp["name"],
        comp["competition_date"],
        comp["weapon"],
        comp["event_name"],
    )

    query = (
        supabase
        .table("competitions")
        .select("*")
        .eq("event_key", event_key)
        .limit(1)
        .execute()
    )

    if query.data:
        return query.data[0]

    # Fallback for legacy rows that predate event_key.
    query = (
        supabase
        .table("competitions")
        .select("*")
        .eq("name", comp["name"])
        .eq("competition_date", comp["competition_date"])
        .eq("weapon", comp["weapon"])
        .eq("event_name", comp["event_name"])
        .limit(1)
        .execute()
    )

    if query.data:
        existing = query.data[0]

        supabase.table("competitions").update({
            "event_key": event_key
        }).eq(
            "id",
            existing["id"]
        ).execute()

        existing["event_key"] = event_key
        return existing

    return None


def find_exact_opponent(imported_fencer, existing_opponents):
    imported_names = {
        normalize_name(imported_fencer["name"]),
        normalize_name(imported_fencer.get("source_name")),
    }
    imported_names.discard("")

    matches = []

    for opponent in existing_opponents:
        if normalize_name(opponent["name"]) in imported_names:
            matches.append(opponent)

    if len(matches) == 1:
        return matches[0]

    return None


def import_competition_data(comp, imported_fencers, imported_bouts, name_choices):
    existing_comp = find_existing_competition(comp)

    competition_payload = {
        "name": comp["name"],
        "competition_date": comp["competition_date"],
        "event_name": comp["event_name"] or None,
        "event_key": competition_event_key(
            comp["name"],
            comp["competition_date"],
            comp["weapon"],
            comp["event_name"],
        ),
        "weapon": comp["weapon"],
        "location": comp["location"] or None,
        "level": comp["level"] or None,
        "field_size": comp["field_size"],
    }

    if existing_comp:
        competition_id = existing_comp["id"]

        supabase.table("competitions").update(
            competition_payload
        ).eq(
            "id",
            competition_id
        ).execute()

        competition_created = False

    else:
        result = (
            supabase
            .table("competitions")
            .insert(competition_payload)
            .execute()
        )

        competition_id = result.data[0]["id"]
        competition_created = True

    existing_opponents = get_opponents()
    opponent_by_id = {row["id"]: row for row in existing_opponents}
    opponent_by_norm_name = {
        normalize_name(row["name"]): row
        for row in existing_opponents
    }

    imported_name_to_id = {}
    new_fencer_count = 0

    for index, fencer in enumerate(imported_fencers):
        exact = find_exact_opponent(fencer, existing_opponents)

        if exact:
            opponent_id = exact["id"]

            update_payload = {}

            if fencer["club"] and not clean_text(exact.get("club")):
                update_payload["club"] = fencer["club"]

            if fencer["region"] and not clean_text(exact.get("region")):
                update_payload["region"] = fencer["region"]

            if not clean_text(exact.get("weapon")):
                update_payload["weapon"] = comp["weapon"]

            if update_payload:
                supabase.table("opponents").update(
                    update_payload
                ).eq(
                    "id",
                    opponent_id
                ).execute()

        else:
            selected = name_choices.get(index, "➕ Create new fencer")

            if selected != "➕ Create new fencer":
                match = opponent_by_norm_name.get(normalize_name(selected))

                if not match:
                    raise ValueError(
                        f"Could not resolve selected match for {fencer['name']}."
                    )

                opponent_id = match["id"]

            else:
                result = (
                    supabase
                    .table("opponents")
                    .insert({
                        "name": fencer["name"],
                        "club": fencer["club"] or None,
                        "region": fencer["region"] or None,
                        "handedness": "Unknown",
                        "weapon": comp["weapon"],
                    })
                    .execute()
                )

                opponent_id = result.data[0]["id"]
                new_fencer_count += 1

                new_person = result.data[0]
                existing_opponents.append(new_person)
                opponent_by_id[opponent_id] = new_person
                opponent_by_norm_name[normalize_name(new_person["name"])] = new_person

        imported_name_to_id[normalize_name(fencer["name"])] = opponent_id

        if fencer.get("source_name"):
            imported_name_to_id[
                normalize_name(fencer["source_name"])
            ] = opponent_id

    existing_comp_fencers = get_competition_fencers(competition_id)
    comp_fencer_by_opponent = {
        row["opponent_id"]: row
        for row in existing_comp_fencers
    }

    competition_fencer_adds = 0
    competition_fencer_updates = 0

    for fencer in imported_fencers:
        opponent_id = imported_name_to_id[normalize_name(fencer["name"])]

        payload = {
            "initial_seed": fencer["initial_seed"],
            "de_seed": fencer["de_seed"],
            "seed": fencer["de_seed"],
            "final_place": fencer["final_place"],
            "final_place_label": (
                fencer["final_place_label"] or
                (str(fencer["final_place"]) if fencer["final_place"] else None)
            ),
        }

        if opponent_id in comp_fencer_by_opponent:
            row_id = comp_fencer_by_opponent[opponent_id]["id"]

            supabase.table("competition_fencers").update(
                payload
            ).eq(
                "id",
                row_id
            ).execute()

            competition_fencer_updates += 1

        else:
            supabase.table("competition_fencers").insert({
                "competition_id": competition_id,
                "opponent_id": opponent_id,
                **payload,
            }).execute()

            competition_fencer_adds += 1

    existing_bouts = get_competition_bouts(competition_id)

    existing_signatures = {
        canonical_bout_signature(
            row.get("stage"),
            row.get("poule_number"),
            row.get("round_name"),
            row["fencer_a_id"],
            row["score_a"],
            row["fencer_b_id"],
            row["score_b"],
        )
        for row in existing_bouts
    }

    added_bouts = 0
    skipped_bouts = 0

    for bout in imported_bouts:
        a_id = imported_name_to_id.get(normalize_name(bout["fencer_a"]))
        b_id = imported_name_to_id.get(normalize_name(bout["fencer_b"]))

        if a_id is None or b_id is None:
            raise ValueError(
                f"Could not match bout: {bout['fencer_a']} vs {bout['fencer_b']}."
            )

        if bout["score_a"] is None or bout["score_b"] is None:
            raise ValueError(
                f"Missing score for {bout['fencer_a']} vs {bout['fencer_b']}."
            )

        signature = canonical_bout_signature(
            bout["stage"],
            bout["poule_number"],
            bout["round_name"],
            a_id,
            bout["score_a"],
            b_id,
            bout["score_b"],
        )

        if signature in existing_signatures:
            skipped_bouts += 1
            continue

        supabase.table("competition_bouts").insert({
            "competition_id": competition_id,
            "fencer_a_id": a_id,
            "fencer_b_id": b_id,
            "score_a": bout["score_a"],
            "score_b": bout["score_b"],
            "stage": bout["stage"],
            "poule_number": bout["poule_number"],
            "round_name": bout["round_name"] or None,
        }).execute()

        existing_signatures.add(signature)
        added_bouts += 1

    return {
        "competition_id": competition_id,
        "competition_created": competition_created,
        "new_fencers": new_fencer_count,
        "competition_fencer_adds": competition_fencer_adds,
        "competition_fencer_updates": competition_fencer_updates,
        "added_bouts": added_bouts,
        "skipped_bouts": skipped_bouts,
    }


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Menu",
    [
        "🏠 Dashboard",
        "🤺 Current Session",
        "👤 Opponents",
        "🏆 Competitions",
        "📈 Strength Rankings",
        "📚 Session History",
        "📖 Bout History",
    ],
)


# ============================================================
# DASHBOARD
# ============================================================

if page == "🏠 Dashboard":
    st.header("Your Fencing")

    bouts = get_bouts()
    sessions = get_sessions()

    total_bouts = len(bouts)
    wins = losses = draws = 0
    touches_for = touches_against = 0
    feeling_total = feeling_count = 0

    for bout in bouts:
        my_score = bout["my_score"]
        their_score = bout["opponent_score"]

        touches_for += my_score
        touches_against += their_score

        if my_score > their_score:
            wins += 1
        elif my_score < their_score:
            losses += 1
        else:
            draws += 1

        if bout.get("feeling") is not None:
            feeling_total += bout["feeling"]
            feeling_count += 1

    win_rate = (wins / total_bouts * 100) if total_bouts else 0
    average_margin = (
        (touches_for - touches_against) / total_bouts
        if total_bouts
        else 0
    )
    average_feeling = (
        feeling_total / feeling_count
        if feeling_count
        else 0
    )

    completed_sessions = [
        session
        for session in sessions
        if session.get("ended_at")
    ]

    col1, col2 = st.columns(2)
    col1.metric("Total bouts", total_bouts)
    col2.metric("Win rate", f"{win_rate:.0f}%")

    col3, col4 = st.columns(2)
    col3.metric("Record", f"{wins}W – {losses}L")
    col4.metric("Avg margin", f"{average_margin:+.2f}")

    col5, col6 = st.columns(2)
    col5.metric("Sessions", len(completed_sessions))
    col6.metric("Avg fencing", f"{average_feeling:.1f}/10")

    st.divider()
    st.subheader("Recent form")

    recent_bouts = bouts[:10]

    if recent_bouts:
        form = ""
        recent_wins = recent_losses = 0

        for bout in reversed(recent_bouts):
            if bout["my_score"] > bout["opponent_score"]:
                form += "🟢 "
                recent_wins += 1
            elif bout["my_score"] < bout["opponent_score"]:
                form += "🔴 "
                recent_losses += 1
            else:
                form += "⚪ "

        st.write(form)
        st.caption(
            f"Last {len(recent_bouts)} bouts: "
            f"{recent_wins}W – {recent_losses}L"
        )
    else:
        st.info("No bouts yet.")

    st.divider()
    st.subheader("Most fenced opponents")

    stats = {}

    for bout in bouts:
        person = bout.get("opponent")
        if not person:
            continue

        name = person["name"]
        stats.setdefault(name, {"bouts": 0, "wins": 0, "losses": 0})
        stats[name]["bouts"] += 1

        if bout["my_score"] > bout["opponent_score"]:
            stats[name]["wins"] += 1
        elif bout["my_score"] < bout["opponent_score"]:
            stats[name]["losses"] += 1

    for name, values in sorted(
        stats.items(),
        key=lambda x: x[1]["bouts"],
        reverse=True,
    )[:5]:
        st.write(
            f"**{name}** — {values['bouts']} bouts • "
            f"{values['wins']}W–{values['losses']}L"
        )

    st.divider()
    st.subheader("Competition strength")

    me = get_me()

    if not me:
        st.info(
            "Set your own fencer profile in Opponents to show "
            "your competition strength ranking."
        )
    else:
        strength_data = calculate_strength_rankings()

        my_strength_rows = []

        for weapon_name in (
            "Épée",
            "Foil",
            "Sabre",
        ):
            rating_row = (
                strength_data[
                    weapon_name
                ]["by_id"].get(
                    me["id"]
                )
            )

            if rating_row:
                my_strength_rows.append(
                    (
                        weapon_name,
                        rating_row,
                    )
                )

        if not my_strength_rows:
            st.caption(
                "No imported competition bouts are linked to "
                "your profile yet, so you do not have a strength rating."
            )
        else:
            for weapon_name, rating_row in my_strength_rows:
                col_a, col_b, col_c = st.columns(3)

                with col_a:
                    st.metric(
                        f"{weapon_name} rank",
                        f"#{rating_row['rank']}",
                    )

                with col_b:
                    st.metric(
                        "Strength",
                        f"{rating_row['rating']:.0f}",
                        delta=(
                            f"{rating_row['last_comp_change']:+.0f} "
                            "last comp"
                        ),
                    )

                with col_c:
                    st.metric(
                        "Confidence",
                        f"{rating_row['confidence']:.0f}%",
                    )

                st.caption(
                    f"Recent {STRENGTH_RECENT_FORM_DAYS}d: "
                    f"{rating_row['recent_wins']}W–"
                    f"{rating_row['recent_losses']}L • "
                    f"{rating_row['bouts']} total bouts • "
                    f"{rating_row['status']}"
                )

    st.divider()
    st.subheader("My competition results")

    me = get_me()

    if not me:
        st.info(
            "Set your own fencer profile in Opponents to show "
            "your competition results here."
        )
    else:
        my_comp_entries = (
            supabase
            .table("competition_fencers")
            .select("*")
            .eq("opponent_id", me["id"])
            .execute()
            .data
        )

        competitions_by_id = {
            row["id"]: row
            for row in get_competitions()
        }

        my_comp_rows = []

        for entry in my_comp_entries:
            competition = competitions_by_id.get(
                entry.get("competition_id")
            )

            if competition:
                my_comp_rows.append(
                    (competition, entry)
                )

        my_comp_rows.sort(
            key=lambda item:
                clean_text(item[0].get("competition_date")),
            reverse=True,
        )

        if not my_comp_rows:
            st.caption(
                "No imported competition results are linked "
                "to your profile yet."
            )
        else:
            for competition, entry in my_comp_rows[:5]:
                place_label = (
                    clean_text(entry.get("final_place_label"))
                    or (
                        str(entry["final_place"])
                        if entry.get("final_place") is not None
                        else "—"
                    )
                )

                result_line = f"**{competition['name']}**"

                if competition.get("event_name"):
                    result_line += (
                        f" — {competition['event_name']}"
                    )

                st.write(result_line)

                detail_bits = [
                    clean_text(competition.get("competition_date")),
                    (
                        f"Final: {place_label}"
                        + (
                            f" / {competition['field_size']}"
                            if competition.get("field_size")
                            else ""
                        )
                    ),
                ]

                if entry.get("de_seed"):
                    detail_bits.append(
                        f"DE seed: {entry['de_seed']}"
                    )

                st.caption(
                    " • ".join(
                        bit
                        for bit in detail_bits
                        if bit
                    )
                )

    st.divider()
    st.subheader("Latest bouts")

    for bout in bouts[:5]:
        person = bout.get("opponent")
        opponent = person["name"] if person else "Unknown"

        result = get_result_icon(
            bout["my_score"],
            bout["opponent_score"],
        )

        st.write(
            f"**{result} {bout['my_score']}–{bout['opponent_score']}** "
            f"vs {opponent}"
        )

        if bout.get("notes"):
            st.caption(bout["notes"])


# ============================================================
# CURRENT SESSION
# ============================================================

elif page == "🤺 Current Session":
    active_session = get_active_session()

    if active_session is None:
        st.header("Start fencing")

        with st.form("start_session"):
            session_date = st.date_input("Date", value=date.today())

            session_type = st.selectbox(
                "Session type",
                ["Training", "Lesson", "Competition"],
            )

            weapon = st.selectbox(
                "Weapon",
                ["Épée", "Foil", "Sabre"],
            )

            location = st.text_input("Location")

            col1, col2 = st.columns(2)

            with col1:
                energy_before = st.slider(
                    "Energy",
                    1,
                    10,
                    5,
                )

            with col2:
                confidence_before = st.slider(
                    "Confidence",
                    1,
                    10,
                    5,
                )

            start_session = st.form_submit_button(
                "🤺 START SESSION",
                type="primary",
                use_container_width=True,
            )

        if start_session:
            supabase.table("sessions").insert({
                "session_date": str(session_date),
                "session_type": session_type,
                "weapon": weapon,
                "location": location.strip(),
                "energy_before": energy_before,
                "confidence_before": confidence_before,
            }).execute()

            st.rerun()

    else:
        st.caption(
            f"{active_session['session_type']} • "
            f"{active_session['weapon']} • "
            f"{active_session['session_date']}"
        )

        if active_session.get("location"):
            st.caption(f"📍 {active_session['location']}")

        session_bouts = (
            supabase
            .table("bouts")
            .select("*")
            .eq("session_id", active_session["id"])
            .order("created_at")
            .execute()
            .data
        )

        people = opponent_map()

        wins = losses = 0
        touches_for = touches_against = 0

        for bout in session_bouts:
            touches_for += bout["my_score"]
            touches_against += bout["opponent_score"]

            if bout["my_score"] > bout["opponent_score"]:
                wins += 1
            elif bout["my_score"] < bout["opponent_score"]:
                losses += 1

        st.subheader("Current session")

        col1, col2, col3 = st.columns(3)
        col1.metric("Bouts", len(session_bouts))
        col2.metric("Record", f"{wins}–{losses}")
        col3.metric("Indicator", f"{touches_for - touches_against:+d}")

        st.divider()
        st.header("⚡ Log Bout")

        opponents = get_opponents()

        if not opponents:
            st.warning("Add an opponent first.")
        else:
            opponent_lookup = {
                person["name"]: person["id"]
                for person in opponents
            }

            with st.form(
                "quick_bout",
                clear_on_submit=True,
            ):
                opponent_name = st.selectbox(
                    "Opponent",
                    list(opponent_lookup.keys()),
                )

                left, middle, right = st.columns([4, 1, 4])

                with left:
                    my_score = st.number_input(
                        "You",
                        min_value=0,
                        max_value=50,
                        value=5,
                        step=1,
                    )

                with middle:
                    st.markdown(
                        "<h2 style='text-align:center;padding-top:22px;'>–</h2>",
                        unsafe_allow_html=True,
                    )

                with right:
                    opponent_score = st.number_input(
                        "Them",
                        min_value=0,
                        max_value=50,
                        value=3,
                        step=1,
                    )

                feeling = st.slider(
                    "How well did you fence?",
                    1,
                    10,
                    5,
                )

                quick_note = st.text_area(
                    "Quick note",
                    placeholder="Distance good, disengage worked...",
                    height=80,
                )

                with st.expander("Detailed notes"):
                    what_worked = st.text_area("What worked?")
                    what_didnt = st.text_area("What didn't work?")

                save_bout = st.form_submit_button(
                    "SAVE + NEXT BOUT",
                    type="primary",
                    use_container_width=True,
                )

            if save_bout:
                supabase.table("bouts").insert({
                    "session_id": active_session["id"],
                    "opponent_id": opponent_lookup[opponent_name],
                    "my_score": int(my_score),
                    "opponent_score": int(opponent_score),
                    "feeling": int(feeling),
                    "what_worked": what_worked,
                    "what_didnt": what_didnt,
                    "notes": quick_note,
                }).execute()

                st.toast(
                    f"Saved {my_score}–{opponent_score} vs {opponent_name}",
                    icon="🤺",
                )

                st.rerun()

        if session_bouts:
            st.divider()
            st.subheader("This session")

            for bout in reversed(session_bouts):
                person = people.get(bout["opponent_id"])
                opponent = person["name"] if person else "Unknown"

                result = get_result_icon(
                    bout["my_score"],
                    bout["opponent_score"],
                )

                st.write(
                    f"**{result} {bout['my_score']}–{bout['opponent_score']}** "
                    f"vs {opponent}"
                )

                if bout.get("notes"):
                    st.caption(bout["notes"])

        st.divider()

        with st.expander("🏁 Finish session"):
            with st.form("finish_session"):
                overall_rating = st.slider(
                    "Overall session",
                    1,
                    10,
                    5,
                )

                what_i_learned = st.text_area("What did you learn?")
                what_to_work_on = st.text_area("What should you work on?")
                session_notes = st.text_area(
                    "General session diary",
                    height=120,
                )

                end_session = st.form_submit_button(
                    "END SESSION",
                    use_container_width=True,
                )

            if end_session:
                supabase.table("sessions").update({
                    "overall_rating": overall_rating,
                    "what_i_learned": what_i_learned,
                    "what_to_work_on": what_to_work_on,
                    "session_notes": session_notes,
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }).eq(
                    "id",
                    active_session["id"],
                ).execute()

                st.success("Session saved!")
                st.rerun()


# ============================================================
# OPPONENTS
# ============================================================

elif page == "👤 Opponents":
    st.header("Opponents")

    # --------------------------------------------------------
    # ADD OPPONENT
    # --------------------------------------------------------

    with st.expander("➕ Add opponent"):
        with st.form(
            "add_opponent",
            clear_on_submit=True,
        ):
            name = st.text_input("Name")
            club = st.text_input("Club")
            region = st.text_input("Region / state")

            handedness = st.selectbox(
                "Handedness",
                ["Unknown", "Right", "Left"],
            )

            weapon = st.selectbox(
                "Weapon",
                ["Épée", "Foil", "Sabre"],
            )

            notes = st.text_area("Opponent notes")

            add_opponent = st.form_submit_button(
                "Add opponent",
                type="primary",
                use_container_width=True,
            )

        if add_opponent:
            if not name.strip():
                st.error("Enter a name.")
            else:
                try:
                    supabase.table("opponents").insert({
                        "name": name.strip(),
                        "club": club.strip() or None,
                        "region": region.strip() or None,
                        "handedness": handedness,
                        "weapon": weapon,
                        "notes": notes,
                    }).execute()

                    st.success(f"{name.strip()} added!")
                    st.rerun()

                except Exception as e:
                    st.error(f"Couldn't add opponent: {e}")

    opponents = get_opponents()

    if not opponents:
        st.info("No opponents yet.")

    else:
        opponent_names = [
            person["name"]
            for person in opponents
        ]

        selected_name = st.selectbox(
            "View opponent",
            opponent_names,
        )

        selected = next(
            person
            for person in opponents
            if person["name"] == selected_name
        )

        selected_id = selected["id"]
        current_me = get_me()

        st.divider()
        st.header(selected["name"])

        # ----------------------------------------------------
        # IDENTIFY USER PROFILE
        # ----------------------------------------------------

        if selected.get("is_me"):
            st.success("⭐ This is your fencer profile.")
        else:
            if current_me:
                st.caption(
                    f"Your current profile: {current_me['name']}"
                )

            if st.button(
                "⭐ Set as me",
                type="primary",
                use_container_width=True,
            ):
                supabase.table("opponents").update({
                    "is_me": False
                }).eq(
                    "is_me",
                    True
                ).execute()

                supabase.table("opponents").update({
                    "is_me": True
                }).eq(
                    "id",
                    selected_id
                ).execute()

                st.success(
                    f"{selected['name']} is now your fencer profile."
                )
                st.rerun()

        details = [
            clean_text(selected.get("club")),
            clean_text(selected.get("region")),
            clean_text(selected.get("handedness")),
            clean_text(selected.get("weapon")),
        ]
        details = [x for x in details if x]

        if details:
            st.caption(" • ".join(details))

        if selected.get("notes"):
            st.write("**Opponent notes**")
            st.write(selected["notes"])

        st.subheader("Competition strength")

        selected_strength_data = (
            calculate_strength_rankings()
        )

        selected_strength_rows = []

        for weapon_name in (
            "Épée",
            "Foil",
            "Sabre",
        ):
            row = (
                selected_strength_data[
                    weapon_name
                ]["by_id"].get(
                    selected_id
                )
            )

            if row:
                selected_strength_rows.append(
                    (
                        weapon_name,
                        row,
                    )
                )

        if not selected_strength_rows:
            st.caption(
                "No imported competition bouts for this fencer yet."
            )
        else:
            for weapon_name, row in selected_strength_rows:
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        f"{weapon_name} rank",
                        f"#{row['rank']}",
                    )

                with col2:
                    st.metric(
                        "Strength",
                        f"{row['rating']:.0f}",
                        delta=(
                            f"{row['last_comp_change']:+.0f} "
                            "last comp"
                        ),
                    )

                with col3:
                    st.metric(
                        "Confidence",
                        f"{row['confidence']:.0f}%",
                    )

                st.caption(
                    f"Recent {STRENGTH_RECENT_FORM_DAYS}d: "
                    f"{row['recent_wins']}W–"
                    f"{row['recent_losses']}L • "
                    f"Lifetime {row['wins']}W–"
                    f"{row['losses']}L • "
                    f"{row['status']}"
                )

        # ----------------------------------------------------
        # LOAD THIS FENCER'S COMPETITION DATA
        # ----------------------------------------------------

        all_competitions = get_competitions()

        competitions_by_id = {
            competition["id"]: competition
            for competition in all_competitions
        }

        competition_entries = (
            supabase
            .table("competition_fencers")
            .select("*")
            .eq("opponent_id", selected_id)
            .execute()
            .data
        )

        competition_bouts = (
            supabase
            .table("competition_bouts")
            .select("*")
            .or_(
                f"fencer_a_id.eq.{selected_id},"
                f"fencer_b_id.eq.{selected_id}"
            )
            .order("id")
            .execute()
            .data
        )

        competition_bouts = dedupe_competition_bout_rows(
            competition_bouts
        )

        people_by_id = opponent_map()

        # ----------------------------------------------------
        # HEAD-TO-HEAD AGAINST YOU
        # ----------------------------------------------------

        st.divider()
        st.subheader("Head-to-head vs you")

        if not current_me:
            st.info(
                "Set one opponent profile as ⭐ Me first. "
                "Then competition head-to-head results can be calculated."
            )

        elif selected_id == current_me["id"]:
            st.caption(
                "This is your own profile, so head-to-head is not applicable."
            )

        else:
            my_id = current_me["id"]

            # Diary/session bouts already represent you vs the selected opponent.
            diary_bouts = (
                supabase
                .table("bouts")
                .select("*")
                .eq("opponent_id", selected_id)
                .order("created_at", desc=True)
                .execute()
                .data
            )

            diary_h2h = []

            for bout in diary_bouts:
                diary_h2h.append({
                    "source": "Diary",
                    "date": clean_text(bout.get("created_at"))[:10],
                    "competition": "",
                    "stage": "Session",
                    "my_score": bout["my_score"],
                    "their_score": bout["opponent_score"],
                    "notes": clean_text(bout.get("notes")),
                })

            # Imported competition bouts involving both you and this opponent.
            competition_h2h = []

            for bout in competition_bouts:
                a_id = bout.get("fencer_a_id")
                b_id = bout.get("fencer_b_id")

                ids = {a_id, b_id}

                if ids != {my_id, selected_id}:
                    continue

                if a_id == my_id:
                    my_score = bout["score_a"]
                    their_score = bout["score_b"]
                else:
                    my_score = bout["score_b"]
                    their_score = bout["score_a"]

                competition = competitions_by_id.get(
                    bout.get("competition_id"),
                    {},
                )

                stage = clean_text(bout.get("stage"))

                if stage == "Poule":
                    stage_label = (
                        f"Poule {clean_int(bout.get('poule_number')) or ''}"
                    ).strip()
                else:
                    stage_label = (
                        clean_text(bout.get("round_name"))
                        or stage
                        or "Competition"
                    )

                competition_h2h.append({
                    "source": "Competition",
                    "date": clean_text(
                        competition.get("competition_date")
                    ),
                    "competition": clean_text(
                        competition.get("name")
                    ),
                    "stage": stage_label,
                    "my_score": my_score,
                    "their_score": their_score,
                    "notes": "",
                })

            combined_h2h = diary_h2h + competition_h2h

            def record_summary(rows):
                wins = 0
                losses = 0
                draws = 0
                margin_total = 0

                for row in rows:
                    margin = (
                        row["my_score"]
                        - row["their_score"]
                    )

                    margin_total += margin

                    if margin > 0:
                        wins += 1
                    elif margin < 0:
                        losses += 1
                    else:
                        draws += 1

                count = len(rows)

                return {
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "count": count,
                    "win_rate": (
                        wins / count * 100
                        if count
                        else 0
                    ),
                    "avg_margin": (
                        margin_total / count
                        if count
                        else 0
                    ),
                }

            diary_stats = record_summary(diary_h2h)
            competition_stats = record_summary(competition_h2h)
            overall_stats = record_summary(combined_h2h)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric(
                    "Overall",
                    (
                        f"{overall_stats['wins']}W–"
                        f"{overall_stats['losses']}L"
                    ),
                )

            with col2:
                st.metric(
                    "Diary",
                    (
                        f"{diary_stats['wins']}W–"
                        f"{diary_stats['losses']}L"
                    ),
                )

            with col3:
                st.metric(
                    "Competition",
                    (
                        f"{competition_stats['wins']}W–"
                        f"{competition_stats['losses']}L"
                    ),
                )

            col4, col5 = st.columns(2)

            with col4:
                st.metric(
                    "Overall win rate",
                    f"{overall_stats['win_rate']:.0f}%"
                )

            with col5:
                st.metric(
                    "Overall avg margin",
                    f"{overall_stats['avg_margin']:+.2f}"
                )

            if not combined_h2h:
                st.info(
                    "No diary or imported competition bouts "
                    "against this fencer yet."
                )

            else:
                st.subheader("Recent head-to-head bouts")

                combined_h2h.sort(
                    key=lambda row: row["date"],
                    reverse=True,
                )

                for row in combined_h2h[:15]:
                    result = get_result_icon(
                        row["my_score"],
                        row["their_score"],
                    )

                    line = (
                        f"**{result} "
                        f"{row['my_score']}–"
                        f"{row['their_score']}**"
                    )

                    if row["source"] == "Competition":
                        line += (
                            f" • {row['competition']}"
                            f" • {row['stage']}"
                        )
                    else:
                        line += " • Diary/session"

                    st.write(line)

                    if row["date"]:
                        st.caption(row["date"])

                    if row["notes"]:
                        st.caption(row["notes"])

        # ----------------------------------------------------
        # COMPLETE COMPETITION HISTORY FOR THIS FENCER
        # ----------------------------------------------------

        st.divider()
        st.subheader("Competition history")

        entries_by_competition = {
            row["competition_id"]: row
            for row in competition_entries
        }

        bouts_by_competition = {}

        for bout in competition_bouts:
            competition_id = bout.get("competition_id")

            if competition_id not in bouts_by_competition:
                bouts_by_competition[competition_id] = []

            bouts_by_competition[competition_id].append(bout)

        competition_ids = set(entries_by_competition.keys())
        competition_ids.update(bouts_by_competition.keys())

        competition_history = []

        for competition_id in competition_ids:
            competition = competitions_by_id.get(competition_id)

            if not competition:
                continue

            competition_history.append(
                (
                    competition,
                    entries_by_competition.get(competition_id),
                    bouts_by_competition.get(competition_id, []),
                )
            )

        competition_history.sort(
            key=lambda item: clean_text(
                item[0].get("competition_date")
            ),
            reverse=True,
        )

        if not competition_history:
            st.info(
                "No imported competition history for this fencer yet."
            )

        else:
            for competition, entry, comp_bouts in competition_history:
                date_label = clean_text(
                    competition.get("competition_date")
                )

                event_name = clean_text(
                    competition.get("event_name")
                )

                final_place_label = ""

                if entry:
                    final_place_label = (
                        clean_text(
                            entry.get("final_place_label")
                        )
                        or (
                            str(entry["final_place"])
                            if entry.get("final_place") is not None
                            else ""
                        )
                    )

                title = (
                    f"{date_label} • {competition['name']}"
                )

                if final_place_label:
                    title += f" • #{final_place_label}"

                with st.expander(title):
                    if event_name:
                        st.write(f"**{event_name}**")

                    comp_details = [
                        clean_text(competition.get("weapon")),
                        clean_text(competition.get("level")),
                        clean_text(competition.get("location")),
                    ]
                    comp_details = [
                        x
                        for x in comp_details
                        if x
                    ]

                    if comp_details:
                        st.caption(
                            " • ".join(comp_details)
                        )

                    if entry:
                        placement_bits = []

                        if entry.get("initial_seed"):
                            placement_bits.append(
                                f"Initial seed: {entry['initial_seed']}"
                            )

                        if entry.get("de_seed"):
                            placement_bits.append(
                                f"DE seed: {entry['de_seed']}"
                            )

                        if final_place_label:
                            field_size = competition.get("field_size")

                            final_text = (
                                f"Final: {final_place_label}"
                            )

                            if field_size:
                                final_text += f" / {field_size}"

                            placement_bits.append(final_text)

                        if placement_bits:
                            st.write(
                                " • ".join(placement_bits)
                            )

                    # Orient every bout from the selected fencer's perspective.
                    oriented_bouts = []

                    for bout in comp_bouts:
                        if bout.get("fencer_a_id") == selected_id:
                            selected_score = bout["score_a"]
                            opponent_score = bout["score_b"]
                            opponent_id = bout.get("fencer_b_id")
                        else:
                            selected_score = bout["score_b"]
                            opponent_score = bout["score_a"]
                            opponent_id = bout.get("fencer_a_id")

                        opponent = people_by_id.get(opponent_id)
                        opponent_name = (
                            opponent["name"]
                            if opponent
                            else "Unknown"
                        )

                        stage = clean_text(bout.get("stage"))

                        if stage == "Poule":
                            stage_label = (
                                f"Poule "
                                f"{clean_int(bout.get('poule_number')) or ''}"
                            ).strip()
                            sort_stage = 0
                        else:
                            stage_label = (
                                clean_text(bout.get("round_name"))
                                or "DE"
                            )
                            sort_stage = 1

                        oriented_bouts.append({
                            "stage": stage,
                            "stage_label": stage_label,
                            "sort_stage": sort_stage,
                            "opponent": opponent_name,
                            "selected_score": selected_score,
                            "opponent_score": opponent_score,
                        })

                    poule_rows = [
                        row
                        for row in oriented_bouts
                        if row["stage"] == "Poule"
                    ]

                    de_rows = [
                        row
                        for row in oriented_bouts
                        if row["stage"] == "DE"
                    ]

                    if poule_rows:
                        poule_wins = sum(
                            1
                            for row in poule_rows
                            if (
                                row["selected_score"]
                                >
                                row["opponent_score"]
                            )
                        )

                        poule_losses = sum(
                            1
                            for row in poule_rows
                            if (
                                row["selected_score"]
                                <
                                row["opponent_score"]
                            )
                        )

                        st.markdown(
                            f"**Poules — "
                            f"{poule_wins}W–{poule_losses}L**"
                        )

                        for row in poule_rows:
                            result = get_result_icon(
                                row["selected_score"],
                                row["opponent_score"],
                            )

                            st.write(
                                f"{result} "
                                f"**{row['selected_score']}–"
                                f"{row['opponent_score']}** "
                                f"vs {row['opponent']} "
                                f"• {row['stage_label']}"
                            )

                    if de_rows:
                        st.markdown("**Direct elimination**")

                        for row in de_rows:
                            result = get_result_icon(
                                row["selected_score"],
                                row["opponent_score"],
                            )

                            st.write(
                                f"{result} "
                                f"**{row['selected_score']}–"
                                f"{row['opponent_score']}** "
                                f"vs {row['opponent']} "
                                f"• {row['stage_label']}"
                            )

                    if not oriented_bouts:
                        st.caption(
                            "No scored bouts imported for this competition."
                        )


# ============================================================
# COMPETITIONS
# ============================================================

elif page == "🏆 Competitions":
    st.header("Competitions")

    import_tab, manage_tab = st.tabs(
        ["📥 Import results", "🏆 Manage competitions"]
    )

    # --------------------------------------------------------
    # IMPORT TAB
    # --------------------------------------------------------

    with import_tab:
        st.subheader("Import competition results")

        st.caption(
            "Upload a CSV in the Fencing Diary import format. "
            "The importer will match existing fencers, create new ones, "
            "add seeds/final placings, and import poule and DE bouts."
        )

        uploaded_file = st.file_uploader(
            "Competition CSV",
            type=["csv"],
            key="competition_import_file",
        )

        if uploaded_file is not None:
            try:
                import_df = pd.read_csv(uploaded_file)
            except Exception as e:
                st.error(f"Could not read CSV: {e}")
                import_df = None

            if import_df is not None:
                errors = validate_import(import_df)

                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    comp, imported_fencers, imported_bouts = parse_import(import_df)

                    st.success("Import file recognised.")

                    title = comp["name"]

                    if comp.get("event_name"):
                        title += f" — {comp['event_name']}"

                    st.markdown(f"### {title}")
                    st.write(
                        f"**Date:** {comp['competition_date']}  \n"
                        f"**Weapon:** {comp['weapon']}  \n"
                        f"**Event identity:** `{competition_event_key(comp['name'], comp['competition_date'], comp['weapon'], comp['event_name'])}`  \n"
                        f"**Location:** {comp['location'] or '—'}  \n"
                        f"**Field size:** {comp['field_size'] or len(imported_fencers)}"
                    )

                    poule_count = sum(
                        1
                        for bout in imported_bouts
                        if normalize_name(bout["stage"]) == "poule"
                    )

                    de_count = sum(
                        1
                        for bout in imported_bouts
                        if normalize_name(bout["stage"]) == "de"
                    )

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Fencers", len(imported_fencers))
                    col2.metric("Poule bouts", poule_count)
                    col3.metric("DE bouts", de_count)

                    existing_comp = find_existing_competition(comp)

                    if existing_comp:
                        st.info(
                            "This competition already exists. "
                            "Importing again will update fencer results and "
                            "skip competition bouts already stored."
                        )

                    st.divider()
                    st.subheader("Fencer matching")

                    existing_opponents = get_opponents()
                    existing_names = [
                        row["name"]
                        for row in existing_opponents
                    ]

                    name_choices = {}
                    unmatched_count = 0

                    for i, fencer in enumerate(imported_fencers):
                        exact = find_exact_opponent(
                            fencer,
                            existing_opponents,
                        )

                        if exact:
                            st.write(
                                f"✅ **{fencer['name']}** → {exact['name']}"
                            )
                        else:
                            unmatched_count += 1

                            choice = st.selectbox(
                                f"{fencer['name']} "
                                f"({fencer['club'] or 'No club'})",
                                ["➕ Create new fencer"] + existing_names,
                                key=f"import_match_{i}",
                            )

                            name_choices[i] = choice

                    if unmatched_count:
                        st.caption(
                            f"{unmatched_count} imported fencer(s) need a decision. "
                            "Leave them on 'Create new fencer' unless an existing "
                            "profile is the same person."
                        )
                    else:
                        st.caption(
                            "All imported fencers matched existing profiles."
                        )

                    st.divider()

                    with st.expander("Preview fencers"):
                        preview_fencers = pd.DataFrame(imported_fencers)
                        st.dataframe(
                            preview_fencers[
                                [
                                    "name",
                                    "club",
                                    "region",
                                    "initial_seed",
                                    "de_seed",
                                    "final_place_label",
                                ]
                            ],
                            use_container_width=True,
                            hide_index=True,
                        )

                    with st.expander("Preview bouts"):
                        preview_bouts = pd.DataFrame(imported_bouts)
                        st.dataframe(
                            preview_bouts,
                            use_container_width=True,
                            hide_index=True,
                        )

                    if st.button(
                        "IMPORT COMPETITION",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            result = import_competition_data(
                                comp,
                                imported_fencers,
                                imported_bouts,
                                name_choices,
                            )

                            st.success(
                                "Competition imported successfully."
                            )

                            if result["competition_created"]:
                                st.write("✅ Competition created")
                            else:
                                st.write("✅ Existing competition updated")

                            st.write(
                                f"✅ {result['new_fencers']} new fencer profile(s) created"
                            )
                            st.write(
                                f"✅ {result['competition_fencer_adds']} fencer(s) "
                                f"added to the competition"
                            )
                            st.write(
                                f"✅ {result['competition_fencer_updates']} competition "
                                f"fencer record(s) updated"
                            )
                            st.write(
                                f"✅ {result['added_bouts']} new bout(s) imported"
                            )

                            if result["skipped_bouts"]:
                                st.write(
                                    f"↪️ {result['skipped_bouts']} duplicate bout(s) skipped"
                                )

                            st.rerun()

                        except Exception as e:
                            st.error(f"Import failed: {e}")

    # --------------------------------------------------------
    # MANAGE TAB
    # --------------------------------------------------------

    with manage_tab:
        with st.expander("➕ Create competition manually"):
            with st.form(
                "create_competition",
                clear_on_submit=True,
            ):
                competition_name = st.text_input(
                    "Competition name"
                )

                event_name = st.text_input(
                    "Event name",
                    placeholder="e.g. Open/Veteran Men's Epee",
                )

                competition_date = st.date_input(
                    "Competition date",
                    value=date.today(),
                )

                weapon = st.selectbox(
                    "Weapon",
                    ["Épée", "Foil", "Sabre"],
                    key="manual_comp_weapon",
                )

                location = st.text_input("Location")

                level = st.selectbox(
                    "Level",
                    [
                        "",
                        "Novice",
                        "Club",
                        "Open",
                        "State",
                        "National",
                        "International",
                    ],
                )

                field_size = st.number_input(
                    "Field size",
                    min_value=1,
                    value=20,
                    step=1,
                )

                create = st.form_submit_button(
                    "Create competition",
                    type="primary",
                    use_container_width=True,
                )

            if create:
                if not competition_name.strip():
                    st.error("Enter a competition name.")
                else:
                    supabase.table("competitions").insert({
                        "name": competition_name.strip(),
                        "event_name": event_name.strip() or None,
                        "event_key": competition_event_key(
                            competition_name.strip(),
                            str(competition_date),
                            weapon,
                            event_name.strip(),
                        ),
                        "competition_date": str(competition_date),
                        "weapon": weapon,
                        "location": location.strip() or None,
                        "level": level or None,
                        "field_size": int(field_size),
                    }).execute()

                    st.success("Competition created!")
                    st.rerun()

        competitions = get_competitions()

        if not competitions:
            st.info("No competitions yet.")
        else:
            competition_lookup = {}

            for competition in competitions:
                label = (
                    f"{competition['competition_date']} • "
                    f"{competition['name']}"
                )

                if competition.get("event_name"):
                    label += f" • {competition['event_name']}"

                label += f" • Event #{competition['id']}"

                competition_lookup[label] = competition

            selected_label = st.selectbox(
                "Select competition",
                list(competition_lookup.keys()),
            )

            competition = competition_lookup[selected_label]
            competition_id = competition["id"]

            st.divider()

            st.header(competition["name"])

            if competition.get("event_name"):
                st.subheader(competition["event_name"])

            details = [
                clean_text(competition.get("competition_date")),
                clean_text(competition.get("weapon")),
                clean_text(competition.get("level")),
                clean_text(competition.get("location")),
            ]
            details = [x for x in details if x]

            if details:
                st.caption(" • ".join(details))

            competition_fencers = get_competition_fencers(
                competition_id
            )

            competition_bouts = get_competition_bouts(
                competition_id
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Fencers", len(competition_fencers))
            col2.metric(
                "Poule bouts",
                sum(
                    1
                    for b in competition_bouts
                    if normalize_name(b.get("stage")) == "poule"
                ),
            )
            col3.metric(
                "DE bouts",
                sum(
                    1
                    for b in competition_bouts
                    if normalize_name(b.get("stage")) == "de"
                ),
            )

            # ------------------------------------------------
            # MANUAL FENCER MANAGEMENT
            # ------------------------------------------------

            with st.expander("➕ Add fencer / edit placing"):
                current_ids = {
                    row["opponent_id"]
                    for row in competition_fencers
                }

                all_opponents = get_opponents()

                available = [
                    person
                    for person in all_opponents
                    if person["id"] not in current_ids
                ]

                st.markdown("**Add existing fencer**")

                if available:
                    available_lookup = {
                        person["name"]: person["id"]
                        for person in available
                    }

                    existing_name = st.selectbox(
                        "Existing fencer",
                        list(available_lookup.keys()),
                        key="manual_add_existing_comp_fencer",
                    )

                    if st.button(
                        "Add existing fencer",
                        key="manual_add_existing_comp_fencer_button",
                    ):
                        supabase.table("competition_fencers").insert({
                            "competition_id": competition_id,
                            "opponent_id": available_lookup[existing_name],
                        }).execute()

                        st.success(f"{existing_name} added.")
                        st.rerun()
                else:
                    st.caption("All existing fencers are already in this competition.")

                st.markdown("**Create new fencer**")

                with st.form(
                    "manual_new_comp_fencer",
                    clear_on_submit=True,
                ):
                    new_name = st.text_input("Name")
                    new_club = st.text_input("Club")
                    new_region = st.text_input("Region / state")

                    create_and_add = st.form_submit_button(
                        "Create + add to competition"
                    )

                if create_and_add:
                    if not new_name.strip():
                        st.error("Enter a name.")
                    else:
                        result = (
                            supabase
                            .table("opponents")
                            .insert({
                                "name": new_name.strip(),
                                "club": new_club.strip() or None,
                                "region": new_region.strip() or None,
                                "handedness": "Unknown",
                                "weapon": competition["weapon"],
                            })
                            .execute()
                        )

                        new_id = result.data[0]["id"]

                        supabase.table("competition_fencers").insert({
                            "competition_id": competition_id,
                            "opponent_id": new_id,
                        }).execute()

                        st.success(f"{new_name.strip()} created and added.")
                        st.rerun()

                competition_fencers = get_competition_fencers(
                    competition_id
                )

                if competition_fencers:
                    st.divider()
                    st.markdown("**Edit seeds / final placing**")

                    editable_lookup = {
                        row["person"]["name"]: row
                        for row in competition_fencers
                        if row.get("person")
                    }

                    edit_name = st.selectbox(
                        "Fencer to edit",
                        list(editable_lookup.keys()),
                        key="edit_comp_fencer_result",
                    )

                    edit_row = editable_lookup[edit_name]

                    with st.form("edit_comp_fencer_form"):
                        initial_seed = st.number_input(
                            "Initial seed",
                            min_value=0,
                            value=int(edit_row.get("initial_seed") or 0),
                            step=1,
                        )

                        de_seed = st.number_input(
                            "DE seed",
                            min_value=0,
                            value=int(edit_row.get("de_seed") or edit_row.get("seed") or 0),
                            step=1,
                        )

                        final_place = st.number_input(
                            "Final place",
                            min_value=0,
                            value=int(edit_row.get("final_place") or 0),
                            step=1,
                        )

                        final_place_label = st.text_input(
                            "Final place label",
                            value=clean_text(edit_row.get("final_place_label")),
                            placeholder="e.g. 3T",
                        )

                        save_fencer_result = st.form_submit_button(
                            "Save fencer result"
                        )

                    if save_fencer_result:
                        supabase.table("competition_fencers").update({
                            "initial_seed": int(initial_seed) if initial_seed else None,
                            "de_seed": int(de_seed) if de_seed else None,
                            "seed": int(de_seed) if de_seed else None,
                            "final_place": int(final_place) if final_place else None,
                            "final_place_label": (
                                final_place_label.strip()
                                if final_place_label.strip()
                                else (
                                    str(int(final_place))
                                    if final_place
                                    else None
                                )
                            ),
                        }).eq(
                            "id",
                            edit_row["id"],
                        ).execute()

                        st.success("Fencer result updated.")
                        st.rerun()

            competition_fencers = get_competition_fencers(
                competition_id
            )

            st.divider()
            st.subheader("Fencers")

            ranked_rows = sorted(
                competition_fencers,
                key=lambda row: (
                    row.get("final_place")
                    if row.get("final_place") is not None
                    else 999999,
                    row.get("de_seed")
                    if row.get("de_seed") is not None
                    else 999999,
                ),
            )

            for row in ranked_rows:
                person = row.get("person")
                if not person:
                    continue

                display_name = person["name"]

                if person.get("is_me"):
                    display_name += " ⭐ You"

                pieces = [f"**{display_name}**"]

                if row.get("initial_seed"):
                    pieces.append(f"Initial seed {row['initial_seed']}")

                if row.get("de_seed"):
                    pieces.append(f"DE seed {row['de_seed']}")

                if row.get("final_place_label"):
                    pieces.append(f"Final {row['final_place_label']}")
                elif row.get("final_place"):
                    pieces.append(f"Final {row['final_place']}")

                st.write(" • ".join(pieces))

            st.divider()
            st.subheader("Poule results")

            poule_bouts = [
                b
                for b in competition_bouts
                if normalize_name(b.get("stage")) == "poule"
            ]

            poules = {}

            for bout in poule_bouts:
                number = bout.get("poule_number") or 0
                poules.setdefault(number, []).append(bout)

            if not poules:
                st.caption("No poule bouts entered.")
            else:
                for poule_number in sorted(poules):
                    st.markdown(f"**Poule {poule_number}**")

                    for bout in poules[poule_number]:
                        a = bout.get("fencer_a")
                        b = bout.get("fencer_b")

                        a_name = a["name"] if a else "Unknown"
                        b_name = b["name"] if b else "Unknown"

                        st.write(
                            f"{a_name} "
                            f"**{bout['score_a']}–{bout['score_b']}** "
                            f"{b_name}"
                        )

            st.divider()
            st.subheader("Direct elimination")

            de_bouts = [
                b
                for b in competition_bouts
                if normalize_name(b.get("stage")) == "de"
            ]

            round_order = [
                "T512",
                "T256",
                "T128",
                "T64",
                "T32",
                "T16",
                "T8",
                "QF",
                "SF",
                "Final",
            ]

            shown_ids = set()

            for round_name in round_order:
                rows = [
                    b
                    for b in de_bouts
                    if clean_text(b.get("round_name")) == round_name
                ]

                if not rows:
                    continue

                st.markdown(f"**{round_name}**")

                for bout in rows:
                    shown_ids.add(bout["id"])

                    a = bout.get("fencer_a")
                    b = bout.get("fencer_b")

                    a_name = a["name"] if a else "Unknown"
                    b_name = b["name"] if b else "Unknown"

                    st.write(
                        f"{a_name} "
                        f"**{bout['score_a']}–{bout['score_b']}** "
                        f"{b_name}"
                    )

            other_de = [
                b
                for b in de_bouts
                if b["id"] not in shown_ids
            ]

            if other_de:
                st.markdown("**Other DE rounds**")
                for bout in other_de:
                    a = bout.get("fencer_a")
                    b = bout.get("fencer_b")

                    a_name = a["name"] if a else "Unknown"
                    b_name = b["name"] if b else "Unknown"

                    st.write(
                        f"{clean_text(bout.get('round_name')) or 'DE'}: "
                        f"{a_name} "
                        f"**{bout['score_a']}–{bout['score_b']}** "
                        f"{b_name}"
                    )

            if not de_bouts:
                st.caption("No DE bouts entered.")

            st.divider()
            st.subheader("Final results")

            placed = [
                row
                for row in competition_fencers
                if row.get("final_place") is not None
            ]

            placed.sort(
                key=lambda row: (
                    row["final_place"],
                    row.get("person", {}).get("name", ""),
                )
            )

            if not placed:
                st.caption("No final placings entered.")
            else:
                for row in placed:
                    person = row.get("person")
                    if not person:
                        continue

                    place_label = (
                        row.get("final_place_label")
                        or str(row["final_place"])
                    )

                    display_name = person["name"]

                    if person.get("is_me"):
                        display_name += " ⭐ You"

                    st.write(
                        f"**{place_label}.** {display_name}"
                    )

            # ------------------------------------------------
            # MANUAL ADD BOUT FALLBACK
            # ------------------------------------------------

            st.divider()

            with st.expander("Manual result entry"):
                if len(competition_fencers) < 2:
                    st.info("Add/import at least two fencers first.")
                else:
                    fencer_name_to_id = {
                        row["person"]["name"]: row["opponent_id"]
                        for row in competition_fencers
                        if row.get("person")
                    }

                    fencer_names = list(fencer_name_to_id.keys())

                    with st.form(
                        "manual_comp_bout",
                        clear_on_submit=True,
                    ):
                        stage = st.selectbox(
                            "Stage",
                            ["Poule", "DE"],
                        )

                        poule_number = st.number_input(
                            "Poule number (use 0 for DE)",
                            min_value=0,
                            value=1,
                            step=1,
                        )

                        round_name = st.text_input(
                            "DE round",
                            placeholder="e.g. T16, T8, SF, Final",
                        )

                        fencer_a = st.selectbox(
                            "Fencer A",
                            fencer_names,
                            key="manual_result_a",
                        )

                        fencer_b = st.selectbox(
                            "Fencer B",
                            fencer_names,
                            index=1 if len(fencer_names) > 1 else 0,
                            key="manual_result_b",
                        )

                        score_a = st.number_input(
                            "Score A",
                            min_value=0,
                            max_value=50,
                            value=5,
                            step=1,
                        )

                        score_b = st.number_input(
                            "Score B",
                            min_value=0,
                            max_value=50,
                            value=3,
                            step=1,
                        )

                        save_manual_result = st.form_submit_button(
                            "Save result",
                            type="primary",
                        )

                    if save_manual_result:
                        if fencer_a == fencer_b:
                            st.error("Choose two different fencers.")
                        else:
                            supabase.table("competition_bouts").insert({
                                "competition_id": competition_id,
                                "fencer_a_id": fencer_name_to_id[fencer_a],
                                "fencer_b_id": fencer_name_to_id[fencer_b],
                                "score_a": int(score_a),
                                "score_b": int(score_b),
                                "stage": stage,
                                "poule_number": (
                                    int(poule_number)
                                    if stage == "Poule"
                                    else None
                                ),
                                "round_name": (
                                    round_name.strip()
                                    if stage == "DE"
                                    else None
                                ),
                            }).execute()

                            st.success("Result saved.")
                            st.rerun()


# ============================================================
# STRENGTH RANKINGS
# ============================================================

elif page == "📈 Strength Rankings":
    st.header("📈 Strength Rankings")

    st.caption(
        "Current-strength ranking from imported competition results. "
        "Recent performances count more strongly than older ones, and "
        "competition category affects how strongly each result moves the rating."
    )

    weapon = st.selectbox(
        "Weapon",
        [
            "Épée",
            "Foil",
            "Sabre",
        ],
        key="strength_weapon",
    )

    strength_data = (
        calculate_strength_rankings()
    )

    weapon_data = (
        strength_data[weapon]
    )

    leaderboard = (
        weapon_data["leaderboard"]
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Rated fencers",
            len(leaderboard),
        )

    with col2:
        st.metric(
            "Competition bouts",
            weapon_data["bout_count"],
        )

    with col3:
        established_count = sum(
            1
            for row in leaderboard
            if row["status"] == "Established"
        )

        st.metric(
            "Established",
            established_count,
        )

    with st.expander(
        "How the strength rating works"
    ):
        st.write(
            "Every fencer starts at **1500** for each weapon. "
            "Only imported competition bouts affect this rating; "
            "training and diary bouts do not."
        )

        st.write(
            "Poule bouts use a standard Elo update with **K = 32**. "
            "Direct-elimination bouts are weighted **20% more** because "
            "they are longer knockout bouts."
        )

        st.write(
            f"Results are also **recency weighted**. A result from today "
            f"has 100% influence; the recency component halves every "
            f"{STRENGTH_RECENCY_HALF_LIFE_DAYS} days and approaches a "
            f"{STRENGTH_RECENCY_FLOOR:.0%} floor. Old results therefore "
            "still provide evidence, but recent form drives current strength."
        )

        st.write(
            "**Competition category is weighted too.** "
            "The current multipliers are: "
            + " • ".join(
                f"{label} {weight:.0%}"
                for label, weight
                in STRENGTH_CATEGORY_WEIGHTS.items()
            )
            + ". The Open value is the baseline. This means a novice-only "
            "result still teaches the model something, but it cannot move "
            "the main strength rating as much as an equivalent Open, State, "
            "National, or International result."
        )

        st.write(
            "The effective rating movement is therefore approximately "
            "**Elo result × DE/poule weight × recency weight × competition "
            "category weight**."
        )

        st.write(
            f"The **recent form** record covers the last "
            f"{STRENGTH_RECENT_FORM_DAYS} days. Confidence is based on "
            "recency- and category-weighted effective bout volume, so a large "
            "amount of fresh Open/State/National-level data produces a more "
            "trustworthy current rating than the same amount of old or novice data."
        )

        st.write(
            "All poule bouts at a competition are treated as one rating "
            "period, and each DE round is treated as its own rating period. "
            "This prevents the arbitrary row order of a poule sheet from "
            "changing the final ratings."
        )

        st.write(
            "Final placing is displayed elsewhere but is **not scored again** "
            "in the strength rating, because the individual bouts that caused "
            "that placing are already included."
        )

        st.write(
            f"A rating is marked **Established** after "
            f"{STRENGTH_ESTABLISHED_BOUTS} competition bouts. "
            "Before that it is **Provisional**."
        )

    st.divider()

    if not leaderboard:
        st.info(
            f"No imported {weapon} competition bouts yet."
        )

    else:
        minimum_bouts = st.selectbox(
            "Minimum competition bouts",
            [1, 3, 5, 10],
            index=0,
        )

        filtered = [
            row
            for row in leaderboard
            if row["bouts"] >= minimum_bouts
        ]

        if not filtered:
            st.info(
                "No fencers meet that minimum-bout filter."
            )

        else:
            display_rows = []

            for row in filtered:
                official_rank = (
                    row["official_rank"]
                    if row.get(
                        "official_rank"
                    )
                    is not None
                    else ""
                )

                display_rows.append({
                    "Rank": row["rank"],
                    "Fencer": row["name"],
                    "Rating": round(
                        row["rating"]
                    ),
                    "Last comp Δ": (
                        f"{row['last_comp_change']:+.0f}"
                    ),
                    "Confidence": (
                        f"{row['confidence']:.0f}%"
                    ),
                    "Bouts": row["bouts"],
                    "Recent": (
                        f"{row['recent_wins']}W–"
                        f"{row['recent_losses']}L"
                    ),
                    "Record": (
                        f"{row['wins']}W–"
                        f"{row['losses']}L"
                    ),
                    "Win %": round(
                        row["win_rate"]
                    ),
                    "Status": row["status"],
                    "Club": row["club"],
                    "Region": row["region"],
                    "Official rank": official_rank,
                })

            ranking_df = pd.DataFrame(
                display_rows
            )

            st.dataframe(
                ranking_df,
                hide_index=True,
                use_container_width=True,
            )

            st.divider()
            st.subheader("Fencer rating detail")

            fencer_lookup = {
                row["name"]: row
                for row in filtered
            }

            selected_name = st.selectbox(
                "Fencer",
                list(
                    fencer_lookup.keys()
                ),
                key="strength_fencer_detail",
            )

            selected_rating = (
                fencer_lookup[
                    selected_name
                ]
            )

            col_a, col_b = st.columns(2)

            with col_a:
                st.metric(
                    "App rank",
                    f"#{selected_rating['rank']}",
                )

            with col_b:
                st.metric(
                    "Strength rating",
                    f"{selected_rating['rating']:.0f}",
                    delta=(
                        f"{selected_rating['last_comp_change']:+.0f} "
                        "last comp"
                    ),
                )

            col_c, col_d = st.columns(2)

            with col_c:
                st.metric(
                    "Competition bouts",
                    selected_rating["bouts"],
                )

            with col_d:
                st.metric(
                    "Rating confidence",
                    f"{selected_rating['confidence']:.0f}%",
                )

            st.write(
                f"**Competition record:** "
                f"{selected_rating['wins']}W–"
                f"{selected_rating['losses']}L"
                + (
                    f"–{selected_rating['draws']}D"
                    if selected_rating[
                        "draws"
                    ]
                    else ""
                )
            )

            st.write(
                f"**Recent {STRENGTH_RECENT_FORM_DAYS}-day form:** "
                f"{selected_rating['recent_wins']}W–"
                f"{selected_rating['recent_losses']}L"
                + (
                    f"–{selected_rating['recent_draws']}D"
                    if selected_rating["recent_draws"]
                    else ""
                )
            )

            st.write(
                f"**Status:** "
                f"{selected_rating['status']} • "
                f"effective bouts "
                f"{selected_rating['effective_bouts']:.1f}"
            )

            if selected_rating.get(
                "last_competition_name"
            ):
                st.caption(
                    "Latest rated competition: "
                    f"{selected_rating['last_competition_name']} "
                    f"({selected_rating['last_competition_date']})"
                )

            fencer_id = (
                selected_rating[
                    "fencer_id"
                ]
            )

            rating_history = (
                weapon_data[
                    "history"
                ].get(
                    fencer_id,
                    [],
                )
            )

            if rating_history:
                st.subheader(
                    "Rating history"
                )

                chart_rows = []

                for step, point in enumerate(
                    rating_history,
                    start=1,
                ):
                    chart_rows.append({
                        "Step": step,
                        "Rating": point[
                            "rating"
                        ],
                    })

                chart_df = pd.DataFrame(
                    chart_rows
                ).set_index("Step")

                st.line_chart(
                    chart_df["Rating"]
                )

                history_rows = []

                for point in reversed(
                    rating_history
                ):
                    history_rows.append({
                        "Date": point[
                            "date"
                        ],
                        "Competition": point[
                            "competition"
                        ],
                        "Stage": point[
                            "stage"
                        ],
                        "Rating": round(
                            point["rating"]
                        ),
                        "Change": (
                            f"{point['change']:+.1f}"
                        ),
                        "Category": point.get(
                            "category",
                            "Open",
                        ),
                        "Category weight": (
                            f"{point.get('category_weight', 1.0):.0%}"
                        ),
                        "Recency weight": (
                            f"{point.get('recency_weight', 1.0):.0%}"
                        ),
                        "Combined weight": (
                            f"{point.get('combined_weight', 1.0):.0%}"
                        ),
                    })

                st.dataframe(
                    pd.DataFrame(
                        history_rows
                    ),
                    hide_index=True,
                    use_container_width=True,
                )


# ============================================================
# SESSION HISTORY
# ============================================================

elif page == "📚 Session History":
    st.header("Session History")

    sessions = (
        supabase
        .table("sessions")
        .select("*")
        .not_.is_("ended_at", "null")
        .order("session_date", desc=True)
        .execute()
        .data
    )

    if not sessions:
        st.info("No completed sessions yet.")
    else:
        session_lookup = {}

        for session in sessions:
            label = (
                f"{session['session_date']} • "
                f"{session['session_type']} • "
                f"{session['weapon']}"
            )

            if session.get("location"):
                label += f" • {session['location']}"

            session_lookup[label] = session

        selected_label = st.selectbox(
            "Select session",
            list(session_lookup.keys()),
        )

        selected_session = session_lookup[selected_label]

        st.divider()
        st.header(selected_label)

        session_bouts = (
            supabase
            .table("bouts")
            .select("*")
            .eq("session_id", selected_session["id"])
            .order("created_at")
            .execute()
            .data
        )

        people = opponent_map()

        wins = losses = 0
        touches_for = touches_against = 0

        for bout in session_bouts:
            touches_for += bout["my_score"]
            touches_against += bout["opponent_score"]

            if bout["my_score"] > bout["opponent_score"]:
                wins += 1
            elif bout["my_score"] < bout["opponent_score"]:
                losses += 1

        col1, col2, col3 = st.columns(3)
        col1.metric("Bouts", len(session_bouts))
        col2.metric("Record", f"{wins}–{losses}")
        col3.metric("Indicator", f"{touches_for - touches_against:+d}")

        if selected_session.get("overall_rating"):
            st.write(
                f"**Overall rating:** "
                f"{selected_session['overall_rating']}/10"
            )

        if selected_session.get("what_i_learned"):
            st.subheader("What I learned")
            st.write(selected_session["what_i_learned"])

        if selected_session.get("what_to_work_on"):
            st.subheader("What to work on")
            st.write(selected_session["what_to_work_on"])

        if selected_session.get("session_notes"):
            st.subheader("Session diary")
            st.write(selected_session["session_notes"])

        st.divider()
        st.subheader("Bouts")

        for bout in session_bouts:
            person = people.get(bout["opponent_id"])
            opponent = person["name"] if person else "Unknown"

            result = get_result_icon(
                bout["my_score"],
                bout["opponent_score"],
            )

            st.write(
                f"**{result} {bout['my_score']}–{bout['opponent_score']}** "
                f"vs {opponent}"
            )

            if bout.get("notes"):
                st.caption(bout["notes"])


# ============================================================
# BOUT HISTORY
# ============================================================

elif page == "📖 Bout History":
    st.header("Bout History")

    bouts = get_bouts()

    if not bouts:
        st.info("No bouts recorded yet.")
    else:
        for bout in bouts:
            person = bout.get("opponent")
            opponent = person["name"] if person else "Unknown"

            result = get_result_icon(
                bout["my_score"],
                bout["opponent_score"],
            )

            st.subheader(
                f"{result} {bout['my_score']}–{bout['opponent_score']} "
                f"vs {opponent}"
            )

            if bout.get("created_at"):
                st.caption(bout["created_at"][:10])

            if bout.get("feeling") is not None:
                st.write(
                    f"**How well I fenced:** {bout['feeling']}/10"
                )

            if bout.get("notes"):
                st.write(bout["notes"])

            if bout.get("what_worked"):
                st.write(
                    "**Worked:** " + bout["what_worked"]
                )

            if bout.get("what_didnt"):
                st.write(
                    "**Didn't work:** " + bout["what_didnt"]
                )

            st.divider()
