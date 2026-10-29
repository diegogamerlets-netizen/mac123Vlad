"""Генерация макета скульптуры с четырёх ракурсов через OpenAI Images API.

Провайдер выбирается переменной AI_PROVIDER (если не задана — по наличию ключа):
  openai — OpenAI (нужен OPENAI_API_KEY);
  demo   — без нейросети, возвращает исходное фото (для проверки сайта).

Чтобы подключить другую нейросеть, реализуйте функцию _call_xxx(images, prompt) -> bytes
и добавьте её в _caller().
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

VIEWS = ["front", "right", "back", "left"]


class AIError(Exception):
    pass


STYLE_PROMPTS = {
    "wood": "hand-carved light birch wood sculpture with visible chisel facets, monochrome pale wood tone",
    "marble": "classical white marble sculpture, polished stone, monochrome ivory tone (the person keeps the same clothing as in the photo; no toga, cloak or added drapery)",
    "folk": "hand-carved light stone-like sculpture dressed in traditional Slavic folk clothing with fine embroidered ornament borders and a cloak, monochrome cream tone",
    "bronze": "cast polished bronze sculpture with warm copper-brown patina and soft highlights, on a simple bronze base",
    "lowpoly": "low-poly faceted geometric sculpture made of flat triangular planes, matte white, monochrome",
    "artdeco": "elegant Art Deco sculpture in dark patinated bronze with sharp geometric fold lines, on a stepped plinth",
    "relief": "bas-relief portrait medallion in an oval frame on a small stand, carved ivory-colored stone, monochrome cream tone",
}

# Единственный цвет всей скульптуры для каждого стиля (модели любят двухцветные вещи: тёмный воротник, светлое лицо)
STYLE_COLORS = {
    "wood": "light pale birch-wood tan",
    "marble": "ivory white marble",
    "folk": "warm cream stone",
    "bronze": "warm copper-bronze metal",
    "lowpoly": "matte white",
    "artdeco": "dark graphite-bronze",
    "relief": "cream ivory stone",
}

BASE_RULES = (
    "Exactly ONE single sculpture, centered in the frame, standing on its own small base, on a plain neutral studio background "
    "with soft light. There must be no other statues, figures, people, props or objects anywhere in the frame, and no text or watermark. "
    "It must look like a physical sculpture about 12 cm tall. The whole sculpture including the ENTIRE base is fully visible inside the frame, with empty margin around it on all sides; nothing is cropped. "
    "CLOTHING (applies to every angle): the clothing is exactly the same on every view as on the front view: the same garments, collar, neckline, folds and accessories. "
    "Never add, remove or change any garment, cloak, drapery or accessory when the viewing angle changes. "
    "MATERIAL (applies to every angle): the ENTIRE sculpture, including the face, skin, hair, clothing, the back, and the base, is made of ONE single material "
    "with one uniform color and finish. Never use natural colors: no skin tone, no natural hair color, and no colored or black clothing, "
    "even if the photo shows them. Colors from the real photo must be ignored; the photo is used only for the shapes and the facial likeness."
)

# Сходство с оригиналом важнее стиля: модели склонны «улучшать» лицо и рисовать обобщённую статую.
LIKENESS = (
    "Reproduce THIS exact person, not a generic one. Copy the proportions of the face exactly (face width, cheek fullness, jawline, chin, forehead height), "
    "the exact shape, size, spacing and gaze direction of the eyes, the shape and thickness of the eyebrows, the shape of the nose and nostrils, the shape of the lips, "
    "the exact smile and facial expression, and every distinctive detail such as freckles, moles, dimples, asymmetries and skin texture, rendered as subtle sculpted relief. "
    "Keep natural imperfections; never smooth the face into an idealized pretty one, and do not make the face thinner, rounder, older or younger than in the photo. "
    "The sculpture must be a faithful portrait likeness of the real person in the photo, instantly recognizable as that exact person: "
    "same face shape, jaw, forehead, eyes and eye spacing, eyebrows, nose, lips, ears, hairline and hairstyle, same age and the same facial expression. "
    "Do NOT beautify, idealize, rejuvenate or replace the face with a generic classical or statue-like face. "
    "Preserve the person's gender, age and build exactly: a woman must clearly look like a woman, a man like a man, a child like a child. "
    "Preserve the hair exactly: its length, volume, waves or curls, parting and how it frames the face. Never shorten, crop, cut off, thin out or simplify the hair, "
    "even when the photo is a tight close-up and the hair is cut off by the edge of the photo: complete it naturally in the same length "
    "(for example, shoulder-length hair keeps falling to the shoulders and covers the ears if it does so in the photo). "
    "If the photo is a tight close-up of the face, still sculpt the neck and shoulders as a bust, with simple plain clothing. "
    "Dress the sculpture in exactly the same garments the person wears in the photo (for example a jacket with a collar and zipper), "
    "with the same neckline and collar, and keep the posture from the photo. Do not add any cloak, toga, drapery, scarf, jewelry or other garments that are not in the photo. "
    "The ENTIRE sculpture, including the face, skin, hair and clothing, is made of ONE single sculpted material with one uniform color and finish "
    "exactly as described in the style; do NOT copy skin tone, hair color or clothing colors from the photo, and do not paint any part in natural colors. "
    "Match the framing of the photo: if only the head and shoulders are visible, make a portrait bust (head, neck, shoulders); "
    "make a full-body figure only if the whole body is visible in the photo."
)

VIEW_PROMPTS = {
    "front": "TOP PRIORITY: an exact, instantly recognizable likeness of the real person. {refs} "
             "Create a photorealistic studio photograph of a MONOCHROME single-color sculpture made as a {style}. "
             "Front view. Any dark or black clothing visible in the photo must be sculpted in exactly the same material and color as the face and hair; "
             "there must be no black or dark patch anywhere on the sculpture, not even a small one on a shoulder or collar. {likeness} {rules}",
    "right": "Image 1 is a finished sculpture ({style}). {refs} "
             "Show exactly the same sculpture as Image 1, with the identical material, color and surface finish copied from Image 1, rotated 90 degrees so we see its RIGHT side in strict profile, with the face pointing toward the RIGHT edge of the image. "
             "Keep the exact likeness of the real person. Do not change the material or color. {rules}",
    "back": "Image 1 is a finished sculpture ({style}). {refs} "
            "Show exactly the same sculpture as Image 1, with the identical material, color and surface finish copied from Image 1, seen from directly BEHIND "
            "(the back of the clothing is plain, without zippers or buttons). Do not change the material or color. {rules}",
    "left": "Image 1 is a finished sculpture ({style}). {refs} "
            "Show exactly the same sculpture as Image 1, with the identical material, color and surface finish copied from Image 1, rotated 90 degrees so we see its LEFT side in strict profile, with the face pointing toward the LEFT edge of the image (the opposite direction to the right-side view, showing the other side of the head and hair). "
            "Keep the exact likeness of the real person. Do not change the material or color. {rules}",
}

SIDE_NAMES = {"left": "LEFT side", "right": "RIGHT side", "back": "BACK"}
EXTRA_ORDER = ("left", "right", "back")


def _refs(first_index, extras, with_front_photo):
    """Текстовое описание картинок-образцов (Image N) и порядок их передачи."""
    parts, n = [], first_index
    if with_front_photo:
        parts.append(f"Image {n} is the main (front) photo of the real person.")
        n += 1
    for key in EXTRA_ORDER:
        if extras.get(key):
            parts.append(f"Image {n} is a photo of the same person from the {SIDE_NAMES[key]}: use it for the real shape of the hair, ears, profile and back of the head.")
            n += 1
    parts.append("Use the photos ONLY as a reference for the facial likeness, proportions and hair shape; ignore their colors.")
    return " ".join(parts)


OPENAI_URL = "https://api.openai.com/v1"


def _provider():
    chosen = os.environ.get("AI_PROVIDER", "").lower()
    if chosen:
        return chosen
    return "openai" if os.environ.get("OPENAI_API_KEY") else "demo"


def demo_mode():
    return _provider() == "demo"


def _sniff_mime(data):
    if data.startswith(b"\x89PNG"):
        return "image/png", "image.png"
    if data[8:12] == b"WEBP":
        return "image/webp", "image.webp"
    return "image/jpeg", "image.jpg"


def _multipart(fields, files):
    """files: список (имя_поля, имя_файла, байты, mime)."""
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for field, filename, data, mime in files:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                      f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _http_json(req, timeout=300, retries=2):
    """Запрос с повтором при временных сбоях (429/5xx/сеть). Возвращает разобранный JSON."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()[:1000]
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"AI HTTP {exc.code}:", body.decode("utf-8", "replace"))
            if exc.code in (401, 403):
                err = AIError("Создание макетов временно недоступно. Мы уже разбираемся.")
            else:
                err = AIError("Не удалось создать макет по этому фото. Попробуйте другое фото или стиль.")
            err.body = body.decode("utf-8", "replace")
            raise err from exc
        except Exception as exc:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            print("AI failure:", repr(exc))
            raise AIError("Создание макета сейчас недоступно. Попробуйте чуть позже.") from exc


