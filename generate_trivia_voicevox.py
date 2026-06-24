# =========================================================
# 雑学たまご Shorts（VOICEVOX）1本を生成してYouTubeへ自動投稿
# ★ダメ元版★ GitHub Actions上でVOICEVOXエンジン(Docker)を立てて使う。
# お題もGeminiが全自動生成・被り防止ログつき。
# Gemini → VOICEVOX（春日部つむぎ）→ MoviePy → YouTube API
# 縦型1080x1920
# =========================================================
import os, re, json, time, requests
from google import genai
try:
    from google.genai import types as genai_types
except Exception:
    genai_types = None
from pydub import AudioSegment
from moviepy.editor import (
    ColorClip, ImageClip, TextClip, CompositeVideoClip,
    AudioFileClip, CompositeAudioClip, concatenate_videoclips, afx
)
import moviepy.config as cf
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

cf.change_settings({"IMAGEMAGICK_BINARY": "/usr/bin/convert"})

# ----- 環境変数（GitHub Secrets） -----
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
YT_CLIENT_ID     = os.environ["YT_CLIENT_ID"]
YT_CLIENT_SECRET = os.environ["YT_CLIENT_SECRET"]
YT_REFRESH_TOKEN = os.environ["YT_REFRESH_TOKEN"]

PRIVACY = os.environ.get("PRIVACY", "public")
MODEL   = os.environ.get("MODEL", "gemini-2.5-flash")

VOICEVOX_URL = "http://127.0.0.1:50021"
SPEAKER_ID   = 8        # 8=春日部つむぎ
VOICE_SPEED  = 1.3

OUT_DIR  = "out_trivia"
LOG_PATH = "used_log_trivia.json"
AVOID_RECENT = 40

BG_IMAGE = "assets/trivia_bg.png" if os.path.exists("assets/trivia_bg.png") else None
BGM_PATH = "assets/bgm.mp3" if os.path.exists("assets/bgm.mp3") else None
BGM_VOLUME = 0.15

client = genai.Client(api_key=GEMINI_API_KEY)

W, H = 1080, 1920
FPS = 10

HEADER_TEXT = "雑学たまご"
HEADER_FONT = "/usr/share/fonts/truetype/custom/PottaOne-Regular.ttf"
HEADER_FONT_SIZE = 140
HEADER_STROKE_COLOR = "#FFD900"
HEADER_STROKE_WIDTH = 50
HEADER_INTERLINE = -20
HEADER_Y = 0.01

FONT = "/usr/share/fonts/truetype/custom/MochiyPopOne-Regular.ttf"
if not os.path.exists(FONT):
    FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
if not os.path.exists(HEADER_FONT):
    HEADER_FONT = FONT
TEXT_COLOR = "white"
STROKE_COLOR = "#FFD900"
MAIN_STROKE_WIDTH = 20
FONT_SIZE = 80


# ----- 被り防止ログ -----
def load_log():
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_log(log):
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


# ----- Geminiで雑学（お題ごと全自動生成・被り回避） -----
def generate_trivia(avoid_summaries, max_retries=5):
    models = [MODEL, "gemini-2.5-flash-lite", "gemini-3.1-flash-lite"]
    avoid_text = ""
    if avoid_summaries:
        joined = "\n".join(f"- {s}" for s in avoid_summaries)
        avoid_text = f"\n\n【これらと被らない、別ジャンルの新しい雑学にすること】\n{joined}"
    prompt = f"""あなたは雑学・豆知識のプロです。
思わず「へぇ！」となる面白い雑学を、テーマから自分で考えて1つ作ってください。
（歴史・科学・動物・言葉・食べ物・地理・日常の不思議など、ジャンルは自由）
※事実として正しい、よく知られた雑学にしてください。マイナーすぎる新説や不確かな情報は避ける。

以下のJSON形式のみで出力（前後に説明やマークダウン不要）:
{{
  "youtube_title": "タップしたくなるタイトル（25文字以内）",
  "summary": "この雑学の要約を1行で（被り防止ログ用・40文字以内）",
  "hook": "冒頭の掴み（興味を引く問いかけ・30文字以内）",
  "topic": "雑学のテーマ名（15文字以内）",
  "explanation": ["解説1（50文字以内）", "解説2", "解説3"],
  "surprising_fact": "一番の驚きポイント・オチ（40文字以内・「実は…」で始めると効果的）",
  "ending": "締めの一言・視聴者への問いかけ（30文字以内）"
}}
※explanation は必ず3文。{avoid_text}
"""
    cfg = genai_types.GenerateContentConfig(temperature=1.15) if genai_types else None
    for attempt in range(max_retries):
        m = models[min(attempt, len(models) - 1)]
        try:
            if cfg:
                resp = client.models.generate_content(model=m, contents=prompt, config=cfg)
            else:
                resp = client.models.generate_content(model=m, contents=prompt)
            text = resp.text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            msg = str(e)
            if ("503" in msg or "429" in msg or "UNAVAILABLE" in msg) and attempt < max_retries - 1:
                time.sleep(20 * (attempt + 1))
            elif attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise


