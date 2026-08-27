import streamlit as st
from supabase import create_client
from datetime import date, datetime, timezone

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

st.set_page_config(
    page_title="Fencing Diary",
    page_icon="🤺",
    layout="centered"
)

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"]
)

st.title("🤺 Fencing Diary")


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

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

    if response.data:
        return response.data[0]

    return None


def get_opponents():
    response = (
        supabase
        .table("opponents")
        .select("id,name")
        .order("name")
        .execute()
    )

    return response.data


# --------------------------------------------------
# NAVIGATION
# --------------------------------------------------

page = st.sidebar.radio(
    "Menu",
    [
        "🤺 Current Session",
        "👤 Opponents",
        "📖 Bout History"
    ]
)


# ==================================================
# CURRENT SESSION
# ==================================================

if page == "🤺 Current Session":

    active_session = get_active_session()

    # ----------------------------------------------
    # NO ACTIVE SESSION
    # ----------------------------------------------

    if active_session is None:

        st.header("Start fencing")

        with st.form("start_session"):

            session_date = st.date_input(
                "Date",
                value=date.today()
            )

            session_type = st.selectbox(
                "Session type",
                [
                    "Training",
                    "Lesson",
                    "Competition"
                ]
            )

            weapon = st.selectbox(
                "Weapon",
                [
                    "Épée",
                    "Foil",
                    "Sabre"
                ]
            )

            location = st.text_input(
                "Location"
            )

            col1, col2 = st.columns(2)

            with col1:
                energy_before = st.slider(
                    "Energy",
                    1,
                    10,
                    5
                )

            with col2:
                confidence_before = st.slider(
                    "Confidence",
                    1,
                    10,
                    5
                )

            start = st.form_submit_button(
                "🤺 START SESSION",
                type="primary",
                use_container_width=True
            )

        if start:

            supabase.table("sessions").insert({
                "session_date": str(session_date),
                "session_type": session_type,
                "weapon": weapon,
                "location": location.strip(),
                "energy_before": energy_before,
                "confidence_before": confidence_before
            }).execute()

            st.rerun()


    # ----------------------------------------------
    # ACTIVE SESSION
    # ----------------------------------------------

    else:

        st.caption(
            f"{active_session['session_type']} • "
            f"{active_session['weapon']} • "
            f"{active_session['session_date']}"
        )

        if active_session.get("location"):
            st.caption(
                f"📍 {active_session['location']}"
            )

        # ------------------------------------------
        # LOAD CURRENT BOUTS
        # ------------------------------------------

        bouts_response = (
            supabase
            .table("bouts")
            .select("*, opponents(name)")
            .eq(
                "session_id",
                active_session["id"]
            )
            .order("created_at")
            .execute()
        )

        session_bouts = bouts_response.data

        wins = 0
        losses = 0
        draws = 0
        touches_for = 0
        touches_against = 0

        for bout in session_bouts:

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

        # ------------------------------------------
        # SESSION SCOREBOARD
        # ------------------------------------------

        st.subheader("Current session")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "Bouts",
                len(session_bouts)
            )

        with col2:
            st.metric(
                "Record",
                f"{wins}–{losses}"
            )

        with col3:
            indicator = touches_for - touches_against

            st.metric(
                "Indicator",
                f"{indicator:+d}"
            )

        st.divider()

        # ------------------------------------------
        # QUICK BOUT LOGGER
        # ------------------------------------------

        st.header("⚡ Log Bout")

        opponents = get_opponents()

        if not opponents:

            st.warning(
                "You need to add an opponent first."
            )

        else:

            opponent_lookup = {
                person["name"]: person["id"]
                for person in opponents
            }

            with st.form(
                "quick_bout",
                clear_on_submit=True
            ):

                opponent_name = st.selectbox(
                    "Opponent",
                    list(opponent_lookup.keys())
                )

                score1, middle, score2 = st.columns(
                    [4, 1, 4]
                )

                with score1:

                    my_score = st.number_input(
                        "You",
                        min_value=0,
                        max_value=50,
                        value=5,
                        step=1
                    )

                with middle:

                    st.markdown(
                        "<h2 style='text-align:center; "
                        "padding-top:22px;'>–</h2>",
                        unsafe_allow_html=True
                    )

                with score2:

                    opponent_score = st.number_input(
                        "Them",
                        min_value=0,
                        max_value=50,
                        value=3,
                        step=1
                    )

                feeling = st.slider(
                    "How well did you fence?",
                    1,
                    10,
                    5
                )

                quick_note = st.text_area(
                    "Quick note",
                    placeholder=(
                        "e.g. Distance good. "
                        "Disengage worked well."
                    ),
                    height=80
                )

                with st.expander(
                    "Add detailed notes"
                ):

                    what_worked = st.text_area(
                        "What worked?"
                    )

                    what_didnt = st.text_area(
                        "What didn't work?"
                    )

                save_bout = st.form_submit_button(
                    "SAVE + NEXT BOUT",
                    type="primary",
                    use_container_width=True
                )

            if save_bout:

                supabase.table("bouts").insert({
                    "session_id":
                        active_session["id"],

                    "opponent_id":
                        opponent_lookup[
                            opponent_name
                        ],

                    "my_score":
                        int(my_score),

                    "opponent_score":
                        int(opponent_score),

                    "feeling":
                        int(feeling),

                    "what_worked":
                        what_worked,

                    "what_didnt":
                        what_didnt,

                    "notes":
                        quick_note
                }).execute()

                st.toast(
                    f"Saved {my_score}–"
                    f"{opponent_score} "
                    f"vs {opponent_name}",
                    icon="🤺"
                )

                st.rerun()

        # ------------------------------------------
        # RECENT BOUTS
        # ------------------------------------------

        if session_bouts:

            st.divider()

            st.subheader("This session")

            # newest first
            for bout in reversed(
                session_bouts
            ):

                opponent = (
                    bout["opponents"]["name"]
                )

                my_score = bout["my_score"]

                their_score = (
                    bout["opponent_score"]
                )

                if my_score > their_score:
                    result = "🟢 W"

                elif my_score < their_score:
                    result = "🔴 L"

                else:
                    result = "⚪ D"

                st.write(
                    f"**{result} "
                    f"{my_score}–"
                    f"{their_score}** "
                    f"vs {opponent}"
                )

                if bout.get("notes"):

                    st.caption(
                        bout["notes"]
                    )

        # ------------------------------------------
        # END SESSION
        # ------------------------------------------

        st.divider()

        with st.expander(
            "🏁 Finish session"
        ):

            with st.form(
                "finish_session"
            ):

                overall_rating = st.slider(
                    "Overall session",
                    1,
                    10,
                    5
                )

                what_i_learned = st.text_area(
                    "What did you learn?"
                )

                what_to_work_on = st.text_area(
                    "What should you work on?"
                )

                session_notes = st.text_area(
                    "General session diary",
                    height=120
                )

                end_session = (
                    st.form_submit_button(
                        "END SESSION",
                        use_container_width=True
                    )
                )

            if end_session:

                supabase.table(
                    "sessions"
                ).update({

                    "overall_rating":
                        overall_rating,

                    "what_i_learned":
                        what_i_learned,

                    "what_to_work_on":
                        what_to_work_on,

                    "session_notes":
                        session_notes,

                    "ended_at":
                        datetime.now(
                            timezone.utc
                        ).isoformat()

                }).eq(
                    "id",
                    active_session["id"]
                ).execute()

                st.success(
                    "Session saved!"
                )

                st.rerun()


