# =========================================================
# 2ch風スカッとスレ読み上げ（縦型Shorts・VOICEVOX版）
# GitHub Actions上でVOICEVOXエンジン(Docker)を立てて使う。
# イッチと名無しで話者を変える（声の演じ分け）。
# 【完全創作】1スレ完結・1分前後。お題もGeminiが全自動・被り防止ログつき。
# Gemini → VOICEVOX → MoviePy（レス風画面・縦型）→ YouTube API
# =========================================================
import os, re, json, time, gc, random, requests
from google import genai
try:
    from google.genai import types as genai_types
except Exception:
    genai_types = None
from pydub import AudioSegment
from moviepy.editor import (
    ColorClip, TextClip, CompositeVideoClip, AudioFileClip, CompositeAudioClip
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
SPEAKER_OP   = 2     # イッチ（スレ主）= 四国めたん
SPEAKER_NPC  = 7     # 名無し（合いの手）= ずんだもん（ツンツン）
# 話者を「話者名＋スタイル名」で指定（IDがバージョンで変わっても引き直す）
SPEAKER_OP_NAME   = "四国めたん"
SPEAKER_OP_STYLE  = "ノーマル"
SPEAKER_NPC_NAME  = "ずんだもん"
SPEAKER_NPC_STYLE = "ツンツン"
VOICE_SPEED  = 1.35

OUT_DIR  = "out_2ch_s_vv"
TMP_DIR  = "tmp_2ch_s_vv"
LOG_PATH = "used_log_2ch_shorts_vv.json"
AVOID_RECENT = 40

BGM_PATH = "assets/bgm.mp3" if os.path.exists("assets/bgm.mp3") else None
BGM_VOLUME = 0.08

client = genai.Client(api_key=GEMINI_API_KEY)

W, H = 1080, 1920
FPS = 10

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
if not os.path.exists(FONT):
    FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"

BG_COLOR       = (239, 239, 239)
TITLE_BG       = (204, 102, 0)
TITLE_COLOR    = "white"
NAME_COLOR     = "#008800"
NAME_COLOR_OP  = "#CC0000"
BODY_COLOR     = "#1A1A1A"
RES_FONTSIZE   = 66
NAME_FONTSIZE  = 40
TITLE_FONTSIZE = 60


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


# ----- Gemini呼び出し -----
def gemini_json(prompt, max_retries=5):
    models = [MODEL, "gemini-2.5-flash-lite", "gemini-3.1-flash-lite"]
    cfg = None
    if genai_types:
        cfg = genai_types.GenerateContentConfig(max_output_tokens=4096, temperature=1.1)
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


# ----- 1スレを完全創作（Shorts向けに短く） -----
def generate_thread(avoid_summaries):
    avoid_text = ""
    if avoid_summaries:
        joined = "\n".join(f"- {s}" for s in avoid_summaries)
        avoid_text = f"\n\n【これらと設定・オチが被らない新作にすること】\n{joined}"
    prompt = f"""あなたは2ch（5ch）風のスカッとスレを書くプロの作家です。
YouTube Shorts（1分前後）向けの、短くテンポの良い「スカッとする」スレ風物語を
完全オリジナルで1つ創作してください。

条件:
・完全な創作。実在の事件・人物・企業・固有地名は使わない。実在スレの転載もしない。
・理不尽な相手や状況に、イッチ（スレ主）が最後に痛快に逆転・反撃する流れ。
・過度な暴力や違法な仕返しはNG。痛快でスッキリする結末に。
・Shorts向けに短く！冒頭2〜3レスで状況を一気に提示し、すぐ反撃→オチへ。
・レスは「イッチ」と「名無し」で構成。1レスは1〜2文、できるだけ短く。

以下のJSON形式のみで出力（前後に説明やマークダウン不要）:
{{
  "title": "スレタイ（28文字以内・【】やwwwなど2ch風の煽りOK）",
  "summary": "このスレの要約を1行で（被り防止ログ用・40文字以内）",
  "res": [
    {{"op": true,  "text": "イッチの最初のレス（状況を一気に）"}},
    {{"op": false, "text": "名無しの短い反応"}}
  ]
}}
※res は8〜12要素。各レスは短く（40文字以内目安）。{avoid_text}
"""
    data = gemini_json(prompt)
    if not data.get("res"):
        raise ValueError("resが空")
    return data


# ----- 読み上げ用にテキストを整える（表示はそのまま・音声だけ整形） -----
def _for_speech(text):
    t = text
    t = re.sub(r'[wWｗＷ]{2,}', '', t)
    t = re.sub(r'(?<=[ぁ-んァ-ヴ一-龯。、！？])[wWｗＷ]+', '', t)
    t = re.sub(r'[>＞]{2}\s*([0-9０-９]+)', r'レス\1', t)
    t = t.replace('ｗ', '').replace('Ｗ', '')
    return t


# ----- VOICEVOX音声生成（話者IDを指定） -----
def make_audio(text, filename, speaker):
    text = _for_speech(text)
    if not text.strip():
        AudioSegment.silent(duration=300).export(filename, format="mp3")
        return filename
    q = requests.post(f"{VOICEVOX_URL}/audio_query",
                      params={"text": text, "speaker": speaker}, timeout=60)
    query = q.json()
    query["speedScale"] = VOICE_SPEED
    query["prePhonemeLength"] = 0.1
    query["postPhonemeLength"] = 0.1
    s = requests.post(f"{VOICEVOX_URL}/synthesis",
                      params={"speaker": speaker},
                      data=json.dumps(query),
                      headers={"Content-Type": "application/json"}, timeout=120)
    tmp_wav = "tmp_" + filename.replace(".mp3", ".wav")
    with open(tmp_wav, "wb") as f:
        f.write(s.content)
    seg = AudioSegment.from_wav(tmp_wav)
    seg = seg + AudioSegment.silent(duration=150)
    seg.export(filename, format="mp3")
    os.remove(tmp_wav)
    return filename


def _fake_id():
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "ID:" + "".join(random.choice(chars) for _ in range(8))


# ----- レス1件の画面（縦型） -----
def make_res_clip(duration, thread_title, res_no, is_op, body):
    layers = [ColorClip(size=(W, H), color=BG_COLOR, duration=duration)]
    title_bar = ColorClip(size=(W, 230), color=TITLE_BG, duration=duration).set_position((0, 0))
    layers.append(title_bar)
    title_txt = TextClip(thread_title, font=FONT, fontsize=TITLE_FONTSIZE,
                         color=TITLE_COLOR, method="caption", align="center",
                         size=(W - 80, 230)).set_duration(duration).set_position((40, 0))
    layers.append(title_txt)
    name = f"{res_no} ：{'＞＞1（イッチ）' if is_op else '名無しさん'}"
    name_clip = TextClip(name, font=FONT, fontsize=NAME_FONTSIZE,
                         color=(NAME_COLOR_OP if is_op else NAME_COLOR),
                         method="label").set_duration(duration).set_position((60, 330))
    layers.append(name_clip)
    id_clip = TextClip(_fake_id(), font=FONT, fontsize=28, color="#888888",
                       method="label").set_duration(duration).set_position((60, 385))
    layers.append(id_clip)
    body_clip = TextClip(body, font=FONT, fontsize=RES_FONTSIZE,
                         color=BODY_COLOR, method="caption", align="West",
                         size=(W - 140, None), interline=16
                         ).set_duration(duration).set_position((70, 480))
    layers.append(body_clip)
    return CompositeVideoClip(layers, size=(W, H)).set_duration(duration)


def render_clip(clip, audio_file, out_path):
    narration = AudioFileClip(audio_file)
    dur = clip.duration
    if dur > narration.duration + 0.02:
        narration = CompositeAudioClip([narration]).set_duration(dur)
    clip = clip.set_audio(narration)
    clip.write_videofile(out_path, fps=FPS, codec="libx264",
                         audio_codec="aac", preset="ultrafast", logger=None)
    try:
        narration.close()
    except Exception:
        pass
    clip.close(); del clip; gc.collect()


def build_video(th):
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    title = th["title"]
    safe = title
    for ch in r'\/:*?"<>|':
        safe = safe.replace(ch, "")
    output_path = os.path.join(OUT_DIR, f"{safe.strip()[:60]}.mp4")

    clip_paths = []
    idx = 0

    # スレタイ読み上げ（イッチの声で）
    a = make_audio(title, f"a_{idx}.mp3", SPEAKER_OP)
    dur = AudioFileClip(a).duration + 0.5
    p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
    render_clip(make_res_clip(dur, title, "1", True, title), a, p)
    clip_paths.append(p); os.remove(a); idx += 1

    res_no = 1
    for r in th["res"]:
        res_no += 1
        body = r.get("text", "")
        if not body.strip():
            continue
        is_op = r.get("op", False)
        speaker = SPEAKER_OP if is_op else SPEAKER_NPC   # 話者を演じ分け
        print(f"  [レス{res_no} {'イッチ' if is_op else '名無し'}] {body[:20]}...")
        a = make_audio(body, f"a_{idx}.mp3", speaker)
        dur = AudioFileClip(a).duration + 0.3
        p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
        render_clip(make_res_clip(dur, title, str(res_no), is_op, body), a, p)
        clip_paths.append(p); os.remove(a); idx += 1

    print(f"  🔗 {len(clip_paths)}クリップを連結...")
    list_file = f"{TMP_DIR}/list.txt"
    with open(list_file, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.basename(cp)}'\n")
    master = f"{TMP_DIR}/master.mp4"
    os.system(f'cd {TMP_DIR} && ffmpeg -y -f concat -safe 0 -i list.txt '
              f'-c:v copy -c:a aac master.mp4 -loglevel error')

    if BGM_PATH and os.path.exists(BGM_PATH):
        os.system(
            f'ffmpeg -y -i "{master}" -stream_loop -1 -i "{BGM_PATH}" '
            f'-filter_complex "[1:a]volume={BGM_VOLUME}[b];'
            f'[0:a][b]amix=inputs=2:duration=first:dropout_transition=0[a]" '
            f'-map 0:v -map "[a]" -c:v copy -c:a aac "{output_path}" -loglevel error'
        )
    else:
        os.replace(master, output_path)

    for cp in clip_paths:
        if os.path.exists(cp):
            os.remove(cp)
    for f in [list_file, master]:
        if os.path.exists(f):
            os.remove(f)
    return output_path, title


def get_youtube():
    creds = Credentials(token=None, refresh_token=YT_REFRESH_TOKEN,
                        client_id=YT_CLIENT_ID, client_secret=YT_CLIENT_SECRET,
                        token_uri="https://oauth2.googleapis.com/token")
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(youtube, path, title):
    description = (
        "2ch風スカッとスレ（オリジナル創作）。\n\n"
        "VOICEVOX:四国めたん／ずんだもん\n\n#スカッと #2ch #スカッとする話 #shorts #Shorts"
    )
    body = {
        "snippet": {
            "title": (title + " #shorts")[:100],
            "description": description[:5000],
            "tags": ["スカッと", "2ch", "5ch", "スカッとする話", "Shorts", "スカッと系"],
            "categoryId": "24",
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
    for _ in range(timeout // 3):
        try:
            if requests.get(f"{VOICEVOX_URL}/version", timeout=5).ok:
                print("✅ VOICEVOXエンジン応答OK")
                return True
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("VOICEVOXエンジンが起動しませんでした")


def resolve_speakers():
    """エンジンの /speakers から「話者名＋スタイル名」でstyle idを引き直す。
    （IDがバージョンで変わっても名前で正しく解決するための保険）"""
    global SPEAKER_OP, SPEAKER_NPC
    try:
        sp = requests.get(f"{VOICEVOX_URL}/speakers", timeout=30).json()
    except Exception as e:
        print(f"  /speakers取得失敗（既定IDのまま進む）: {e}")
        return

    def style_id(speaker_name, style_name):
        for s in sp:
            if speaker_name in s.get("name", ""):
                styles = s.get("styles", [])
                # まずスタイル名が一致するものを探す
                for st in styles:
                    if style_name and style_name in st.get("name", ""):
                        return st["id"]
                # 見つからなければ先頭（ノーマル相当）
                if styles:
                    return styles[0]["id"]
        return None

    op = style_id(SPEAKER_OP_NAME, SPEAKER_OP_STYLE)
    npc = style_id(SPEAKER_NPC_NAME, SPEAKER_NPC_STYLE)
    if op is not None:
        SPEAKER_OP = op
    else:
        print(f"  ⚠️ 話者『{SPEAKER_OP_NAME}』が見つからず。既定ID {SPEAKER_OP} を使用")
    if npc is not None:
        SPEAKER_NPC = npc
    else:
        print(f"  ⚠️ 話者『{SPEAKER_NPC_NAME}』が見つからず。既定ID {SPEAKER_NPC} を使用")
    print(f"🎙 イッチ={SPEAKER_OP_NAME}/{SPEAKER_OP_STYLE}(id {SPEAKER_OP}) / "
          f"名無し={SPEAKER_NPC_NAME}/{SPEAKER_NPC_STYLE}(id {SPEAKER_NPC})")


def main():
    wait_voicevox()
    resolve_speakers()
    log = load_log()
    avoid = [e["summary"] for e in log][-AVOID_RECENT:]
    print("📝 スレを創作中（Shorts向け）...")
    th = generate_thread(avoid)
    print(f"   スレタイ：{th.get('title')}（{len(th.get('res', []))}レス）")

    path, title = build_video(th)
    print(f"🎬 生成完了：{path}")

    youtube = get_youtube()
    res = upload(youtube, path, title)
    print(f"✅ 投稿成功： https://www.youtube.com/watch?v={res['id']}")

    log.append({"title": th.get("title", ""), "summary": th.get("summary", "")})
    save_log(log)
    print(f"📝 ログ更新（計{len(log)}件）")


if __name__ == "__main__":
    main()