# ----- VOICEVOX音声生成 -----
def make_audio(text, filename, tail_cut=100):
    q = requests.post(f"{VOICEVOX_URL}/audio_query",
                      params={"text": text, "speaker": SPEAKER_ID}, timeout=60)
    query = q.json()
    query["speedScale"] = VOICE_SPEED
    query["prePhonemeLength"] = 0.1
    query["postPhonemeLength"] = 0.1
    s = requests.post(f"{VOICEVOX_URL}/synthesis",
                      params={"speaker": SPEAKER_ID},
                      data=json.dumps(query),
                      headers={"Content-Type": "application/json"}, timeout=120)
    tmp_wav = "tmp_" + filename.replace(".mp3", ".wav")
    with open(tmp_wav, "wb") as f:
        f.write(s.content)
    seg = AudioSegment.from_wav(tmp_wav)
    if tail_cut > 0 and len(seg) > tail_cut + 100:
        seg = seg[:-tail_cut]
    seg.export(filename, format="mp3")
    os.remove(tmp_wav)
    return filename


# ----- 背景（Pillowで直接リサイズ＝ANTIALIASエラー回避） -----
_BG_CACHE = None
def _fit_bg(path):
    global _BG_CACHE
    if _BG_CACHE is None:
        from PIL import Image
        import numpy as np
        resample = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        _BG_CACHE = np.array(Image.open(path).convert("RGB").resize((W, H), resample))
    return _BG_CACHE


def make_background(duration, bg_color):
    if BG_IMAGE and os.path.exists(BG_IMAGE):
        return ImageClip(_fit_bg(BG_IMAGE)).set_duration(duration)
    return ColorClip(size=(W, H), color=bg_color, duration=duration)


def make_outlined_clip(text, duration, fontsize, font=None,
                       stroke_color=None, stroke_width=None, interline=14, size=None):
    if font is None: font = FONT
    if stroke_color is None: stroke_color = STROKE_COLOR
    if stroke_width is None: stroke_width = MAIN_STROKE_WIDTH
    if size is None: size = (W - 60, None)
    common = dict(font=font, fontsize=fontsize, method="caption",
                  size=size, align="center", interline=interline)
    shadow = (TextClip(text, color="black", stroke_color="black",
                       stroke_width=stroke_width + 4, **common)
              .set_duration(duration).set_opacity(0.45))
    stroke = TextClip(text, color=stroke_color, stroke_color=stroke_color,
                      stroke_width=stroke_width, **common).set_duration(duration)
    fill = TextClip(text, color=TEXT_COLOR, **common).set_duration(duration)
    return CompositeVideoClip(
        [shadow.set_position(("center", 6)), stroke.set_position("center"),
         fill.set_position("center")], size=stroke.size).set_duration(duration)


def make_scene(text, audio_file, bg_color=(30, 25, 10), fontsize=None):
    if fontsize is None: fontsize = FONT_SIZE
    narration = AudioFileClip(audio_file)
    duration = narration.duration + 0.5
    layers = [make_background(duration, bg_color)]
    main = make_outlined_clip(text, duration, fontsize)
    layers.append(main.set_position(("center", H * 0.45)))
    if HEADER_TEXT:
        header = make_outlined_clip(HEADER_TEXT, duration, HEADER_FONT_SIZE,
                                    font=HEADER_FONT, stroke_color=HEADER_STROKE_COLOR,
                                    stroke_width=HEADER_STROKE_WIDTH, interline=HEADER_INTERLINE
                                    ).set_position(("center", H * HEADER_Y))
        layers.append(header)
    scene = CompositeVideoClip(layers, size=(W, H)).set_duration(duration)
    return scene.set_audio(narration)


