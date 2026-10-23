"""
חיפוש רוחבי במשרד.

דורש מסד נתונים חי עם נתוני הזרע.

הפער שנסגר: מסמך, משימה ודרישה היו נגישים דרך התיק בלבד,
ולכן "איפה ה-EMG של ישראל" חייב לפתוח תיקים אחד אחד.

הבדיקות כאן נועלות שלושה דברים: שהחיפוש באמת חוצה מקורות,
שהוא אינו חוצה משרדים, ושאין בו AI ולא תוכן מסמכים.
"""

import pytest
from starlette.testclient import TestClient

from server.app import app
from server.db.pool import cursor

# ה-fixture חי ב-test_firm_isolation ולא ב-conftest. הייבוא
# הוא מה שהופך אותו לזמין כאן, ולכן הוא אינו מיותר.
from .test_firm_isolation import second_firm  # noqa: F401

LOCAL = ("127.0.0.1", 45123)

STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


def find(api, q, kinds=None):
    url = "/api/office/search?q=%s" % q
    if kinds:
        url += "&kinds=" + kinds
    r = api.get(url)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================
#  1. החיפוש חוצה מקורות
# ================================================================

def test_search_finds_a_document_by_name(api, temp_case):
    """
    זו הסיבה שהנקודה נבנתה: מסמך נמצא בלי לדעת באיזה תיק הוא.
    """
    staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, position)
               values (%s, %s, 'בדיקת EMG יד ימין', 90) returning id""",
            (temp_case["firm_id"], temp_case["case_id"]))
        doc_id = cur.fetchone()["id"]

    hits = find(api, "EMG")["results"]
    mine = [h for h in hits if h["id"] == str(doc_id)]
    assert mine, "המסמך לא נמצא בחיפוש רוחבי"

    hit = mine[0]
    assert hit["kind"] == "document"
    assert hit["caseId"] == str(temp_case["case_id"])
    assert hit["tab"] == "docs", "התוצאה חייבת לדעת לאיזו לשונית לקפוץ"
    assert hit["clientName"], "בלי שם הלקוח התוצאה חסרת הקשר"

    with cursor(commit=True) as cur:
        cur.execute("delete from case_documents where id = %s", (doc_id,))


def test_search_covers_every_source(api):
    """
    הקטלוג והתוצאות חייבים להסכים: כל סוג שהקטלוג מכריז עליו
    הוא סוג שאפשר באמת לבקש.

    27.09: המקורות גדלו מחמישה לשמונה. החלטה, עדכון ללקוח
    ושיחה הם נתונים שכבר קיימים במסד, והצוות שואל עליהם -
    "אילו תיקים נדחו", "מה כתבנו ללקוח" - ולא הייתה שום דרך
    למצוא אותם.
    """
    staff_login(api)
    kinds = [k["kind"] for k in api.get("/api/office/search/kinds").json()["kinds"]]
    assert set(kinds) == {"case", "client", "document", "task", "requirement",
                          "decision", "message", "conversation"}

    for kind in kinds:
        data = find(api, "a", kinds=kind)
        assert "results" in data
        assert "groups" in data, "החוזה המקובץ חסר עבור %s" % kind


def test_kinds_narrows_the_search(api):
    """
    הפרמטר קיים כדי ששכבת השפה הטבעית העתידית תשתמש באותה
    נקודת קצה. אם הוא אינו מסנן באמת, היא תצטרך נתיב משלה.
    """
    staff_login(api)
    only_docs = find(api, "ייפוי", kinds="document")["results"]
    assert only_docs, "אין נתוני זרע לבדיקה הזאת"
    assert {h["kind"] for h in only_docs} == {"document"}


# ================================================================
#  2. בידוד, הרשאות וקלט
# ================================================================

def test_search_never_crosses_firms(api, second_firm):
    """
    החיפוש נוגע בחמש טבלאות. די בטבלה אחת שתשכח את firm_id
    כדי שמשרד אחד יראה נתונים של אחר.

    השמות נשלפים מהמסד ולא מנוחשים, כדי שהבדיקה תמשיך לתפוס
    דליפה גם אם ה-fixture ישנה את הנוסח שלו.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("select full_name from clients where firm_id = %s",
                    (second_firm["firm_id"],))
        names = [r["full_name"] for r in cur.fetchall()]
        cur.execute("select case_number from cases where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["case_number"] for r in cur.fetchall()]
        cur.execute("select name from case_documents where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["name"] for r in cur.fetchall()]
        cur.execute("select title from case_tasks where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["title"] for r in cur.fetchall()]

    other_case = str(second_firm["case_id"])
    leaked = []
    for q in names:
        if not q or len(q) < 2:
            continue
        for hit in find(api, q)["results"]:
            if hit["caseId"] == other_case or hit["id"] == other_case:
                leaked.append(q)
    assert not leaked, "נתונים של משרד אחר דלפו לחיפוש: %s" % leaked


def test_search_needs_a_staff_session(api):
    assert api.get("/api/office/search?q=test").status_code == 401
    assert api.get("/api/office/search/kinds").status_code == 401


def test_a_single_character_returns_nothing(api):
    """תו אחד מחזיר כמעט הכול, וזה אינו חיפוש."""
    staff_login(api)
    data = find(api, "a"[:1])
    assert data["results"] == []
    assert data["minLength"] == 2


def test_wildcards_in_the_query_are_escaped(api):
    """
    בלי בריחה, חיפוש "%" היה מחזיר את כל המשרד - כל הלקוחות,
    כל התיקים וכל המסמכים - למי שהקליד תו אחד.
    """
    staff_login(api)
    for bad in ["%", "_", "%%", "\\"]:
        assert find(api, bad + bad)["results"] == [], (
            "התבנית %r לא נוטרלה" % bad)


def test_the_answer_is_capped(api):
    """שאילתה רחבה לא תגרור אלפי שורות אל הדפדפן."""
    staff_login(api)
    data = find(api, "ב")          # תו עברי נפוץ, אך קצר מהמינימום
    assert data["results"] == []
    data = find(api, "בל")
    assert len(data["results"]) <= 30


# ================================================================
#  3. התנאי: אין כאן AI ואין תוכן מסמכים
# ================================================================

def test_search_returns_no_document_content(api):
    """
    שלב א' מחפש בשמות ובכותרות בלבד. תוכן מסמך, טקסט שחולץ
    או metadata רפואי אינם קיימים כאן - הם שכבה נפרדת שתתוכנן
    בנפרד, יחד עם ההשלכות על METADATA_ONLY.
    """
    staff_login(api)
    body = api.get("/api/office/search?q=ייפוי").text
    for forbidden in ["content", "text", "ocr", "extract", "body", "snippet"]:
        assert forbidden not in body.lower(), (
            "השדה %s הופיע בתשובה - שלב א' אינו אמור להחזיר תוכן" % forbidden)


def test_search_does_not_flood_the_audit_log(api):
    """
    חיפוש הוא פעולת קריאה בתדירות גבוהה. רישום שלו היה מציף
    את היומן ומאבד לו את הערך, בדיוק כמו office.case_viewed.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log")
        before = cur.fetchone()["n"]

    for _ in range(5):
        find(api, "ייפוי")

    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log")
        after = cur.fetchone()["n"]

    assert after == before, "החיפוש כתב שורות אודיט"


# ================================================================
#  4. דירוג
# ----------------------------------------------------------------
#  עד 27.09 לא היה דירוג כלל, והסדר נקבע לפי אלפבית או תאריך.
#  בחיפוש שנועד למצוא פריט מסוים זה ההפרש בין תוצאה ראשונה
#  נכונה לבין גלילה.
# ================================================================

def test_an_exact_match_outranks_a_prefix_and_a_contains(api, temp_case):
    """
    שלוש רמות, ובסדר הזה. הבדיקה בונה את שלושתן כדי שהיא
    תיפול אם הדירוג ייעלם - ולא רק אם הוא ישתנה.
    """
    staff_login(api)
    firm = temp_case["firm_id"]
    case_id = temp_case["case_id"]

    made = []
    with cursor(commit=True) as cur:
        for pos, name in ((91, "זולגן"), (92, "זולגן משלים"),
                          (93, "טופס זולגן נוסף")):
            cur.execute(
                """insert into case_documents (firm_id, case_id, name, position)
                   values (%s, %s, %s, %s) returning id""",
                (firm, case_id, name, pos))
            made.append(cur.fetchone()["id"])

    try:
        hits = [h for h in find(api, "זולגן")["results"]
                if h["kind"] == "document" and str(h["id"]) in map(str, made)]
        assert len(hits) == 3, "לא כל שלושת המסמכים חזרו"

        by_title = {h["title"]: h["rank"] for h in hits}
        assert by_title["זולגן"] == 0, "התאמה מדויקת אינה rank 0"
        assert by_title["זולגן משלים"] == 1, "תחילית אינה rank 1"
        assert by_title["טופס זולגן נוסף"] == 2, "הכלה אינה rank 2"

        order = [h["title"] for h in hits]
        assert order == ["זולגן", "זולגן משלים", "טופס זולגן נוסף"], (
            "התוצאות אינן מוחזרות לפי הדירוג")
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_documents where id = any(%s)", (made,))


def test_the_flat_list_is_ranked_across_sources(api):
    """
    הרשימה השטוחה ממוינת לפי דירוג ולא לפי סוג. שכבת השפה
    הטבעית העתידית תרצה "התוצאה הטובה ביותר", ולא "התוצאה
    הראשונה מהמקור הראשון".
    """
    staff_login(api)
    ranks = [h["rank"] for h in find(api, "ועדה")["results"]]
    assert ranks == sorted(ranks), "הרשימה השטוחה אינה ממוינת לפי דירוג"


# ================================================================
#  5. עברית, אנגלית, ומה שביניהן
# ================================================================

def test_latin_search_is_case_insensitive(api, temp_case):
    """
    EMG ו-emg הם אותה בדיקה רפואית. מי שמקליד באנגלית לא
    מקליד באותיות גדולות.
    """
    staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, position)
               values (%s, %s, 'בדיקת EMG יד שמאל', 94) returning id""",
            (temp_case["firm_id"], temp_case["case_id"]))
        doc = cur.fetchone()["id"]

    try:
        found = {}
        for q in ("EMG", "emg", "eMg"):
            found[q] = {h["id"] for h in find(api, q)["results"]}
            assert str(doc) in found[q], "%r לא מצא את המסמך" % q
        assert found["EMG"] == found["emg"] == found["eMg"], (
            "אותה שאילתה ברישיות שונות החזירה תוצאות שונות")
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_documents where id = %s", (doc,))


def test_mixed_hebrew_and_latin_and_punctuation(api, temp_case):
    """
    שם מסמך אמיתי מערבב שפות, מקפים וסוגריים. כל אחד מהם
    שובר חיפוש נאיבי בדרך אחרת.
    """
    staff_login(api)
    name = "MRI עמוד-שדרה מותני (ללא חומר ניגוד)"
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, position)
               values (%s, %s, %s, 95) returning id""",
            (temp_case["firm_id"], temp_case["case_id"], name))
        doc = cur.fetchone()["id"]

    try:
        for q in ("MRI", "עמוד-שדרה", "עמוד", "חומר ניגוד", "(ללא"):
            ids = {h["id"] for h in find(api, q)["results"]}
            assert str(doc) in ids, "החיפוש %r לא מצא את המסמך" % q
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_documents where id = %s", (doc,))


# ================================================================
#  6. השדות שנוספו
# ================================================================

def test_a_case_is_found_by_its_client_name(api):
    """
    "ישראל" צריך למצוא את התיק שלו ולא רק אותו. זו השאלה
    הנפוצה ביותר בחיפוש משרדי, והיא לא עבדה עד 27.09.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("""select cl.full_name, c.case_number
                         from clients cl join cases c on c.client_id = cl.id
                        limit 1""")
        row = cur.fetchone()

    hits = find(api, row["full_name"].split()[0])["results"]
    cases = [h for h in hits if h["kind"] == "case"]
    assert cases, "חיפוש שם לקוח לא החזיר את התיק שלו"


def test_a_document_is_found_by_its_guidance(api, temp_case):
    """ההנחיה היא טקסט שהצוות כתב, ולעתים היא הזיכרון היחיד."""
    staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, guidance, position)
               values (%s, %s, 'מסמך כלשהו', 'נדרש גם צילום קרסול שמאל', 96)
               returning id""",
            (temp_case["firm_id"], temp_case["case_id"]))
        doc = cur.fetchone()["id"]
    try:
        ids = {h["id"] for h in find(api, "קרסול")["results"]}
        assert str(doc) in ids, "חיפוש בהנחיה של מסמך אינו עובד"
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_documents where id = %s", (doc,))


def test_a_client_with_many_cases_appears_once(api, temp_case):
    """
    ההצטרפות לתיקים מחזירה שורה לכל תיק. בלי distinct לקוח
    עם שלושה תיקים היה מופיע שלוש פעמים ובולע את מכסת
    התוצאות של כל הלקוחות האחרים.
    """
    staff_login(api)
    firm = temp_case["firm_id"]

    with cursor(commit=True) as cur:
        cur.execute("""select client_id, claim_type_id from cases where id = %s""",
                    (temp_case["case_id"],))
        base = cur.fetchone()
        cur.execute("select full_name from clients where id = %s",
                    (base["client_id"],))
        name = cur.fetchone()["full_name"]

        extra = []
        for i in (1, 2):
            cur.execute(
                """insert into cases (firm_id, client_id, claim_type_id,
                                      case_number, opened_at)
                   values (%s, %s, %s, %s, current_date) returning id""",
                (firm, base["client_id"], base["claim_type_id"],
                 "TMPDUP-%d-%s" % (i, str(temp_case["case_id"])[:8])))
            extra.append(cur.fetchone()["id"])

    try:
        hits = [h for h in find(api, name.split()[0])["results"]
                if h["kind"] == "client"]
        ids = [h["id"] for h in hits]
        assert len(ids) == len(set(ids)), "אותו לקוח חזר יותר מפעם אחת"
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from cases where id = any(%s)", (extra,))


# ================================================================
#  7. החוזה שהממשק נשען עליו
# ================================================================

def test_every_result_knows_where_to_open(api):
    """
    caseId ו-tab הם מה שהופך תוצאה לפעולה. תוצאה בלעדיהם
    היא שורה שאי אפשר ללחוץ עליה.
    """
    staff_login(api)
    # בדיוק שש הלשוניות שקיימות ב-admin.html. "updates" היא
    # תצוגה בפורטל הלקוח ולא לשונית בתיק, ותוצאה שמצביעה
    # אליה הייתה נוחתת על כלום.
    TABS = {"state", "comms", "docs", "committees", "tasks", "history"}

    for q in ("ועדה", "נכות", "מסמך"):
        for h in find(api, q)["results"]:
            assert h["tab"] in TABS, "יעד ניווט לא מוכר: %r" % h["tab"]
            assert h["caseId"], "תוצאה בלי caseId אינה ניתנת לפתיחה: %r" % h["title"]
            assert h["title"], "תוצאה בלי כותרת"


def test_the_grouped_contract(api):
    """
    הקבוצות והרשימה השטוחה חייבות להסכים. אם הן ייפרדו,
    הממשק יציג דבר אחד והשכבה הבאה תקרא דבר אחר.
    """
    staff_login(api)
    data = find(api, "ועדה")

    assert "groups" in data and "results" in data and "total" in data

    from_groups = []
    for g in data["groups"]:
        assert g["kind"] and g["label"], "קבוצה בלי סוג או תווית"
        assert g["results"], "קבוצה ריקה לא אמורה להיות בתשובה"
        from_groups.extend(x["id"] for x in g["results"])

    assert len(from_groups) == data["total"]
    assert set(from_groups) == {x["id"] for x in data["results"]}, (
        "הקבוצות והרשימה השטוחה אינן מכילות את אותן תוצאות")


def test_the_per_kind_limit_holds(api):
    """
    התקרה היא לכל מקור בנפרד. בלעדיה מקור אחד עשיר בנתונים
    היה דוחק את כל האחרים מהתשובה.
    """
    staff_login(api)
    data = find(api, "ה")          # תו בודד - מתחת למינימום
    assert data["results"] == []

    data = find(api, "ועדה")
    for g in data["groups"]:
        assert len(g["results"]) <= 8, "המקור %s חרג מהתקרה" % g["kind"]


# ================================================================
#  8. פרטיות: מה שאסור לצאת
# ================================================================

def test_no_personal_identifier_ever_leaves_the_search(api):
    """
    תעודת זהות היא גם מזהה מוצפן וגם מפתח ההתחברות של
    הלקוח. אין לה שום שימוש בתוצאת חיפוש.

    טלפון ואימייל אפשר לחפש לפיהם - זה מה שמאפשר למצוא לקוח
    לפי מספר שהתקשר ממנו - אבל הם אינם חוזרים בתשובה.
    """
    staff_login(api)
    for q in ("ישראל", "050", "example", "ועדה"):
        body = api.get("/api/office/search?q=%s" % q).text.lower()
        for forbidden in ("national", "nationalid", "national_id",
                          "phone", "email", "password", "id_enc"):
            assert forbidden not in body, (
                "השדה %s הופיע בתשובה לשאילתה %r" % (forbidden, q))


def test_a_client_can_be_found_by_phone_without_it_being_returned(api):
    """
    שני הצדדים של אותו כלל, בבדיקה אחת.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("""select full_name, phone from clients
                        where phone is not null limit 1""")
        row = cur.fetchone()
    if not row:
        return

    data = find(api, row["phone"])
    names = [h["title"] for h in data["results"] if h["kind"] == "client"]
    assert row["full_name"] in names, "חיפוש לפי טלפון לא מצא את הלקוח"

    # הבדיקה על התוצאות בלבד. השדה query הוא הד של מה
    # שהמשתמש הקליד בעצמו, ואינו חשיפה.
    for h in data["results"]:
        assert row["phone"] not in str(h.values()), "הטלפון חזר בתוצאה"