def _call_openai(images, prompt, quality=None):
    """OpenAI Images API (edits): одна или несколько картинок-образцов + промпт -> байты изображения."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise AIError("Не задан OPENAI_API_KEY")
    images = list(images) if isinstance(images, (list, tuple)) else [images]
    files = []
    for n, img in enumerate(images):
        mime, name = _sniff_mime(img)
        files.append(("image[]", f"ref{n}{name[name.index('.'):]}", img, mime))
    fields = {
        "model": os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        "prompt": prompt,
        "size": os.environ.get("OPENAI_IMAGE_SIZE", "1024x1536"),
        "quality": quality or os.environ.get("OPENAI_IMAGE_QUALITY", "medium"),
        "n": "1",
    }
    fidelity = os.environ.get("OPENAI_INPUT_FIDELITY", "high")   # high = точнее сохраняет лицо и детали с фото
    if fidelity and fidelity != "off":
        fields["input_fidelity"] = fidelity
    started = time.time()
    try:
        body, ctype = _multipart(fields, files)
        req = urllib.request.Request(OPENAI_URL + "/images/edits", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
        payload = _http_json(req)
    except AIError as err:
        if "input_fidelity" in getattr(err, "body", ""):    # модель не знает этот параметр — повторяем без него
            fields.pop("input_fidelity", None)
            body, ctype = _multipart(fields, files)
            req = urllib.request.Request(OPENAI_URL + "/images/edits", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
            payload = _http_json(req)
        else:
            raise
    print(f"OpenAI image: {len(images)} реф., {fields['quality']}, ответ за {time.time() - started:.0f} с")
    items = payload.get("data") or []
    if not items or not items[0].get("b64_json"):
        print("OpenAI: в ответе нет картинки:", str(payload)[:400])
        raise AIError("Не удалось создать макет по этому фото. Попробуйте другое фото или стиль.")
    return base64.b64decode(items[0]["b64_json"])


def _caller():
    return {"openai": _call_openai}[_provider()]


def _qa_enabled():
    return _provider() == "openai" and os.environ.get("AI_QA", "1") != "0"


def _is_uniform(image, style):
    """Автопроверка результата моделью зрения: нет ли тёмных/контрастных пятен на скульптуре.

    Возвращает True, если всё в порядке ИЛИ если проверку выполнить не удалось (проверка не должна ломать заказ).
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not (key and _qa_enabled()):
        return True
    color = STYLE_COLORS.get(style, "a single color")
    question = (
        f"This is a photo of a sculpture. The sculpture must be made of ONE single uniform color ({color}) in every part, including "
        "face, hair, clothing and base. Does any part of the sculpture have a clearly different, darker or black/grey color "
        "(for example a dark patch, dark inlay, dark collar or dark clothing)? Answer with exactly one word: DARK or UNIFORM.")
    uri = f"data:{_sniff_mime(image)[0]};base64,{base64.b64encode(image).decode()}"
    body = {
        "model": os.environ.get("OPENAI_QA_MODEL", "gpt-4.1-mini"),
        "max_tokens": 5, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": uri, "detail": "low"}}, {"type": "text", "text": question}]}],
    }
    req = urllib.request.Request(OPENAI_URL + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            answer = json.loads(resp.read())["choices"][0]["message"]["content"].strip().upper()
    except Exception as exc:
        print("QA: проверка недоступна, пропускаю:", repr(exc))
        return True
    print("QA:", answer)
    return not answer.startswith("DARK")


def _checked(call, images, prompt, style, attempts, deadline, on_retry=None):
    """Генерирует картинку и при тёмных пятнах пробует заново (но не дольше общего дедлайна)."""
    out = None
    for i in range(attempts):
        out = call(images, prompt)
        if _is_uniform(out, style):
            return out
        if time.time() > deadline:
            print("QA: дедлайн, отдаю как есть")
            break
        print(f"QA: тёмные участки, перегенерация (попытка {i + 2} из {attempts})")
        if on_retry and i + 1 < attempts:
            on_retry()
    return out


def generate_views(photo, style, people=1, elements=0, note="", progress=None, extras=None):
    """Возвращает {'front': bytes, 'right': bytes, 'back': bytes, 'left': bytes}.

    extras — необязательные дополнительные фото того же человека: {'left': bytes, 'right': bytes, 'back': bytes}.
    """
    if demo_mode():
        return {v: photo for v in VIEWS}
    extras = {k: v for k, v in (extras or {}).items() if k in EXTRA_ORDER and v}
    extra_images = [extras[k] for k in EXTRA_ORDER if k in extras]
    style_text = STYLE_PROMPTS[style]
    rules = BASE_RULES + f" The sculpture shows exactly {people} {'person' if people == 1 else 'people'} from the photo."
    rules += (f" STRICT COLOR RULE: the whole sculpture, every single part (face, hair, neck, clothing, collar, buttons, base), has exactly ONE uniform color: "
              f"{STYLE_COLORS[style]}. No black, dark, grey or contrasting parts at all, no two-tone, no painted details, no veins of another color.")
    if elements:
        rules += f" It also includes {elements} extra element(s) (for example a pet or an object) taken from the photo"
        rules += f" or described as: {note}." if note else "."

    def prompt(view):
        # у вида спереди фото начинаются с Image 1, у остальных Image 1 — готовая скульптура
        refs = _refs(1, extras, True) if view == "front" else _refs(2, extras, True)
        return VIEW_PROMPTS[view].format(style=style_text, rules=rules, likeness=LIKENESS, refs=refs)

    call = _caller()
    front_quality = os.environ.get("OPENAI_FRONT_QUALITY", "high")    # внешность задаётся на виде спереди — тут не экономим
    say = progress or (lambda step, pct: None)
    deadline = time.time() + int(os.environ.get("AI_QA_BUDGET_SEC", "420"))   # дальше повторы не делаем
    say("Лепим вид спереди", 8)
    front = _checked(lambda imgs, p: call(imgs, p, quality=front_quality), [photo] + extra_images, prompt("front"),
                     style, 3, deadline, on_retry=lambda: say("Дорабатываем вид спереди", 30))
    say("Создаём боковые виды и вид сзади", 55)
    others = ["right", "back", "left"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda v: _checked(call, [front, photo] + extra_images, prompt(v), style, 2, deadline,
                                                   on_retry=lambda: say("Дорабатываем один из видов", 80)), others))
    say("Собираем макет", 95)
    out = {"front": front}
    out.update(dict(zip(others, results)))
    return out