def make_output_path(yt_title=""):
    os.makedirs(OUT_DIR, exist_ok=True)
    safe = yt_title or "雑学たまご"
    for ch in r'\/:*?"<>|':
        safe = safe.replace(ch, "")
    return os.path.join(OUT_DIR, f"{safe.strip()}.mp4")


def build_video(data):
    output_path = make_output_path(data.get("youtube_title", ""))
    scenes = []
    a = make_audio(data["hook"], "a_hook.mp3", tail_cut=80)
    scenes.append(make_scene(data["hook"], a, bg_color=(80, 65, 20)))
    a = make_audio(data["topic"], "a_topic.mp3", tail_cut=80)
    scenes.append(make_scene(data["topic"], a, bg_color=(65, 55, 20)))
    for i, exp in enumerate(data["explanation"]):
        a = make_audio(exp, f"a_exp{i}.mp3", tail_cut=100)
        scenes.append(make_scene(exp, a, bg_color=(50, 60, 75)))
    a = make_audio(data["surprising_fact"], "a_fact.mp3", tail_cut=100)
    scenes.append(make_scene(data["surprising_fact"], a, bg_color=(90, 70, 20)))
    a = make_audio(data["ending"], "a_end.mp3", tail_cut=100)
    scenes.append(make_scene(data["ending"], a, bg_color=(80, 65, 20)))

    final = concatenate_videoclips(scenes, method="compose")
    if BGM_PATH and os.path.exists(BGM_PATH):
        bgm = afx.audio_loop(AudioFileClip(BGM_PATH).volumex(BGM_VOLUME), duration=final.duration)
        final = final.set_audio(CompositeAudioClip([final.audio, bgm]) if final.audio else bgm)
    final.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac")

    for f in os.listdir("."):
        if f.startswith("a_") and f.endswith(".mp3"):
            os.remove(f)
    return output_path


# ----- YouTube -----
def get_youtube():
    creds = Credentials(token=None, refresh_token=YT_REFRESH_TOKEN,
                        client_id=YT_CLIENT_ID, client_secret=YT_CLIENT_SECRET,
                        token_uri="https://oauth2.googleapis.com/token")
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(youtube, path, title):
    description = (
        "へぇ！となる雑学をお届け。\n\n"
        "VOICEVOX:春日部つむぎ\n\n#雑学 #豆知識 #shorts #Shorts"
    )
    body = {
        "snippet": {
            "title": (title + " #shorts")[:100],
            "description": description[:5000],
            "tags": ["雑学", "豆知識", "Shorts", "雑学たまご", "へぇ"],
            "categoryId": "27",   # 教育
            "defaultLanguage": "ja",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, chunksize=10 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None; retry = 0
    while response is None:
        try:
            status, response = req.next_chunk()
            if status:
                print(f"  ⏫ {int(status.progress()*100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                retry += 1
                if retry > 10: raise
                time.sleep(min(2 ** retry, 60))
            else:
                raise
    return response


def wait_voicevox(timeout=180):
    """VOICEVOXエンジンが立ち上がるまで待つ"""
    for _ in range(timeout // 3):
        try:
            if requests.get(f"{VOICEVOX_URL}/version", timeout=5).ok:
                print("✅ VOICEVOXエンジン応答OK")
                return True
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("VOICEVOXエンジンが起動しませんでした")


def main():
    wait_voicevox()
    log = load_log()
    avoid = [e["summary"] for e in log][-AVOID_RECENT:]
    print("📝 雑学を生成中...")
    data = generate_trivia(avoid)
    print(f"   タイトル：{data.get('youtube_title')}")

    path = build_video(data)
    print(f"🎬 生成完了：{path}")

    youtube = get_youtube()
    res = upload(youtube, path, data.get("youtube_title", "雑学たまご"))
    print(f"✅ 投稿成功： https://www.youtube.com/watch?v={res['id']}")

    log.append({"title": data.get("youtube_title", ""), "summary": data.get("summary", "")})
    save_log(log)
    print(f"📝 ログ更新（計{len(log)}件）")


if __name__ == "__main__":
    main()
