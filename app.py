import streamlit as st
from supabase import create_client
from datetime import date, datetime, timezone


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Fencing Diary",
    page_icon="🤺",
    layout="centered"
)


# ============================================================
# SUPABASE CONNECTION
# ============================================================

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"]
)


# ============================================================
# TITLE
# ============================================================

st.title("🤺 Fencing Diary")


# ============================================================
# HELPERS
# ============================================================

def get_active_session():

    response = (
        supabase
        .table("sessions")
        .select("*")
        .is_("ended_at", "null")
        .order(
            "created_at",
            desc=True
        )
        .limit(1)
        .execute()
    )

    if response.data:
        return response.data[0]

    return None


def get_opponents():

    return (
        supabase
        .table("opponents")
        .select("*")
        .order("name")
        .execute()
        .data
    )


def get_bouts():

    return (
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


def get_sessions():

    return (
        supabase
        .table("sessions")
        .select("*")
        .order(
            "session_date",
            desc=True
        )
        .execute()
        .data
    )


def get_result(
    my_score,
    their_score
):

    if my_score > their_score:
        return "W"

    elif my_score < their_score:
        return "L"

    return "D"


def get_result_icon(
    my_score,
    their_score
):

    if my_score > their_score:
        return "🟢 W"

    elif my_score < their_score:
        return "🔴 L"

    return "⚪ D"


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Menu",
    [
        "🏠 Dashboard",
        "🤺 Current Session",
        "👤 Opponents",
        "📚 Session History",
        "📖 Bout History"
    ]
)


# ============================================================
# DASHBOARD
# ============================================================