# ==================================================
# OPPONENTS
# ==================================================

elif page == "👤 Opponents":

    st.header("Opponents")

    with st.expander(
        "➕ Add opponent",
        expanded=False
    ):

        with st.form(
            "add_opponent",
            clear_on_submit=True
        ):

            name = st.text_input(
                "Name"
            )

            club = st.text_input(
                "Club"
            )

            handedness = st.selectbox(
                "Handedness",
                [
                    "Unknown",
                    "Right",
                    "Left"
                ]
            )

            weapon = st.selectbox(
                "Weapon",
                [
                    "Épée",
                    "Foil",
                    "Sabre"
                ]
            )

            add_opponent = (
                st.form_submit_button(
                    "Add opponent",
                    type="primary",
                    use_container_width=True
                )
            )

        if add_opponent:

            if not name.strip():

                st.error(
                    "Enter a name."
                )

            else:

                try:

                    supabase.table(
                        "opponents"
                    ).insert({

                        "name":
                            name.strip(),

                        "club":
                            club.strip(),

                        "handedness":
                            handedness,

                        "weapon":
                            weapon

                    }).execute()

                    st.success(
                        f"{name.strip()} added!"
                    )

                    st.rerun()

                except Exception as e:

                    st.error(
                        f"Couldn't add "
                        f"opponent: {e}"
                    )

    opponents = (
        supabase
        .table("opponents")
        .select("*")
        .order("name")
        .execute()
        .data
    )

    if not opponents:

        st.info(
            "No opponents yet."
        )

    for person in opponents:

        st.subheader(
            person["name"]
        )

        details = []

        if person.get("club"):
            details.append(
                person["club"]
            )

        if person.get("handedness"):
            details.append(
                person["handedness"]
            )

        if person.get("weapon"):
            details.append(
                person["weapon"]
            )

        if details:
            st.caption(
                " • ".join(details)
            )

        st.divider()


# ==================================================
# BOUT HISTORY
# ==================================================

elif page == "📖 Bout History":

    st.header("Bout History")

    bouts = (
        supabase
        .table("bouts")
        .select(
            "*, opponents(name)"
        )
        .order(
            "created_at",
            desc=True
        )
        .execute()
        .data
    )

    if not bouts:

        st.info(
            "No bouts recorded yet."
        )

    for bout in bouts:

        opponent = (
            bout["opponents"]["name"]
        )

        my_score = bout["my_score"]

        their_score = (
            bout["opponent_score"]
        )

        if my_score > their_score:
            result = "🟢 W"

        elif my_score < their_score:
            result = "🔴 L"

        else:
            result = "⚪ D"

        st.subheader(
            f"{result} "
            f"{my_score}–{their_score} "
            f"vs {opponent}"
        )

        st.caption(
            f"Fencing rating: "
            f"{bout['feeling']}/10"
        )

        if bout.get("notes"):

            st.write(
                bout["notes"]
            )

        if bout.get("what_worked"):

            st.write(
                "**Worked:** "
                + bout["what_worked"]
            )

        if bout.get("what_didnt"):

            st.write(
                "**Didn't work:** "
                + bout["what_didnt"]
            )

        st.divider()