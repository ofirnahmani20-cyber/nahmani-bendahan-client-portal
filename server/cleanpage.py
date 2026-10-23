"""
cleanpage.py - עמוד נקי וקריא מסריקה או מצילום טלפון (docs/10, 23.10).

רץ בתוך ה-sandbox בלבד (task "clean"), על תמונה שכבר פוענחה שם.
אינו מייבא מסד, מפתחות או אחסון - אותו כלל של sandbox.py.

מה נעשה לכל עמוד, בסדר הזה:
  1. סיבוב לפי EXIF - טלפון ששומר "תמונה לרוחב + דגל סיבוב"
  2. גווני אפור
  3. יישור תאורה (ocr.prepare) - רק כשהרקע אינו אחיד, כמו ב-OCR
  4. יישור הטיה עד ±MAX_SKEW מעלות, לפי פרופיל ההקרנה
  5. מתיחת ניגודיות
  6. הקטנה לרוחב של A4 ב-150 DPI, ו-JPEG

מה *לא* נעשה: חיתוך הדף מתוך רקע (שולחן מאחורי הדף בצילום), וסיבוב
של 90/180 מעלות בסריקה בלי EXIF. שניהם דורשים זיהוי שעלול לטעות
ולחתוך תוכן; עמוד עם שוליים מיותרים עדיף על עמוד חתוך.

Pillow בלבד - בלי OpenCV, שהיה מוסיף כ-50MB של קוד נייטיבי שמפענח
קלט עוין, בשביל שני הצעדים שמומשו כאן בכמה עשרות שורות.
"""

import io

from PIL import Image, ImageOps, ImageStat

TARGET_DPI = 150
MAX_WIDTH = 1240            # A4 ברוחב 8.27 אינץ' ב-150 DPI
JPEG_QUALITY = 70

MAX_SKEW = 5.0
SKEW_STEP = 0.25
MIN_SKEW = 0.4              # מתחת לזה - לא נוגעים: סיבוב מטשטש מעט
PROBE_WIDTH = 600


def _profile_score(binary, angle):
    """
    שונות סכומי השורות אחרי סיבוב. שורות טקסט מיושרות = שורות כהות
    ובהירות לסירוגין = שונות גבוהה. הקטנה לרוחב 1 בממוצע BOX היא
    בדיוק ממוצע השורה, בלי numpy.
    """
    rotated = binary.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
    column = rotated.resize((1, rotated.height), Image.Resampling.BOX)
    return ImageStat.Stat(column).var[0]


def skew_angle(gray):
    """הזווית (במעלות, נגד כיוון השעון) שמיישרת את שורות הטקסט, או 0."""
    if gray.width < 50 or gray.height < 50:
        return 0.0
    scale = PROBE_WIDTH / gray.width if gray.width > PROBE_WIDTH else 1.0
    probe = gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))))
    # הפוך: דיו = לבן (255), רקע = 0. כך הסיבוב ממלא את הפינות ב"רקע".
    threshold = ImageStat.Stat(probe).mean[0] * 0.75
    binary = probe.point(lambda v: 255 if v < threshold else 0)

    best, best_score = 0.0, _profile_score(binary, 0.0)
    steps = int(MAX_SKEW / SKEW_STEP)
    for i in range(-steps, steps + 1):
        angle = i * SKEW_STEP
        if angle == 0:
            continue
        score = _profile_score(binary, angle)
        if score > best_score * 1.02:       # שיפור ממשי, לא רעש
            best, best_score = angle, score
    return best if abs(best) >= MIN_SKEW else 0.0


def clean(img, *, flatten=True):
    """
    תמונת עמוד (PIL) -> (JPEG bytes, רוחב, גובה, זווית היישור).
    """
    from . import ocr

    img = ImageOps.exif_transpose(img)
    gray = img.convert("L")
    if flatten:
        gray, _ = ocr.prepare(gray)

    angle = skew_angle(gray)
    if angle:
        gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True,
                           fillcolor=255)

    gray = ImageOps.autocontrast(gray, cutoff=1)
    if gray.width > MAX_WIDTH:
        h = max(1, round(gray.height * MAX_WIDTH / gray.width))
        gray = gray.resize((MAX_WIDTH, h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    gray.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), gray.width, gray.height, angle