if page == "🏠 Dashboard":

    st.header("Your Fencing")

    bouts = get_bouts()
    sessions = get_sessions()

    # --------------------------------------------------------
    # OVERALL STATS
    # --------------------------------------------------------

    total_bouts = len(bouts)

    wins = 0
    losses = 0
    draws = 0

    touches_for = 0
    touches_against = 0

    feeling_total = 0
    feeling_count = 0

    for bout in bouts:

        my_score = bout["my_score"]
        their_score = bout[
            "opponent_score"
        ]

        touches_for += my_score

        touches_against += (
            their_score
        )

        if my_score > their_score:

            wins += 1

        elif my_score < their_score:

            losses += 1

        else:

            draws += 1

        if (
            bout.get("feeling")
            is not None
        ):

            feeling_total += (
                bout["feeling"]
            )

            feeling_count += 1

    if total_bouts > 0:

        win_rate = (
            wins
            / total_bouts
            * 100
        )

        average_margin = (
            touches_for
            - touches_against
        ) / total_bouts

    else:

        win_rate = 0
        average_margin = 0

    if feeling_count > 0:

        average_feeling = (
            feeling_total
            / feeling_count
        )

    else:

        average_feeling = 0

    completed_sessions = [
        session
        for session in sessions
        if session.get("ended_at")
    ]

    # --------------------------------------------------------
    # TOP METRICS
    # --------------------------------------------------------

    col1, col2 = st.columns(2)

    with col1:

        st.metric(
            "Total bouts",
            total_bouts
        )

    with col2:

        st.metric(
            "Win rate",
            f"{win_rate:.0f}%"
        )

    col3, col4 = st.columns(2)

    with col3:

        st.metric(
            "Record",
            f"{wins}W – {losses}L"
        )

    with col4:

        st.metric(
            "Avg margin",
            f"{average_margin:+.2f}"
        )

    col5, col6 = st.columns(2)

    with col5:

        st.metric(
            "Sessions",
            len(
                completed_sessions
            )
        )

    with col6:

        st.metric(
            "Avg fencing",
            f"{average_feeling:.1f}/10"
        )

    st.divider()

    # --------------------------------------------------------
    # RECENT FORM
    # --------------------------------------------------------

    st.subheader("Recent form")

    recent_bouts = bouts[:10]

    if not recent_bouts:

        st.info(
            "Log some bouts to "
            "see your recent form."
        )

    else:

        result_string = ""

        recent_wins = 0
        recent_losses = 0
        recent_draws = 0

        # Reverse so oldest -> newest
        for bout in reversed(
            recent_bouts
        ):

            my_score = (
                bout["my_score"]
            )

            their_score = (
                bout[
                    "opponent_score"
                ]
            )

            if my_score > their_score:

                result_string += "🟢 "

                recent_wins += 1

            elif my_score < their_score:

                result_string += "🔴 "

                recent_losses += 1

            else:

                result_string += "⚪ "

                recent_draws += 1

        st.write(
            result_string
        )

        st.caption(
            f"Last "
            f"{len(recent_bouts)} bouts: "
            f"{recent_wins}W – "
            f"{recent_losses}L"
        )

    st.divider()

    # --------------------------------------------------------
    # MOST FENCED OPPONENTS
    # --------------------------------------------------------

    st.subheader(
        "Most fenced opponents"
    )

    opponent_stats = {}

    for bout in bouts:

        opponent_data = (
            bout.get("opponents")
        )

        if not opponent_data:
            continue

        name = (
            opponent_data["name"]
        )

        if name not in opponent_stats:

            opponent_stats[name] = {
                "bouts": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0
            }

        opponent_stats[
            name
        ]["bouts"] += 1

        if (
            bout["my_score"]
            >
            bout["opponent_score"]
        ):

            opponent_stats[
                name
            ]["wins"] += 1

        elif (
            bout["my_score"]
            <
            bout["opponent_score"]
        ):

            opponent_stats[
                name
            ]["losses"] += 1

        else:

            opponent_stats[
                name
            ]["draws"] += 1

    sorted_opponents = sorted(
        opponent_stats.items(),
        key=lambda item:
            item[1]["bouts"],
        reverse=True
    )

    if not sorted_opponents:

        st.info(
            "No opponent data yet."
        )

    else:

        for (
            name,
            stats
        ) in sorted_opponents[:5]:

            st.write(
                f"**{name}** — "
                f"{stats['bouts']} bouts • "
                f"{stats['wins']}W–"
                f"{stats['losses']}L"
            )

    st.divider()

    # --------------------------------------------------------
    # LATEST BOUTS
    # --------------------------------------------------------

    st.subheader(
        "Latest bouts"
    )

    if not bouts:

        st.info(
            "No bouts yet."
        )

    else:

        for bout in bouts[:5]:

            opponent_data = (
                bout.get(
                    "opponents"
                )
            )

            if opponent_data:

                opponent = (
                    opponent_data[
                        "name"
                    ]
                )

            else:

                opponent = (
                    "Unknown opponent"
                )

            my_score = (
                bout["my_score"]
            )

            their_score = (
                bout[
                    "opponent_score"
                ]
            )

            result = (
                get_result_icon(
                    my_score,
                    their_score
                )
            )

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

    st.divider()

    # --------------------------------------------------------
    # RECENT SESSIONS
    # --------------------------------------------------------

    st.subheader(
        "Recent sessions"
    )

    if not completed_sessions:

        st.info(
            "No completed sessions yet."
        )

    else:

        for session in (
            completed_sessions[:5]
        ):

            text = (
                f"**"
                f"{session['session_date']}"
                f"** — "
                f"{session['session_type']}"
                f" • "
                f"{session['weapon']}"
            )

            if session.get(
                "location"
            ):

                text += (
                    f" • "
                    f"{session['location']}"
                )

            st.write(text)

            if session.get(
                "overall_rating"
            ):

                st.caption(
                    f"Session rating: "
                    f"{session['overall_rating']}"
                    f"/10"
                )


# ============================================================
# CURRENT SESSION
# ============================================================

