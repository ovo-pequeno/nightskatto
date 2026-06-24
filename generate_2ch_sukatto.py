# =========================================================
# 2ch風スカッとスレ読み上げ（横型長尺）1本を生成してYouTubeへ自動投稿
# GitHub Actions（毎日cron）から実行する想定。
# 【完全創作】実在スレの転載はしない。Geminiが2ch風スレをオリジナル創作。
# Gemini → gTTS（単一ナレーション）→ MoviePy（レス風画面）→ YouTube API
# 横型1920x1080 / 1本に2〜3スレ / レス風UI・被り防止ログつき
# =========================================================
import os, re, json, time, gc, random
from google import genai
try:
    from google.genai import types as genai_types
except Exception:
    genai_types = None
from gtts import gTTS
from pydub import AudioSegment
from moviepy.editor import (
    ColorClip, ImageClip, TextClip, CompositeVideoClip,
    AudioFileClip, CompositeAudioClip
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

VOICE_SPEED  = 1.3
THREADS_PER_VIDEO = 3      # 1本に詰めるスレ数
OUT_DIR  = "out_2ch"
TMP_DIR  = "tmp_2ch"
LOG_PATH = "used_log_2ch.json"
AVOID_RECENT = 30

BGM_PATH = "assets/bgm.mp3" if os.path.exists("assets/bgm.mp3") else None
BGM_VOLUME = 0.08

client = genai.Client(api_key=GEMINI_API_KEY)

W, H = 1920, 1080
FPS = 10

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"   # 2ch風はゴシック
if not os.path.exists(FONT):
    FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"

# 2ch風の配色
BG_COLOR      = (239, 239, 239)   # おなじみの薄いグレー背景
TITLE_BG      = (204, 102, 0)     # スレタイ帯（オレンジ系）
TITLE_COLOR   = "white"
NAME_COLOR    = "#008800"         # 「名無しさん」の緑
NAME_COLOR_OP = "#CC0000"         # イッチ（>>1）は赤
BODY_COLOR    = "#222222"
RES_FONTSIZE  = 46
NAME_FONTSIZE = 30
TITLE_FONTSIZE = 52


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
        cfg = genai_types.GenerateContentConfig(max_output_tokens=8192, temperature=1.1)
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
                wait = 20 * (attempt + 1)
                print(f"  Gemini混雑中… {wait}秒待って再試行 ({attempt+1}/{max_retries})")
                time.sleep(wait)
            elif attempt < max_retries - 1:
                print(f"  生成失敗（{e}）。再試行")
                time.sleep(5)
            else:
                raise


# ----- 1スレを完全創作 -----
def generate_thread(avoid_summaries):
    avoid_text = ""
    if avoid_summaries:
        joined = "\n".join(f"- {s}" for s in avoid_summaries)
        avoid_text = f"\n\n【これらと設定・オチが被らない新作にすること】\n{joined}"
    prompt = f"""あなたは2ch（5ch）風のスカッとスレを書くプロの作家です。
完全オリジナルの「スカッとする」スレ風物語を1つ創作してください。

条件:
・完全な創作。実在の事件・人物・企業・固有地名は使わない。実在スレの転載もしない。
・理不尽な相手や状況に、イッチ（スレ主）が最後に痛快に逆転・反撃する流れ。
・過度な暴力や違法な仕返しはNG。あくまで痛快でスッキリする結末に。
・2chスレ風の口語・ノリ（「なんJ」ほど砕けすぎず読みやすく）。
・レスは「イッチ」（スレ主＝スカッと体験を語る本人）と「名無し」（合いの手・反応）で構成。
・全体で15〜25レス程度。1レスは1〜3文、長すぎないように。

以下のJSON形式のみで出力（前後に説明やマークダウン不要）:
{{
  "title": "スレタイ（30文字以内・【】や www など2ch風の煽りOK）",
  "summary": "このスレの要約を1行で（被り防止ログ用・40文字以内）",
  "res": [
    {{"op": true,  "text": "イッチの最初のレス（状況説明）"}},
    {{"op": false, "text": "名無しの反応"}},
    {{"op": true,  "text": "イッチの続き"}}
  ]
}}
※res は15〜25要素。opはイッチの発言ならtrue、名無しならfalse。{avoid_text}
"""
    data = gemini_json(prompt)
    if not data.get("res"):
        raise ValueError("resが空")
    return data


# ----- gTTS音声 -----
def make_audio(text, filename):
    if not re.search(r'[ぁ-んァ-ヴ一-龯a-zA-Z0-9０-９]', text):
        AudioSegment.silent(duration=400).export(filename, format="mp3")
        return filename
    tmp = "tmp_" + filename
    gTTS(text=text, lang="ja", slow=False).save(tmp)
    seg = AudioSegment.from_mp3(tmp)
    if VOICE_SPEED and VOICE_SPEED != 1.0:
        seg = seg.speedup(playback_speed=VOICE_SPEED)
    seg = seg + AudioSegment.silent(duration=250)
    seg.export(filename, format="mp3")
    os.remove(tmp)
    return filename


# ----- テキスト折り返し（指定文字数で改行） -----
def wrap(text, n):
    out, line = [], ""
    for ch in text:
        line += ch
        if ch == "\n":
            out.append(line.rstrip("\n")); line = ""
        elif len(line) >= n:
            out.append(line); line = ""
    if line:
        out.append(line)
    return "\n".join(out)


# ----- レス1件の画面（スレタイ固定＋レス番＋名前＋本文） -----
def make_res_clip(duration, thread_title, res_no, is_op, body):
    layers = []
    layers.append(ColorClip(size=(W, H), color=BG_COLOR, duration=duration))

    # スレタイ帯（上部固定）
    title_bar = ColorClip(size=(W, 110), color=TITLE_BG, duration=duration).set_position((0, 0))
    layers.append(title_bar)
    title_txt = TextClip(thread_title, font=FONT, fontsize=TITLE_FONTSIZE,
                         color=TITLE_COLOR, method="caption", align="West",
                         size=(W - 80, 110)).set_duration(duration).set_position((40, 0))
    layers.append(title_txt)

    # 名前行（レス番 ：名無しさん／イッチは赤）
    name = f"{res_no} ：{'＞＞1（イッチ）' if is_op else '名無しさん'}　{_fake_id()}"
    name_clip = TextClip(name, font=FONT, fontsize=NAME_FONTSIZE,
                         color=(NAME_COLOR_OP if is_op else NAME_COLOR),
                         method="label").set_duration(duration).set_position((60, 170))
    layers.append(name_clip)

    # 本文（左寄せ・大きめ）
    body_clip = TextClip(body, font=FONT, fontsize=RES_FONTSIZE,
                         color=BODY_COLOR, method="caption", align="West",
                         size=(W - 160, None), interline=14
                         ).set_duration(duration).set_position((80, 250))
    layers.append(body_clip)

    return CompositeVideoClip(layers, size=(W, H)).set_duration(duration)


def _fake_id():
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "ID:" + "".join(random.choice(chars) for _ in range(8))


# ----- スレ間のタイトルカード -----
def make_card_clip(duration, text):
    layers = [ColorClip(size=(W, H), color=(20, 20, 24), duration=duration)]
    t = TextClip(text, font=FONT, fontsize=72, color="white",
                 method="caption", align="center", size=(W - 300, None)
                 ).set_duration(duration).set_position(("center", "center"))
    layers.append(t)
    return CompositeVideoClip(layers, size=(W, H)).set_duration(duration)


# ----- 1クリップ書き出し（メモリ解放つき） -----
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


# ----- 動画を組み立てる -----
def build_video(threads):
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    main_title = threads[0]["title"]
    safe = main_title
    for ch in r'\/:*?"<>|':
        safe = safe.replace(ch, "")
    output_path = os.path.join(OUT_DIR, f"{safe.strip()[:60]}.mp4")

    clip_paths = []
    idx = 0

    for ti, th in enumerate(threads):
        # スレ間カード（2スレ目以降）
        if ti > 0:
            card_text = f"次のスレ\n\n{th['title']}"
            a = make_audio("次のスレいきます。", f"a_{idx}.mp3")
            dur = AudioFileClip(a).duration + 1.0
            p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
            render_clip(make_card_clip(dur, card_text), a, p)
            clip_paths.append(p); os.remove(a); idx += 1

        # スレタイ読み上げ（最初のスレor各スレ冒頭）
        a = make_audio(th["title"], f"a_{idx}.mp3")
        dur = AudioFileClip(a).duration + 0.6
        p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
        render_clip(make_res_clip(dur, th["title"], "1", True, th["title"]), a, p)
        clip_paths.append(p); os.remove(a); idx += 1

        # レス
        res_no = 1
        for r in th["res"]:
            res_no += 1
            body = r.get("text", "")
            if not body.strip():
                continue
            print(f"  [スレ{ti+1} レス{res_no}] {body[:24]}...")
            a = make_audio(body, f"a_{idx}.mp3")
            dur = AudioFileClip(a).duration + 0.35
            p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
            render_clip(make_res_clip(dur, th["title"], str(res_no), r.get("op", False), body), a, p)
            clip_paths.append(p); os.remove(a); idx += 1

    # エンディング
    a = make_audio("ご視聴ありがとうございました。チャンネル登録お願いします。", f"a_{idx}.mp3")
    dur = AudioFileClip(a).duration + 0.5
    p = f"{TMP_DIR}/clip_{idx:04d}.mp4"
    render_clip(make_card_clip(dur, "ご視聴ありがとうございました\nチャンネル登録お願いします"), a, p)
    clip_paths.append(p); os.remove(a); idx += 1

    # 連結（映像コピー・音声再エンコード）
    print(f"  🔗 {len(clip_paths)}クリップを連結...")
    list_file = f"{TMP_DIR}/list.txt"
    with open(list_file, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{os.path.basename(cp)}'\n")
    master = f"{TMP_DIR}/master.mp4"
    os.system(f'cd {TMP_DIR} && ffmpeg -y -f concat -safe 0 -i list.txt '
              f'-c:v copy -c:a aac master.mp4 -loglevel error')

    if BGM_PATH and os.path.exists(BGM_PATH):
        print("  🎵 BGMを合成...")
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
    return output_path, main_title


# ----- YouTube -----
def get_youtube():
    creds = Credentials(
        token=None, refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID, client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(youtube, path, title, threads):
    titles = "／".join(t["title"] for t in threads)
    description = (
        "2ch風スカッとスレの読み上げ（すべてオリジナル創作です）。\n"
        f"収録スレ：{titles}\n\n#スカッと #2ch #スカッとする話 #作業用 #ゆっくり"
    )
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": ["スカッと", "2ch", "5ch", "スカッとする話", "スカッと系", "作業用"],
            "categoryId": "24",
            "defaultLanguage": "ja",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, chunksize=10 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    retry = 0
    while response is None:
        try:
            status, response = req.next_chunk()
            if status:
                print(f"  ⏫ {int(status.progress()*100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                retry += 1
                if retry > 10:
                    raise
                time.sleep(min(2 ** retry, 60))
            else:
                raise
    return response


def main():
    log = load_log()
    avoid = [e["summary"] for e in log][-AVOID_RECENT:]

    threads = []
    for i in range(THREADS_PER_VIDEO):
        print(f"📝 スレ{i+1}/{THREADS_PER_VIDEO} を創作中...")
        th = generate_thread(avoid + [t["summary"] for t in threads if t.get("summary")])
        threads.append(th)
        print(f"   スレタイ：{th.get('title')}（{len(th.get('res', []))}レス）")
        time.sleep(2)

    path, title = build_video(threads)
    print(f"🎬 生成完了：{path}")

    youtube = get_youtube()
    res = upload(youtube, path, title, threads)
    print(f"✅ 投稿成功： https://www.youtube.com/watch?v={res['id']}")

    for th in threads:
        log.append({"title": th.get("title", ""), "summary": th.get("summary", "")})
    save_log(log)
    print(f"📝 ログ更新（計{len(log)}件）")


if __name__ == "__main__":
    main()