elif page == "🤺 Current Session":

    active_session = (
        get_active_session()
    )

    # --------------------------------------------------------
    # NO ACTIVE SESSION
    # --------------------------------------------------------

    if active_session is None:

        st.header(
            "Start fencing"
        )

        with st.form(
            "start_session"
        ):

            session_date = (
                st.date_input(
                    "Date",
                    value=date.today()
                )
            )

            session_type = (
                st.selectbox(
                    "Session type",
                    [
                        "Training",
                        "Lesson",
                        "Competition"
                    ]
                )
            )

            weapon = st.selectbox(
                "Weapon",
                [
                    "Épée",
                    "Foil",
                    "Sabre"
                ]
            )

            location = (
                st.text_input(
                    "Location"
                )
            )

            col1, col2 = (
                st.columns(2)
            )

            with col1:

                energy_before = (
                    st.slider(
                        "Energy",
                        1,
                        10,
                        5
                    )
                )

            with col2:

                confidence_before = (
                    st.slider(
                        "Confidence",
                        1,
                        10,
                        5
                    )
                )

            start_session = (
                st.form_submit_button(
                    "🤺 START SESSION",
                    type="primary",
                    use_container_width=True
                )
            )

        if start_session:

            supabase.table(
                "sessions"
            ).insert({

                "session_date":
                    str(session_date),

                "session_type":
                    session_type,

                "weapon":
                    weapon,

                "location":
                    location.strip(),

                "energy_before":
                    energy_before,

                "confidence_before":
                    confidence_before

            }).execute()

            st.rerun()

    # --------------------------------------------------------
    # ACTIVE SESSION
    # --------------------------------------------------------

    else:

        st.caption(
            f"{active_session['session_type']}"
            f" • "
            f"{active_session['weapon']}"
            f" • "
            f"{active_session['session_date']}"
        )

        if active_session.get(
            "location"
        ):

            st.caption(
                f"📍 "
                f"{active_session['location']}"
            )

        # ----------------------------------------------------
        # GET SESSION BOUTS
        # ----------------------------------------------------

        session_bouts = (
            supabase
            .table("bouts")
            .select(
                "*, opponents(name)"
            )
            .eq(
                "session_id",
                active_session["id"]
            )
            .order("created_at")
            .execute()
            .data
        )

        wins = 0
        losses = 0
        draws = 0

        touches_for = 0
        touches_against = 0

        for bout in session_bouts:

            my_score = (
                bout["my_score"]
            )

            their_score = (
                bout[
                    "opponent_score"
                ]
            )

            touches_for += (
                my_score
            )

            touches_against += (
                their_score
            )

            if (
                my_score
                >
                their_score
            ):

                wins += 1

            elif (
                my_score
                <
                their_score
            ):

                losses += 1

            else:

                draws += 1

        # ----------------------------------------------------
        # SESSION SCOREBOARD
        # ----------------------------------------------------

        st.subheader(
            "Current session"
        )

        col1, col2, col3 = (
            st.columns(3)
        )

        with col1:

            st.metric(
                "Bouts",
                len(
                    session_bouts
                )
            )

        with col2:

            st.metric(
                "Record",
                f"{wins}–{losses}"
            )

        with col3:

            indicator = (
                touches_for
                - touches_against
            )

            st.metric(
                "Indicator",
                f"{indicator:+d}"
            )

        st.divider()

        # ----------------------------------------------------
        # QUICK BOUT LOGGER
        # ----------------------------------------------------

        st.header(
            "⚡ Log Bout"
        )

        opponents = (
            get_opponents()
        )

        if not opponents:

            st.warning(
                "Add an opponent first."
            )

        else:

            opponent_lookup = {
                person["name"]:
                    person["id"]

                for person
                in opponents
            }

            with st.form(
                "quick_bout",
                clear_on_submit=True
            ):

                opponent_name = (
                    st.selectbox(
                        "Opponent",
                        list(
                            opponent_lookup.keys()
                        )
                    )
                )

                left, middle, right = (
                    st.columns(
                        [4, 1, 4]
                    )
                )

                with left:

                    my_score = (
                        st.number_input(
                            "You",
                            min_value=0,
                            max_value=50,
                            value=5,
                            step=1
                        )
                    )

                with middle:

                    st.markdown(
                        "<h2 "
                        "style='text-align:center;"
                        "padding-top:22px;'>"
                        "–"
                        "</h2>",
                        unsafe_allow_html=True
                    )

                with right:

                    opponent_score = (
                        st.number_input(
                            "Them",
                            min_value=0,
                            max_value=50,
                            value=3,
                            step=1
                        )
                    )

                feeling = (
                    st.slider(
                        "How well did you fence?",
                        1,
                        10,
                        5
                    )
                )

                quick_note = (
                    st.text_area(
                        "Quick note",
                        placeholder=(
                            "e.g. Distance good. "
                            "Disengage worked."
                        ),
                        height=80
                    )
                )

                with st.expander(
                    "Detailed notes"
                ):

                    what_worked = (
                        st.text_area(
                            "What worked?"
                        )
                    )

                    what_didnt = (
                        st.text_area(
                            "What didn't work?"
                        )
                    )

                save_bout = (
                    st.form_submit_button(
                        "SAVE + NEXT BOUT",
                        type="primary",
                        use_container_width=True
                    )
                )

            if save_bout:

                supabase.table(
                    "bouts"
                ).insert({

                    "session_id":
                        active_session[
                            "id"
                        ],

                    "opponent_id":
                        opponent_lookup[
                            opponent_name
                        ],

                    "my_score":
                        int(
                            my_score
                        ),

                    "opponent_score":
                        int(
                            opponent_score
                        ),

                    "feeling":
                        int(
                            feeling
                        ),

                    "what_worked":
                        what_worked,

                    "what_didnt":
                        what_didnt,

                    "notes":
                        quick_note

                }).execute()

                st.toast(
                    f"Saved "
                    f"{my_score}–"
                    f"{opponent_score} "
                    f"vs "
                    f"{opponent_name}",
                    icon="🤺"
                )

                st.rerun()

        # ----------------------------------------------------
        # SESSION BOUT LIST
        # ----------------------------------------------------

        if session_bouts:

            st.divider()

            st.subheader(
                "This session"
            )

            for bout in reversed(
                session_bouts
            ):

                opponent_data = (
                    bout.get(
                        "opponents"
                    )
                )

                if opponent_data:

                    opponent = (
                        opponent_data[
                            "name"
                        ]
                    )

                else:

                    opponent = (
                        "Unknown"
                    )

                my_score = (
                    bout["my_score"]
                )

                their_score = (
                    bout[
                        "opponent_score"
                    ]
                )

                result = (
                    get_result_icon(
                        my_score,
                        their_score
                    )
                )

                st.write(
                    f"**{result} "
                    f"{my_score}–"
                    f"{their_score}** "
                    f"vs "
                    f"{opponent}"
                )

                if bout.get(
                    "notes"
                ):

                    st.caption(
                        bout[
                            "notes"
                        ]
                    )

        # ----------------------------------------------------
        # END SESSION
        # ----------------------------------------------------

        st.divider()

        with st.expander(
            "🏁 Finish session"
        ):

            with st.form(
                "finish_session"
            ):

                overall_rating = (
                    st.slider(
                        "Overall session",
                        1,
                        10,
                        5
                    )
                )

                what_i_learned = (
                    st.text_area(
                        "What did you learn?"
                    )
                )

                what_to_work_on = (
                    st.text_area(
                        "What should you work on?"
                    )
                )

                session_notes = (
                    st.text_area(
                        "General session diary",
                        height=120
                    )
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


# ============================================================
# OPPONENTS
# ============================================================

elif page == "👤 Opponents":

    st.header(
        "Opponents"
    )

    # --------------------------------------------------------
    # ADD OPPONENT
    # --------------------------------------------------------

    with st.expander(
        "➕ Add opponent"
    ):

        with st.form(
            "add_opponent",
            clear_on_submit=True
        ):

            name = (
                st.text_input(
                    "Name"
                )
            )

            club = (
                st.text_input(
                    "Club"
                )
            )

            handedness = (
                st.selectbox(
                    "Handedness",
                    [
                        "Unknown",
                        "Right",
                        "Left"
                    ]
                )
            )

            weapon = (
                st.selectbox(
                    "Weapon",
                    [
                        "Épée",
                        "Foil",
                        "Sabre"
                    ]
                )
            )

            notes = (
                st.text_area(
                    "Opponent notes"
                )
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
                            weapon,

                        "notes":
                            notes

                    }).execute()

                    st.success(
                        f"{name.strip()} "
                        f"added!"
                    )

                    st.rerun()

                except Exception as e:

                    st.error(
                        f"Couldn't add "
                        f"opponent: "
                        f"{e}"
                    )

    # --------------------------------------------------------
    # VIEW OPPONENT
    # --------------------------------------------------------

    opponents = (
        get_opponents()
    )

    if not opponents:

        st.info(
            "No opponents yet."
        )

    else:

        opponent_names = [
            person["name"]
            for person
            in opponents
        ]

        selected_name = (
            st.selectbox(
                "View opponent",
                opponent_names
            )
        )

        selected = next(
            person
            for person
            in opponents
            if (
                person["name"]
                ==
                selected_name
            )
        )

        st.divider()

        st.header(
            selected[
                "name"
            ]
        )

        details = []

        if selected.get(
            "club"
        ):

            details.append(
                selected[
                    "club"
                ]
            )

        if selected.get(
            "handedness"
        ):

            details.append(
                selected[
                    "handedness"
                ]
            )

        if selected.get(
            "weapon"
        ):

            details.append(
                selected[
                    "weapon"
                ]
            )

        if details:

            st.caption(
                " • ".join(
                    details
                )
            )

        if selected.get(
            "notes"
        ):

            st.write(
                "**Opponent notes**"
            )

            st.write(
                selected[
                    "notes"
                ]
            )

        # ----------------------------------------------------
        # HEAD-TO-HEAD
        # ----------------------------------------------------

        opponent_bouts = (
            supabase
            .table("bouts")
            .select("*")
            .eq(
                "opponent_id",
                selected[
                    "id"
                ]
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
            .data
        )

        st.subheader(
            "Head-to-head"
        )

        if not opponent_bouts:

            st.info(
                "No bouts recorded "
                "against this opponent."
            )

        else:

            wins = 0
            losses = 0
            draws = 0

            total_margin = 0

            for bout in (
                opponent_bouts
            ):

                margin = (
                    bout[
                        "my_score"
                    ]
                    -
                    bout[
                        "opponent_score"
                    ]
                )

                total_margin += (
                    margin
                )

                if margin > 0:

                    wins += 1

                elif margin < 0:

                    losses += 1

                else:

                    draws += 1

            bout_count = len(
                opponent_bouts
            )

            average_margin = (
                total_margin
                / bout_count
            )

            win_rate = (
                wins
                / bout_count
                * 100
            )

            col1, col2 = (
                st.columns(2)
            )

            with col1:

                st.metric(
                    "Record",
                    f"{wins}W – "
                    f"{losses}L"
                )

            with col2:

                st.metric(
                    "Win rate",
                    f"{win_rate:.0f}%"
                )

            col3, col4 = (
                st.columns(2)
            )

            with col3:

                st.metric(
                    "Avg margin",
                    f"{average_margin:+.2f}"
                )

            with col4:

                st.metric(
                    "Total bouts",
                    bout_count
                )

            # -----------------------------------------------
            # LAST 5
            # -----------------------------------------------

            st.subheader(
                "Last 5"
            )

            last_five = (
                opponent_bouts[:5]
            )

            form = ""

            for bout in reversed(
                last_five
            ):

                if (
                    bout["my_score"]
                    >
                    bout[
                        "opponent_score"
                    ]
                ):

                    form += "🟢 "

                elif (
                    bout["my_score"]
                    <
                    bout[
                        "opponent_score"
                    ]
                ):

                    form += "🔴 "

                else:

                    form += "⚪ "

            st.write(form)

            # -----------------------------------------------
            # RECENT BOUTS
            # -----------------------------------------------

            st.subheader(
                "Recent bouts"
            )

            for bout in (
                opponent_bouts[:10]
            ):

                my_score = (
                    bout[
                        "my_score"
                    ]
                )

                their_score = (
                    bout[
                        "opponent_score"
                    ]
                )

                result = (
                    get_result_icon(
                        my_score,
                        their_score
                    )
                )

                st.write(
                    f"**{result} "
                    f"{my_score}–"
                    f"{their_score}**"
                )

                if bout.get(
                    "notes"
                ):

                    st.caption(
                        bout[
                            "notes"
                        ]
                    )


# ============================================================
# SESSION HISTORY
# ============================================================

elif page == "📚 Session History":

    st.header(
        "Session History"
    )

    sessions = (
        supabase
        .table("sessions")
        .select("*")
        .not_.is_(
            "ended_at",
            "null"
        )
        .order(
            "session_date",
            desc=True
        )
        .execute()
        .data
    )

    if not sessions:

        st.info(
            "No completed "
            "sessions yet."
        )

    else:

        session_lookup = {}

        for session in (
            sessions
        ):

            label = (
                f"{session['session_date']}"
                f" • "
                f"{session['session_type']}"
                f" • "
                f"{session['weapon']}"
            )

            if session.get(
                "location"
            ):

                label += (
                    f" • "
                    f"{session['location']}"
                )

            session_lookup[
                label
            ] = session

        selected_label = (
            st.selectbox(
                "Select session",
                list(
                    session_lookup.keys()
                )
            )
        )

        selected_session = (
            session_lookup[
                selected_label
            ]
        )

        st.divider()

        st.header(
            selected_label
        )

        session_bouts = (
            supabase
            .table("bouts")
            .select(
                "*, opponents(name)"
            )
            .eq(
                "session_id",
                selected_session[
                    "id"
                ]
            )
            .order(
                "created_at"
            )
            .execute()
            .data
        )

        wins = 0
        losses = 0
        draws = 0

        touches_for = 0
        touches_against = 0

        for bout in (
            session_bouts
        ):

            touches_for += (
                bout[
                    "my_score"
                ]
            )

            touches_against += (
                bout[
                    "opponent_score"
                ]
            )

            if (
                bout[
                    "my_score"
                ]
                >
                bout[
                    "opponent_score"
                ]
            ):

                wins += 1

            elif (
                bout[
                    "my_score"
                ]
                <
                bout[
                    "opponent_score"
                ]
            ):

                losses += 1

            else:

                draws += 1

        col1, col2, col3 = (
            st.columns(3)
        )

        with col1:

            st.metric(
                "Bouts",
                len(
                    session_bouts
                )
            )

        with col2:

            st.metric(
                "Record",
                f"{wins}–{losses}"
            )

        with col3:

            indicator = (
                touches_for
                -
                touches_against
            )

            st.metric(
                "Indicator",
                f"{indicator:+d}"
            )

        # ----------------------------------------------------
        # SESSION NOTES
        # ----------------------------------------------------

        if selected_session.get(
            "overall_rating"
        ):

            st.write(
                "**Overall rating:** "
                f"{selected_session['overall_rating']}"
                f"/10"
            )

        if selected_session.get(
            "energy_before"
        ):

            st.write(
                "**Starting energy:** "
                f"{selected_session['energy_before']}"
                f"/10"
            )

        if selected_session.get(
            "confidence_before"
        ):

            st.write(
                "**Starting confidence:** "
                f"{selected_session['confidence_before']}"
                f"/10"
            )

        if selected_session.get(
            "what_i_learned"
        ):

            st.subheader(
                "What I learned"
            )

            st.write(
                selected_session[
                    "what_i_learned"
                ]
            )

        if selected_session.get(
            "what_to_work_on"
        ):

            st.subheader(
                "What to work on"
            )

            st.write(
                selected_session[
                    "what_to_work_on"
                ]
            )

        if selected_session.get(
            "session_notes"
        ):

            st.subheader(
                "Session diary"
            )

            st.write(
                selected_session[
                    "session_notes"
                ]
            )

        # ----------------------------------------------------
        # SESSION BOUTS
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "Bouts"
        )

        if not session_bouts:

            st.info(
                "No bouts recorded "
                "in this session."
            )

        else:

            for bout in (
                session_bouts
            ):

                opponent_data = (
                    bout.get(
                        "opponents"
                    )
                )

                if opponent_data:

                    opponent = (
                        opponent_data[
                            "name"
                        ]
                    )

                else:

                    opponent = (
                        "Unknown"
                    )

                my_score = (
                    bout[
                        "my_score"
                    ]
                )

                their_score = (
                    bout[
                        "opponent_score"
                    ]
                )

                result = (
                    get_result_icon(
                        my_score,
                        their_score
                    )
                )

                st.write(
                    f"**{result} "
                    f"{my_score}–"
                    f"{their_score}** "
                    f"vs "
                    f"{opponent}"
                )

                if bout.get(
                    "notes"
                ):

                    st.caption(
                        bout[
                            "notes"
                        ]
                    )


# ============================================================
# BOUT HISTORY
# ============================================================

elif page == "📖 Bout History":

    st.header(
        "Bout History"
    )

    bouts = (
        get_bouts()
    )

    if not bouts:

        st.info(
            "No bouts recorded yet."
        )

    else:

        for bout in bouts:

            opponent_data = (
                bout.get(
                    "opponents"
                )
            )

            if opponent_data:

                opponent = (
                    opponent_data[
                        "name"
                    ]
                )

            else:

                opponent = (
                    "Unknown opponent"
                )

            my_score = (
                bout[
                    "my_score"
                ]
            )

            their_score = (
                bout[
                    "opponent_score"
                ]
            )

            result = (
                get_result_icon(
                    my_score,
                    their_score
                )
            )

            st.subheader(
                f"{result} "
                f"{my_score}–"
                f"{their_score} "
                f"vs "
                f"{opponent}"
            )

            if bout.get(
                "created_at"
            ):

                created = (
                    bout[
                        "created_at"
                    ][:10]
                )

                st.caption(
                    created
                )

            if bout.get(
                "feeling"
            ) is not None:

                st.write(
                    "**How well I fenced:** "
                    f"{bout['feeling']}/10"
                )

            if bout.get(
                "notes"
            ):

                st.write(
                    bout[
                        "notes"
                    ]
                )

            if bout.get(
                "what_worked"
            ):

                st.write(
                    "**Worked:** "
                    +
                    bout[
                        "what_worked"
                    ]
                )

            if bout.get(
                "what_didnt"
            ):

                st.write(
                    "**Didn't work:** "
                    +
                    bout[
                        "what_didnt"
                    ]
                )

            st.divider()